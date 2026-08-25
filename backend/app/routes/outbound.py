import csv
import html
import io
import json
import re
import uuid
from datetime import date, datetime, timezone
from typing import Annotated
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import record_audit
from app.config import get_settings
from app.database import get_session
from app.llm import get_llm_client
from app.localdata import (
    BUILDING_PERMIT_URL,
    BUILDING_TITLE_URL,
    SBIZ_STORE_URL,
    apply_recent_major_repair,
    building_age_score,
    building_query_from_sbiz,
    parse_building_permits,
    parse_building_title,
    parse_sbiz_rows,
)
from app.models import (
    Account,
    Lead,
    Opportunity,
    OpportunityStageHistory,
    OutboundDraft,
    Product,
    Staff,
)
from app.product_pricing import trusted_business_price
from app.prompts import outbound_prompt
from app.schemas import (
    CsvTextRequest,
    DraftEditRequest,
    LeadConversionRequest,
    LeadStageRequest,
    LeadUpdateRequest,
    ManualContactRequest,
    OpportunityResponse,
)
from app.security import CurrentStaff, ManagerStaff

router = APIRouter(prefix="/api/outbound", tags=["outbound"])
Session = Annotated[AsyncSession, Depends(get_session)]
TERMINAL_LEAD_STAGES = {"converted", "dropped"}
CSV_ROW_LIMIT = 500
LEAD_STAGE_TRANSITIONS = {
    "discovered": {"draft_generated", "dropped"},
    "draft_generated": {"approved", "dropped"},
    "approved": {"contacted", "follow_up_due", "dropped"},
    "contacted": {"draft_generated", "follow_up_due", "dropped"},
    "follow_up_due": {"draft_generated", "contacted", "dropped"},
}


class DraftResult(BaseModel):
    subject: str = Field(min_length=1, max_length=280)
    body: str = Field(min_length=1, max_length=20_000)


class SbizSyncRequest(BaseModel):
    region_code: str = Field(pattern=r"^\d{2}$")
    page: int = Field(default=1, ge=1, le=10_000)
    rows: int = Field(default=100, ge=1, le=100)


def renovation_evidence(lead: Lead) -> dict[str, object]:
    evidence = lead.raw_data.get("renovation_evidence", {})
    return evidence if isinstance(evidence, dict) else {}


def parse_naver_mentions(payload: dict[str, object], source: str) -> list[dict[str, str | None]]:
    items = payload.get("items", [])
    if not isinstance(items, list):
        raise TypeError("네이버 검색 결과는 목록이어야 합니다.")
    mentions = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = html.unescape(re.sub(r"<[^>]+>", "", str(item.get("title") or ""))).strip()
        description = html.unescape(
            re.sub(r"<[^>]+>", "", str(item.get("description") or ""))
        ).strip()
        link = str(item.get("link") or "").strip()
        if not title or not any(
            keyword in f"{title} {description}"
            for keyword in ("리모델링", "리뉴얼", "새단장", "재오픈")
        ):
            continue
        if urlsplit(link).scheme not in {"http", "https"}:
            continue
        postdate = str(item.get("postdate") or "").strip()
        published_at = (
            f"{postdate[:4]}-{postdate[4:6]}-{postdate[6:]}"
            if len(postdate) == 8 and postdate.isdigit()
            else None
        )
        mentions.append(
            {
                "source": source,
                "title": title,
                "description": description,
                "link": link,
                "published_at": published_at,
            }
        )
    return mentions


async def search_renovation_mentions(
    client: httpx.AsyncClient, name: str, address: str | None, client_id: str, client_secret: str
) -> list[dict[str, str | None]]:
    headers = {"X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": client_secret}
    query = f"{name} {address or ''} 리모델링 리뉴얼 새단장 재오픈"
    mentions: list[dict[str, str | None]] = []
    for endpoint, source in (("blog.json", "네이버 블로그"), ("webkr.json", "네이버 웹문서")):
        response = await client.get(
            f"https://openapi.naver.com/v1/search/{endpoint}",
            params={
                "query": query,
                "display": 5,
                **({"sort": "date"} if source.endswith("블로그") else {}),
            },
            headers=headers,
        )
        response.raise_for_status()
        mentions.extend(parse_naver_mentions(response.json(), source))
    return list({mention["link"]: mention for mention in mentions}.values())[:5]


def csv_safe(value: object) -> object:
    dangerous = ("=", "+", "-", "@", "\t", "\r")
    return f"'{value}" if isinstance(value, str) and value.startswith(dangerous) else value


def require_active_lead(lead: Lead) -> None:
    if lead.pipeline_stage in TERMINAL_LEAD_STAGES:
        raise HTTPException(status_code=409, detail="종결된 리드는 변경할 수 없습니다.")


def require_lead_access(lead: Lead, staff: Staff) -> None:
    if staff.role == "rep" and lead.assignee_id != staff.id:
        raise HTTPException(status_code=403, detail="담당 리드만 처리할 수 있습니다.")


def draft_payload(draft: OutboundDraft) -> dict[str, object]:
    return {
        "id": draft.id,
        "lead_id": draft.lead_id,
        "sequence_step": draft.sequence_step,
        "subject": draft.subject,
        "body": draft.body,
        "generated_at": draft.generated_at,
        "reviewed": draft.reviewed_by is not None,
        "send_mode": draft.send_mode,
        "sent_at": draft.sent_at,
    }


@router.post("/leads/sync-sbiz")
async def sync_sbiz_leads(
    payload: SbizSyncRequest, session: Session, staff: ManagerStaff
) -> dict[str, int]:
    service_key = get_settings().data_go_kr_service_key
    if not service_key:
        raise HTTPException(status_code=503, detail="DATA_GO_KR_SERVICE_KEY가 설정되지 않았습니다.")
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                SBIZ_STORE_URL,
                params={
                    "serviceKey": service_key,
                    "pageNo": payload.page,
                    "numOfRows": payload.rows,
                    "divId": "ctprvnCd",
                    "key": payload.region_code,
                    "type": "json",
                },
            )
            response.raise_for_status()
            rows, total_count = parse_sbiz_rows(response.json())
    except (httpx.HTTPError, TypeError, ValueError) as error:
        raise HTTPException(
            status_code=502, detail="상가정보 API를 불러오지 못했습니다."
        ) from error

    external_ids = [row["external_id"] for row in rows]
    existing = {
        lead.external_id: lead
        for lead in (
            await session.scalars(
                select(Lead).where(Lead.external_id.in_(external_ids)).with_for_update()
            )
        ).all()
    }
    created = 0
    for row in rows:
        lead = existing.get(row["external_id"])
        if lead:
            for field, value in row.items():
                setattr(lead, field, value)
            continue
        session.add(Lead(**row))
        created += 1
    try:
        record_audit(
            session,
            staff,
            "lead.sbiz_sync",
            "lead",
            "bulk",
            {"region_code": payload.region_code, "page": payload.page, "fetched": len(rows)},
        )
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(
            status_code=409, detail="동시에 같은 공공데이터 리드가 저장되었습니다."
        ) from error
    return {
        "fetched_count": len(rows),
        "created_count": created,
        "updated_count": len(rows) - created,
        "total_count": total_count,
    }


@router.post("/leads/{lead_id}/enrich-building")
async def enrich_lead_building(
    lead_id: int, session: Session, staff: ManagerStaff
) -> dict[str, object]:
    lead = await session.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="잠재고객을 찾을 수 없습니다.")
    if lead.source != "sbiz":
        raise HTTPException(
            status_code=422, detail="상가정보에서 가져온 리드만 보강할 수 있습니다."
        )
    try:
        query = building_query_from_sbiz(lead.raw_data)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    settings = get_settings()
    service_key = settings.effective_building_hub_api_key
    if not service_key:
        raise HTTPException(status_code=503, detail="BUILDING_HUB_API_KEY가 설정되지 않았습니다.")
    permits: list[dict[str, object]] = []
    permit_status = "failed"
    mentions: list[dict[str, str | None]] = []
    naver_status = "not_configured"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                BUILDING_TITLE_URL,
                params={"serviceKey": service_key, **query},
            )
            response.raise_for_status()
            building = parse_building_title(response.json())
            try:
                permit_response = await client.get(
                    BUILDING_PERMIT_URL,
                    params={"serviceKey": service_key, **query},
                )
                permit_response.raise_for_status()
                permits = parse_building_permits(permit_response.json())
                permit_status = "success"
            except (httpx.HTTPError, TypeError, ValueError):
                permit_status = "failed"
            naver_id = getattr(settings, "naver_client_id", None)
            naver_secret = getattr(settings, "naver_client_secret", None)
            if naver_id and naver_secret:
                try:
                    mentions = await search_renovation_mentions(
                        client, lead.name, lead.address, naver_id, naver_secret
                    )
                    naver_status = "success"
                except (httpx.HTTPError, TypeError, ValueError):
                    naver_status = "failed"
    except (httpx.HTTPError, TypeError, ValueError) as error:
        raise HTTPException(
            status_code=502, detail="건축물대장 정보를 불러오지 못했습니다."
        ) from error

    approval_date = date.fromisoformat(str(building["approval_date"]))
    base_score, reason = building_age_score(approval_date)
    if permit_status == "success":
        score, permit_reason = apply_recent_major_repair(base_score, permits)
    else:
        score = base_score
        permit_reason = (
            "건축인허가 정보를 확인하지 못해 공식 대수선 이력은 점수에 반영하지 않았습니다."
        )
    if naver_status == "success" and mentions:
        mention_titles = ", ".join(f"‘{mention['title']}’" for mention in mentions[:3])
        online_reason = (
            f"리모델링 관련 공개 검색 후보 {len(mentions)}건을 찾았습니다: {mention_titles}. "
            "동일 상호나 광고 글일 수 있어 점수에는 반영하지 않고 담당자 확인 자료로만 표시합니다."
        )
    elif naver_status == "success":
        online_reason = (
            "리모델링·리뉴얼 관련 공개 검색 결과를 찾지 못했습니다. "
            "검색 결과가 없다는 사실이 리모델링을 하지 않았다는 뜻은 아닙니다."
        )
    elif naver_status == "not_configured":
        online_reason = "네이버 검색 API가 설정되지 않아 온라인 리뉴얼 정황을 확인하지 않았습니다."
    else:
        online_reason = (
            "네이버 검색을 완료하지 못해 온라인 리뉴얼 정황은 점수에 반영하지 않았습니다."
        )
    evidence = {
        "permit_status": permit_status,
        "official_permits": [
            {key: permit.get(key) for key in ("kind", "date", "date_basis", "building_name")}
            for permit in permits
        ],
        "naver_status": naver_status,
        "online_mentions": mentions,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    lead = await session.scalar(select(Lead).where(Lead.id == lead_id).with_for_update())
    if not lead:
        raise HTTPException(status_code=404, detail="잠재고객을 찾을 수 없습니다.")
    if lead.source != "sbiz":
        raise HTTPException(
            status_code=422, detail="상가정보에서 가져온 리드만 보강할 수 있습니다."
        )
    raw_data = dict(lead.raw_data)
    raw_data["building_register"] = building
    raw_data["renovation_evidence"] = evidence
    reasoning = dict(lead.lead_score_reasoning)
    reasoning.pop("business_type", None)
    reasoning["source_data"] = (
        "상가정보로 업체 존재와 업종을 확인했으며 업종은 노후도 점수에 반영하지 않았습니다."
    )
    reasoning["building_age"] = reason
    reasoning["official_permit"] = permit_reason
    reasoning["online_renovation"] = online_reason
    lead.raw_data = raw_data
    lead.lead_score = score
    lead.lead_score_reasoning = reasoning
    lead.lead_scoring_version = "v4"
    record_audit(session, staff, "lead.building_enrich", "lead", str(lead.id), {"score": score})
    await session.commit()
    return {
        "id": lead.id,
        "lead_score": lead.lead_score,
        "reasoning": lead.lead_score_reasoning,
        "evidence": evidence,
    }


@router.get("/leads")
async def list_leads(
    session: Session,
    staff: CurrentStaff,
    q: str | None = None,
    pipeline_stage: Annotated[str | None, Query(alias="status", max_length=30)] = None,
    assignee_id: uuid.UUID | None = None,
    work_queue: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict[str, object]]:
    statement = select(Lead, Staff.name).outerjoin(Staff, Lead.assignee_id == Staff.id)
    if staff.role == "rep":
        statement = statement.where(Lead.assignee_id == staff.id)
    elif assignee_id:
        statement = statement.where(Lead.assignee_id == assignee_id)
    if q:
        statement = statement.where(or_(Lead.name.ilike(f"%{q}%"), Lead.address.ilike(f"%{q}%")))
    if pipeline_stage:
        statement = statement.where(Lead.pipeline_stage == pipeline_stage)
    if work_queue:
        statement = statement.where(
            Lead.next_action_at.is_not(None),
            Lead.pipeline_stage.not_in(TERMINAL_LEAD_STAGES),
        )
        ordering = (Lead.next_action_at.asc(), Lead.lead_score.desc())
    else:
        ordering = (Lead.lead_score.desc(),)
    leads = (await session.execute(statement.order_by(*ordering).limit(limit).offset(offset))).all()
    return [
        {
            "id": lead.id,
            "name": lead.name,
            "address": lead.address,
            "business_type": lead.business_type,
            "assignee_id": lead.assignee_id,
            "assignee_name": assignee_name,
            "contact_name": lead.contact_name,
            "contact_phone": lead.contact_phone,
            "contact_email": lead.contact_email,
            "next_action_at": lead.next_action_at,
            "source": lead.source,
            "lead_score": lead.lead_score,
            "reasoning": lead.lead_score_reasoning,
            "evidence": renovation_evidence(lead),
            "pipeline_stage": lead.pipeline_stage,
        }
        for lead, assignee_name in leads
    ]


@router.get("/leads/export.csv")
async def export_leads(session: Session, staff: CurrentStaff) -> Response:
    output = io.StringIO()
    fields = [
        "name",
        "address",
        "license_date",
        "years_in_business",
        "business_type",
        "contact_name",
        "contact_phone",
        "contact_email",
        "source",
        "lead_score",
        "lead_score_reasoning",
        "pipeline_stage",
    ]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    statement = select(Lead).order_by(Lead.id)
    if staff.role == "rep":
        statement = statement.where(Lead.assignee_id == staff.id)
    for lead in (await session.scalars(statement)).all():
        row = {
            "name": lead.name,
            "address": lead.address or "",
            "license_date": lead.license_date.isoformat() if lead.license_date else "",
            "years_in_business": lead.years_in_business
            if lead.years_in_business is not None
            else "",
            "business_type": lead.business_type or "",
            "contact_name": lead.contact_name or "",
            "contact_phone": lead.contact_phone or "",
            "contact_email": lead.contact_email or "",
            "source": lead.source,
            "lead_score": lead.lead_score,
            "lead_score_reasoning": json.dumps(lead.lead_score_reasoning, ensure_ascii=False),
            "pipeline_stage": lead.pipeline_stage,
        }
        writer.writerow({key: csv_safe(value) for key, value in row.items()})
    return Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="leads.csv"'},
    )


@router.post("/leads/import")
async def import_leads(
    payload: CsvTextRequest, session: Session, staff: ManagerStaff
) -> dict[str, object]:
    try:
        reader = csv.DictReader(io.StringIO(payload.csv_text.lstrip("\ufeff")))
        rows = list(reader)
    except csv.Error as error:
        return {"imported_count": 0, "errors": [{"row": 0, "error": str(error)}]}
    required = {"name", "lead_score"}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        return {
            "imported_count": 0,
            "errors": [{"row": 1, "error": "name, lead_score 헤더가 필요합니다."}],
        }
    if len(rows) > CSV_ROW_LIMIT:
        return {
            "imported_count": 0,
            "errors": [{"row": 0, "error": f"최대 {CSV_ROW_LIMIT}행까지 가져올 수 있습니다."}],
        }
    errors: list[dict[str, object]] = []
    leads: list[Lead] = []
    for row_number, row in enumerate(rows, start=2):
        try:
            name = (row.get("name") or "").strip()
            if not name or len(name) > 200:
                raise ValueError("name은 1~200자여야 합니다.")
            score = int(row.get("lead_score") or "")
            if not 0 <= score <= 100:
                raise ValueError("lead_score는 0~100이어야 합니다.")
            license_date = (
                date.fromisoformat(row["license_date"]) if row.get("license_date") else None
            )
            years = int(row["years_in_business"]) if row.get("years_in_business") else None
            if years is not None and years < 0:
                raise ValueError("years_in_business는 0 이상이어야 합니다.")
            address = (row.get("address") or "").strip()
            business_type = (row.get("business_type") or "").strip()
            contact = LeadUpdateRequest(
                contact_name=(row.get("contact_name") or "").strip() or None,
                contact_phone=(row.get("contact_phone") or "").strip() or None,
                contact_email=(row.get("contact_email") or "").strip() or None,
            )
            contact_name = contact.contact_name or ""
            contact_phone = contact.contact_phone or ""
            contact_email = str(contact.contact_email or "")
            source = (row.get("source") or "csv").strip() or "csv"
            stage = (row.get("pipeline_stage") or "discovered").strip()
            if (
                len(address) > 500
                or len(business_type) > 100
                or len(source) > 30
                or len(contact_name) > 100
                or len(contact_phone) > 30
                or len(contact_email) > 320
            ):
                raise ValueError("address, business_type 또는 source가 너무 깁니다.")
            if stage != "discovered":
                raise ValueError("가져온 리드는 discovered 단계로만 시작할 수 있습니다.")
            reasoning = json.loads(row.get("lead_score_reasoning") or "{}")
            if not isinstance(reasoning, dict):
                raise TypeError("lead_score_reasoning은 JSON 객체여야 합니다.")
            leads.append(
                Lead(
                    name=name,
                    address=address or None,
                    license_date=license_date,
                    years_in_business=years,
                    business_type=business_type or None,
                    contact_name=contact_name or None,
                    contact_phone=contact_phone or None,
                    contact_email=contact_email or None,
                    source=source,
                    raw_data={},
                    lead_score=score,
                    lead_score_reasoning=reasoning,
                    pipeline_stage=stage,
                )
            )
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            errors.append({"row": row_number, "error": str(error)})
    if errors:
        return {"imported_count": 0, "errors": errors}
    session.add_all(leads)
    record_audit(
        session,
        staff,
        "lead.csv_import",
        "lead",
        "bulk",
        {"count": len(leads)},
    )
    await session.commit()
    return {"imported_count": len(leads), "errors": []}


@router.get("/leads/{lead_id}/drafts")
async def list_drafts(
    lead_id: int, session: Session, staff: CurrentStaff
) -> list[dict[str, object]]:
    lead = await session.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="리드를 찾을 수 없습니다.")
    require_lead_access(lead, staff)
    drafts = (
        await session.scalars(
            select(OutboundDraft)
            .where(OutboundDraft.lead_id == lead_id)
            .order_by(OutboundDraft.sequence_step.desc())
        )
    ).all()
    return [draft_payload(draft) for draft in drafts]


@router.patch("/leads/{lead_id}")
async def update_lead(
    lead_id: int, payload: LeadUpdateRequest, session: Session, staff: CurrentStaff
) -> dict[str, object]:
    lead = await session.scalar(select(Lead).where(Lead.id == lead_id).with_for_update())
    if not lead:
        raise HTTPException(status_code=404, detail="리드를 찾을 수 없습니다.")
    require_lead_access(lead, staff)
    require_active_lead(lead)
    changes = payload.model_dump(exclude_unset=True)
    if "assignee_id" in changes:
        if staff.role == "rep":
            raise HTTPException(status_code=403, detail="관리자만 담당자를 변경할 수 있습니다.")
        assignee_id = changes["assignee_id"]
        if assignee_id and not await session.scalar(
            select(Staff.id).where(
                Staff.id == assignee_id, Staff.role == "rep", Staff.is_active.is_(True)
            )
        ):
            raise HTTPException(status_code=404, detail="활성 영업 담당자를 찾을 수 없습니다.")
    if (
        changes.get("next_action_at") is None
        and "next_action_at" in changes
        and lead.pipeline_stage == "follow_up_due"
    ):
        raise HTTPException(status_code=422, detail="후속 필요 단계에는 다음 행동일이 필요합니다.")
    if (
        changes.get("next_action_at") is not None
        and lead.pipeline_stage != "follow_up_due"
        and "follow_up_due" not in LEAD_STAGE_TRANSITIONS[lead.pipeline_stage]
    ):
        raise HTTPException(
            status_code=409,
            detail="현재 단계에서는 후속 일정을 지정할 수 없습니다.",
        )
    previous_assignee = lead.assignee_id
    for field, value in changes.items():
        setattr(lead, field, value)
    if "next_action_at" in changes and changes["next_action_at"] is not None:
        lead.pipeline_stage = "follow_up_due"
    record_audit(
        session,
        staff,
        "lead.update",
        "lead",
        lead.id,
        {
            "fields": sorted(changes),
            "previous_assignee_id": str(previous_assignee) if previous_assignee else None,
        },
    )
    await session.commit()
    return {"id": lead.id, "pipeline_stage": lead.pipeline_stage}


@router.put("/leads/{lead_id}/stage")
async def change_stage(
    lead_id: int, payload: LeadStageRequest, session: Session, staff: CurrentStaff
) -> dict[str, object]:
    lead = await session.scalar(select(Lead).where(Lead.id == lead_id).with_for_update())
    if not lead:
        raise HTTPException(status_code=404, detail="리드를 찾을 수 없습니다.")
    require_lead_access(lead, staff)
    if payload.pipeline_stage == "converted":
        raise HTTPException(status_code=422, detail="리드 전환 API를 사용해주세요.")
    if payload.pipeline_stage == "approved":
        raise HTTPException(status_code=422, detail="초안 검토 API를 사용해주세요.")
    if payload.pipeline_stage == "follow_up_due" and lead.next_action_at is None:
        raise HTTPException(status_code=422, detail="다음 행동일을 먼저 지정해주세요.")
    previous = lead.pipeline_stage
    require_active_lead(lead)
    if (
        payload.pipeline_stage != previous
        and payload.pipeline_stage not in LEAD_STAGE_TRANSITIONS[previous]
    ):
        raise HTTPException(status_code=409, detail="허용되지 않는 리드 단계 변경입니다.")
    lead.pipeline_stage = payload.pipeline_stage
    if payload.pipeline_stage != "follow_up_due":
        lead.next_action_at = None
    record_audit(
        session,
        staff,
        "lead.stage_change",
        "lead",
        lead.id,
        {"from": previous, "to": lead.pipeline_stage},
    )
    await session.commit()
    return {"id": lead.id, "pipeline_stage": lead.pipeline_stage}


@router.post(
    "/leads/{lead_id}/convert",
    response_model=OpportunityResponse,
    status_code=201,
)
async def convert_lead(
    lead_id: int,
    payload: LeadConversionRequest,
    session: Session,
    staff: CurrentStaff,
) -> Opportunity:
    lead = await session.scalar(select(Lead).where(Lead.id == lead_id).with_for_update())
    if not lead:
        raise HTTPException(status_code=404, detail="리드를 찾을 수 없습니다.")
    require_lead_access(lead, staff)
    if staff.role == "rep" and payload.assignee_id != staff.id:
        raise HTTPException(status_code=403, detail="자신에게 배정된 영업기회만 만들 수 있습니다.")
    if lead.pipeline_stage in TERMINAL_LEAD_STAGES or await session.scalar(
        select(Opportunity.id).where(Opportunity.lead_id == lead.id)
    ):
        raise HTTPException(status_code=409, detail="이미 전환된 리드입니다.")
    assignee = await session.scalar(
        select(Staff).where(
            Staff.id == payload.assignee_id,
            Staff.is_active.is_(True),
            Staff.role == "rep",
        )
    )
    if not assignee:
        raise HTTPException(status_code=404, detail="담당자를 찾을 수 없습니다.")
    account = await session.scalar(select(Account).where(Account.phone == payload.phone))
    if account and account.deleted_at is not None:
        raise HTTPException(status_code=409, detail="삭제된 고객사를 먼저 복구해주세요.")
    if not account:
        account = Account(
            name=payload.account_name or lead.name,
            phone=payload.phone,
            attributes={
                "business_type": lead.business_type,
                "address": lead.address,
            },
        )
        session.add(account)
        await session.flush()
    opportunity = Opportunity(
        account_id=account.id,
        lead_id=lead.id,
        assignee_id=payload.assignee_id,
        title=payload.opportunity_title,
        amount=payload.amount,
        stage="qualify",
        probability=10,
    )
    session.add(opportunity)
    try:
        await session.flush()
        session.add(
            OpportunityStageHistory(
                opportunity_id=opportunity.id, stage="qualify", changed_by=staff.id
            )
        )
        lead.pipeline_stage = "converted"
        record_audit(
            session,
            staff,
            "lead.convert",
            "lead",
            lead.id,
            {"account_id": account.id, "opportunity_id": opportunity.id},
        )
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(status_code=409, detail="리드 전환 중 중복이 발생했습니다.") from error
    await session.refresh(opportunity)
    return opportunity


@router.post("/leads/{lead_id}/drafts")
async def generate_draft(lead_id: int, session: Session, staff: CurrentStaff) -> dict[str, object]:
    lead = await session.scalar(select(Lead).where(Lead.id == lead_id).with_for_update())
    if not lead:
        raise HTTPException(status_code=404, detail="리드를 찾을 수 없습니다.")
    require_lead_access(lead, staff)
    require_active_lead(lead)
    sender_name = staff.name
    if lead.assignee_id:
        sender_name = (
            await session.scalar(select(Staff.name).where(Staff.id == lead.assignee_id))
            or staff.name
        )
    previous = await session.scalar(
        select(OutboundDraft)
        .where(OutboundDraft.lead_id == lead_id)
        .order_by(OutboundDraft.sequence_step.desc())
        .limit(1)
    )
    sequence_step = 1 if not previous else previous.sequence_step + 1
    if sequence_step > 3:
        raise HTTPException(status_code=409, detail="시퀀스는 최대 3단계입니다.")
    if previous and (previous.reviewed_by is None or previous.sent_at is None):
        raise HTTPException(
            status_code=409, detail="이전 초안을 검토하고 발송 처리한 뒤 진행해주세요."
        )
    products = list(
        (
            await session.scalars(
                select(Product)
                .where(Product.brand == "LG", Product.is_verified.is_(True))
                .order_by(Product.id)
                .limit(5)
            )
        ).all()
    )
    try:
        result = await get_llm_client().structured(
            outbound_prompt(
                {
                    "name": lead.name,
                    "business_type": lead.business_type,
                    "years_in_business": lead.years_in_business,
                    "lead_score_reasoning": lead.lead_score_reasoning,
                },
                [
                    {
                        "name": product.name,
                        "category": product.category,
                        "business_price": trusted_business_price(product)[1],
                    }
                    for product in products
                ],
                sequence_step,
                sender_name,
                {"subject": previous.subject, "body": previous.body} if previous else None,
            ),
            DraftResult,
        )
    except Exception as error:
        raise HTTPException(status_code=502, detail="계약 제안 초안 생성에 실패했습니다.") from error
    subject_core = result.subject.strip().removeprefix("[공급 계약 제안]").strip()
    subject = f"[공급 계약 제안] {subject_core}"
    body = (
        f"안녕하세요. LG Deal Scale 담당자 {sender_name}입니다.\n\n"
        f"{result.body.strip()}\n\n"
        "구체적인 공급 수량과 일정, 계약 조건은 검토 후 협의를 통해 정리하겠습니다.\n\n"
        f"감사합니다.\n{sender_name} 드림"
    )
    draft = OutboundDraft(
        lead_id=lead.id,
        sequence_step=sequence_step,
        previous_draft_id=previous.id if previous else None,
        subject=subject,
        body=body,
        send_mode=get_settings().outbound_email_mode,
    )
    lead.pipeline_stage = "draft_generated"
    lead.next_action_at = None
    session.add(draft)
    try:
        await session.flush()
        record_audit(session, staff, "draft.create", "outbound_draft", draft.id)
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(
            status_code=409, detail="같은 단계의 초안이 이미 생성되었습니다."
        ) from error
    await session.refresh(draft)
    return draft_payload(draft)


@router.patch("/drafts/{draft_id}")
async def edit_draft(
    draft_id: int, payload: DraftEditRequest, session: Session, staff: CurrentStaff
) -> dict[str, object]:
    draft = await session.scalar(
        select(OutboundDraft).where(OutboundDraft.id == draft_id).with_for_update()
    )
    if not draft:
        raise HTTPException(status_code=404, detail="초안을 찾을 수 없습니다.")
    lead = await session.scalar(
        select(Lead).where(Lead.id == draft.lead_id).with_for_update()
    )
    if lead:
        require_lead_access(lead, staff)
        require_active_lead(lead)
    if draft.sent_at is not None:
        raise HTTPException(status_code=409, detail="발송 처리된 초안은 수정할 수 없습니다.")
    draft.subject = payload.subject
    draft.body = payload.body
    draft.reviewed_by = None
    if lead and lead.pipeline_stage == "approved":
        lead.pipeline_stage = "draft_generated"
    record_audit(session, staff, "draft.edit", "outbound_draft", draft.id)
    await session.commit()
    return draft_payload(draft)


@router.post("/leads/{lead_id}/actual-contact")
async def record_actual_contact(
    lead_id: int,
    payload: ManualContactRequest,
    session: Session,
    staff: CurrentStaff,
) -> dict[str, object]:
    lead = await session.scalar(select(Lead).where(Lead.id == lead_id).with_for_update())
    if not lead:
        raise HTTPException(status_code=404, detail="리드를 찾을 수 없습니다.")
    require_lead_access(lead, staff)
    if lead.pipeline_stage == "converted" or lead.pipeline_stage == "dropped":
        raise HTTPException(status_code=409, detail="종결된 리드는 변경할 수 없습니다.")
    previous = lead.pipeline_stage
    lead.pipeline_stage = "contacted"
    lead.next_action_at = None
    record_audit(
        session,
        staff,
        "lead.actual_contact",
        "lead",
        lead.id,
        {"from": previous, "channel": payload.channel, "note": payload.note},
    )
    await session.commit()
    return {"id": lead.id, "pipeline_stage": lead.pipeline_stage}


@router.post("/leads/{lead_id}/stop")
async def stop_sequence(lead_id: int, session: Session, staff: CurrentStaff) -> dict[str, object]:
    lead = await session.scalar(select(Lead).where(Lead.id == lead_id).with_for_update())
    if not lead:
        raise HTTPException(status_code=404, detail="리드를 찾을 수 없습니다.")
    require_lead_access(lead, staff)
    if lead.pipeline_stage == "converted":
        raise HTTPException(status_code=409, detail="전환된 리드는 중단할 수 없습니다.")
    if lead.pipeline_stage != "dropped":
        previous = lead.pipeline_stage
        lead.pipeline_stage = "dropped"
        lead.next_action_at = None
        record_audit(
            session,
            staff,
            "lead.sequence_stop",
            "lead",
            lead.id,
            {"from": previous, "to": "dropped"},
        )
    lead.next_action_at = None
    await session.commit()
    return {"id": lead.id, "pipeline_stage": lead.pipeline_stage}


@router.post("/drafts/{draft_id}/review")
async def review_draft(draft_id: int, session: Session, staff: CurrentStaff) -> dict[str, object]:
    draft = await session.scalar(
        select(OutboundDraft).where(OutboundDraft.id == draft_id).with_for_update()
    )
    if not draft:
        raise HTTPException(status_code=404, detail="초안을 찾을 수 없습니다.")
    lead = await session.scalar(
        select(Lead).where(Lead.id == draft.lead_id).with_for_update()
    )
    if lead:
        require_lead_access(lead, staff)
        require_active_lead(lead)
    if draft.sent_at is not None:
        raise HTTPException(status_code=409, detail="발송 처리된 초안은 검토할 수 없습니다.")
    draft.reviewed_by = staff.id
    if lead and lead.pipeline_stage == "draft_generated":
        lead.pipeline_stage = "approved"
    record_audit(session, staff, "draft.review", "outbound_draft", draft.id)
    await session.commit()
    return {"id": draft.id, "reviewed": True}


@router.post("/drafts/{draft_id}/send")
async def safe_send(draft_id: int, session: Session, staff: CurrentStaff) -> dict[str, object]:
    draft = await session.scalar(
        select(OutboundDraft).where(OutboundDraft.id == draft_id).with_for_update()
    )
    if not draft:
        raise HTTPException(status_code=404, detail="초안을 찾을 수 없습니다.")
    lead = await session.scalar(
        select(Lead).where(Lead.id == draft.lead_id).with_for_update()
    )
    if lead:
        require_lead_access(lead, staff)
        require_active_lead(lead)
    if not draft.reviewed_by:
        raise HTTPException(status_code=409, detail="담당자 검토 후에만 발송할 수 있습니다.")
    if draft.sent_at is not None:
        raise HTTPException(status_code=409, detail="이미 발송 처리된 초안입니다.")
    settings = get_settings()
    if settings.outbound_email_mode == "test_override":
        if not all((settings.test_email_address, settings.email_provider_api_key)):
            raise HTTPException(status_code=503, detail="테스트 발송 설정이 필요합니다.")
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {settings.email_provider_api_key}",
                    "Idempotency-Key": f"directdesk-draft-{draft.id}",
                },
                json={
                    "from": "LG Deal Scale Test <onboarding@resend.dev>",
                    "to": [settings.test_email_address],
                    "subject": draft.subject,
                    "text": draft.body,
                },
            )
            response.raise_for_status()
    draft.sent_at = datetime.now(timezone.utc)
    draft.send_mode = settings.outbound_email_mode
    record_audit(
        session,
        staff,
        "draft.send",
        "outbound_draft",
        draft.id,
        {"mode": draft.send_mode},
    )
    await session.commit()
    return {"id": draft.id, "mode": draft.send_mode, "sent_at": draft.sent_at}


@router.get("/dashboard")
async def dashboard(session: Session, staff: CurrentStaff) -> dict[str, object]:
    stage_statement = select(Lead.pipeline_stage, func.count())
    draft_statement = select(
        func.count(OutboundDraft.id),
        func.sum(case((OutboundDraft.reviewed_by.is_not(None), 1), else_=0)),
    )
    sequence_statement = select(OutboundDraft.sequence_step, func.count())
    if staff.role == "rep":
        stage_statement = stage_statement.where(Lead.assignee_id == staff.id)
        draft_statement = draft_statement.join(Lead).where(Lead.assignee_id == staff.id)
        sequence_statement = sequence_statement.join(Lead).where(Lead.assignee_id == staff.id)
    stages = dict((await session.execute(stage_statement.group_by(Lead.pipeline_stage))).all())
    draft_counts = (await session.execute(draft_statement)).one()
    sequence = dict(
        (await session.execute(sequence_statement.group_by(OutboundDraft.sequence_step))).all()
    )
    total, reviewed = int(draft_counts[0] or 0), int(draft_counts[1] or 0)
    return {
        "pipeline": stages,
        "draft_approval_rate": round(reviewed / total, 3) if total else 0,
        "sequence_distribution": sequence,
        "outbound_email_mode": get_settings().outbound_email_mode,
    }
