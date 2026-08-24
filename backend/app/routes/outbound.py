import csv
import io
import json
from datetime import date, datetime, timezone
from typing import Annotated

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
from app.localdata import SBIZ_LODGING_CODE, SBIZ_STORE_URL, parse_sbiz_rows
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
    "contacted": {"follow_up_due", "dropped"},
    "follow_up_due": {"contacted", "dropped"},
}


class DraftResult(BaseModel):
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=20_000)


class SbizSyncRequest(BaseModel):
    region_code: str = Field(pattern=r"^\d{2}$")
    page: int = Field(default=1, ge=1, le=10_000)
    rows: int = Field(default=100, ge=1, le=100)


def csv_safe(value: object) -> object:
    dangerous = ("=", "+", "-", "@", "\t", "\r")
    return f"'{value}" if isinstance(value, str) and value.startswith(dangerous) else value


def require_active_lead(lead: Lead) -> None:
    if lead.pipeline_stage in TERMINAL_LEAD_STAGES:
        raise HTTPException(status_code=409, detail="종결된 리드는 변경할 수 없습니다.")


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
                    "indsLclsCd": SBIZ_LODGING_CODE,
                    "type": "json",
                },
            )
            response.raise_for_status()
            rows, total_count = parse_sbiz_rows(response.json())
    except (httpx.HTTPError, TypeError, ValueError) as error:
        raise HTTPException(status_code=502, detail="상가정보 API를 불러오지 못했습니다.") from error

    external_ids = [row["external_id"] for row in rows]
    existing = {
        lead.external_id: lead
        for lead in (
            await session.scalars(select(Lead).where(Lead.external_id.in_(external_ids)))
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
        raise HTTPException(status_code=409, detail="동시에 같은 공공데이터 리드가 저장되었습니다.") from error
    return {
        "fetched_count": len(rows),
        "created_count": created,
        "updated_count": len(rows) - created,
        "total_count": total_count,
    }


@router.get("/leads")
async def list_leads(
    session: Session,
    _staff: CurrentStaff,
    q: str | None = None,
    pipeline_stage: Annotated[str | None, Query(alias="status", max_length=30)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict[str, object]]:
    statement = select(Lead)
    if q:
        statement = statement.where(or_(Lead.name.ilike(f"%{q}%"), Lead.address.ilike(f"%{q}%")))
    if pipeline_stage:
        statement = statement.where(Lead.pipeline_stage == pipeline_stage)
    leads = list(
        (
            await session.scalars(
                statement.order_by(Lead.lead_score.desc()).limit(limit).offset(offset)
            )
        ).all()
    )
    return [
        {
            "id": lead.id,
            "name": lead.name,
            "address": lead.address,
            "business_type": lead.business_type,
            "lead_score": lead.lead_score,
            "reasoning": lead.lead_score_reasoning,
            "pipeline_stage": lead.pipeline_stage,
        }
        for lead in leads
    ]


@router.get("/leads/export.csv")
async def export_leads(session: Session, _staff: CurrentStaff) -> Response:
    output = io.StringIO()
    fields = [
        "name",
        "address",
        "license_date",
        "years_in_business",
        "business_type",
        "source",
        "lead_score",
        "lead_score_reasoning",
        "pipeline_stage",
    ]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for lead in (await session.scalars(select(Lead).order_by(Lead.id))).all():
        row = {
            "name": lead.name,
            "address": lead.address or "",
            "license_date": lead.license_date.isoformat() if lead.license_date else "",
            "years_in_business": lead.years_in_business
            if lead.years_in_business is not None
            else "",
            "business_type": lead.business_type or "",
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
            source = (row.get("source") or "csv").strip() or "csv"
            stage = (row.get("pipeline_stage") or "discovered").strip()
            if len(address) > 500 or len(business_type) > 100 or len(source) > 30:
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
    lead_id: int, session: Session, _staff: CurrentStaff
) -> list[dict[str, object]]:
    if not await session.get(Lead, lead_id):
        raise HTTPException(status_code=404, detail="리드를 찾을 수 없습니다.")
    drafts = (
        await session.scalars(
            select(OutboundDraft)
            .where(OutboundDraft.lead_id == lead_id)
            .order_by(OutboundDraft.sequence_step.desc())
        )
    ).all()
    return [draft_payload(draft) for draft in drafts]


@router.put("/leads/{lead_id}/stage")
async def change_stage(
    lead_id: int, payload: LeadStageRequest, session: Session, staff: ManagerStaff
) -> dict[str, object]:
    lead = await session.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="리드를 찾을 수 없습니다.")
    if payload.pipeline_stage == "converted":
        raise HTTPException(status_code=422, detail="리드 전환 API를 사용해주세요.")
    if payload.pipeline_stage == "approved":
        raise HTTPException(status_code=422, detail="초안 검토 API를 사용해주세요.")
    previous = lead.pipeline_stage
    require_active_lead(lead)
    if (
        payload.pipeline_stage != previous
        and payload.pipeline_stage not in LEAD_STAGE_TRANSITIONS[previous]
    ):
        raise HTTPException(status_code=409, detail="허용되지 않는 리드 단계 변경입니다.")
    lead.pipeline_stage = payload.pipeline_stage
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
    staff: ManagerStaff,
) -> Opportunity:
    lead = await session.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="리드를 찾을 수 없습니다.")
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
async def generate_draft(lead_id: int, session: Session, staff: ManagerStaff) -> dict[str, object]:
    lead = await session.scalar(select(Lead).where(Lead.id == lead_id).with_for_update())
    if not lead:
        raise HTTPException(status_code=404, detail="리드를 찾을 수 없습니다.")
    require_active_lead(lead)
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
                {"subject": previous.subject, "body": previous.body} if previous else None,
            ),
            DraftResult,
        )
    except Exception as error:
        raise HTTPException(status_code=502, detail="마케팅 초안 생성에 실패했습니다.") from error
    draft = OutboundDraft(
        lead_id=lead.id,
        sequence_step=sequence_step,
        previous_draft_id=previous.id if previous else None,
        subject=result.subject,
        body=result.body,
        send_mode=get_settings().outbound_email_mode,
    )
    if lead.pipeline_stage == "discovered":
        lead.pipeline_stage = "draft_generated"
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
    draft_id: int, payload: DraftEditRequest, session: Session, staff: ManagerStaff
) -> dict[str, object]:
    draft = await session.scalar(
        select(OutboundDraft).where(OutboundDraft.id == draft_id).with_for_update()
    )
    if not draft:
        raise HTTPException(status_code=404, detail="초안을 찾을 수 없습니다.")
    if draft.sent_at is not None:
        raise HTTPException(status_code=409, detail="발송 처리된 초안은 수정할 수 없습니다.")
    lead = await session.get(Lead, draft.lead_id)
    if lead:
        require_active_lead(lead)
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
    staff: ManagerStaff,
) -> dict[str, object]:
    lead = await session.scalar(select(Lead).where(Lead.id == lead_id).with_for_update())
    if not lead:
        raise HTTPException(status_code=404, detail="리드를 찾을 수 없습니다.")
    if lead.pipeline_stage == "converted" or lead.pipeline_stage == "dropped":
        raise HTTPException(status_code=409, detail="종결된 리드는 변경할 수 없습니다.")
    previous = lead.pipeline_stage
    lead.pipeline_stage = "contacted"
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
async def stop_sequence(lead_id: int, session: Session, staff: ManagerStaff) -> dict[str, object]:
    lead = await session.scalar(select(Lead).where(Lead.id == lead_id).with_for_update())
    if not lead:
        raise HTTPException(status_code=404, detail="리드를 찾을 수 없습니다.")
    if lead.pipeline_stage == "converted":
        raise HTTPException(status_code=409, detail="전환된 리드는 중단할 수 없습니다.")
    if lead.pipeline_stage != "dropped":
        previous = lead.pipeline_stage
        lead.pipeline_stage = "dropped"
        record_audit(
            session,
            staff,
            "lead.sequence_stop",
            "lead",
            lead.id,
            {"from": previous, "to": "dropped"},
        )
        await session.commit()
    return {"id": lead.id, "pipeline_stage": lead.pipeline_stage}


@router.post("/drafts/{draft_id}/review")
async def review_draft(draft_id: int, session: Session, staff: ManagerStaff) -> dict[str, object]:
    draft = await session.scalar(
        select(OutboundDraft).where(OutboundDraft.id == draft_id).with_for_update()
    )
    if not draft:
        raise HTTPException(status_code=404, detail="초안을 찾을 수 없습니다.")
    lead = await session.get(Lead, draft.lead_id)
    if lead:
        require_active_lead(lead)
    draft.reviewed_by = staff.id
    if lead and lead.pipeline_stage == "draft_generated":
        lead.pipeline_stage = "approved"
    record_audit(session, staff, "draft.review", "outbound_draft", draft.id)
    await session.commit()
    return {"id": draft.id, "reviewed": True}


@router.post("/drafts/{draft_id}/send")
async def safe_send(draft_id: int, session: Session, staff: ManagerStaff) -> dict[str, object]:
    draft = await session.scalar(
        select(OutboundDraft).where(OutboundDraft.id == draft_id).with_for_update()
    )
    if not draft:
        raise HTTPException(status_code=404, detail="초안을 찾을 수 없습니다.")
    if not draft.reviewed_by:
        raise HTTPException(status_code=409, detail="담당자 검토 후에만 발송할 수 있습니다.")
    if draft.sent_at is not None:
        raise HTTPException(status_code=409, detail="이미 발송 처리된 초안입니다.")
    lead = await session.get(Lead, draft.lead_id)
    if lead:
        require_active_lead(lead)
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
                    "from": "DirectDesk Test <onboarding@resend.dev>",
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
async def dashboard(session: Session, _staff: CurrentStaff) -> dict[str, object]:
    stages = dict(
        (
            await session.execute(
                select(Lead.pipeline_stage, func.count()).group_by(Lead.pipeline_stage)
            )
        ).all()
    )
    draft_counts = (
        await session.execute(
            select(
                func.count(OutboundDraft.id),
                func.sum(case((OutboundDraft.reviewed_by.is_not(None), 1), else_=0)),
            )
        )
    ).one()
    sequence = dict(
        (
            await session.execute(
                select(OutboundDraft.sequence_step, func.count()).group_by(
                    OutboundDraft.sequence_step
                )
            )
        ).all()
    )
    total, reviewed = int(draft_counts[0] or 0), int(draft_counts[1] or 0)
    return {
        "pipeline": stages,
        "draft_approval_rate": round(reviewed / total, 3) if total else 0,
        "sequence_distribution": sequence,
        "outbound_email_mode": get_settings().outbound_email_mode,
    }
