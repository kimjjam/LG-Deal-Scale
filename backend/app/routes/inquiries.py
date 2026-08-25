import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import record_audit
from app.database import get_session
from app.llm import get_llm_client
from app.models import (
    Account,
    Assignment,
    Inquiry,
    Opportunity,
    OpportunityStageHistory,
    Partner,
    Score,
    Staff,
)
from app.schemas import (
    InquiryConversionRequest,
    InquiryCreate,
    InquiryResponse,
    InquiryStatusRequest,
    IntentCorrectionRequest,
    ManualAssignmentRequest,
    OpportunityResponse,
    PartnerLinkRequest,
)
from app.scoring import INTENT_POINTS, calculate_total
from app.security import CurrentStaff, ManagerStaff, require_account_access
from app.services import (
    claim_inquiry,
    create_inquiry,
    manager_account_ids,
    manager_region_keywords,
    manually_assign,
    partner_matches_region_keywords,
    regional_manager_id,
    require_inquiry_access,
    score_inquiry,
)

router = APIRouter(prefix="/api/inquiries", tags=["inquiries"])
Session = Annotated[AsyncSession, Depends(get_session)]


def _nearby_store_search_from_raw(raw: object | None) -> dict[str, object] | None:
    if not isinstance(raw, list):
        return None
    for entry in reversed(raw):
        if not isinstance(entry, dict) or entry.get("type") != "nearby_store_search":
            continue
        status = entry.get("status")
        message = entry.get("message")
        stores = entry.get("stores")
        if (
            status not in {"location_missing", "not_configured", "failed", "no_results", "success"}
            or not isinstance(message, str)
            or not isinstance(stores, list)
        ):
            return None
        valid_stores = [
            {
                "name": store.get("name", ""),
                "address": store.get("address", ""),
                "phone": store.get("phone", ""),
            }
            for store in stores
            if isinstance(store, dict)
            and all(isinstance(store.get(field, ""), str) for field in ("name", "address", "phone"))
        ]
        return {"status": status, "message": message, "stores": valid_stores}
    return None


@router.post("", response_model=InquiryResponse)
async def create(payload: InquiryCreate, session: Session, staff: CurrentStaff) -> Inquiry:
    account = await session.scalar(
        select(Account).where(Account.id == payload.account_id, Account.deleted_at.is_(None))
    )
    if not account:
        raise HTTPException(status_code=404, detail="활성 고객사를 찾을 수 없습니다.")
    if staff.role != "owner":
        await require_account_access(session, payload.account_id, staff)
    location = account.attributes.get("location") if isinstance(account.attributes, dict) else None
    routing_manager_id = (
        staff.id
        if staff.role == "manager"
        else await regional_manager_id(session, location if isinstance(location, str) else None)
    )
    try:
        llm = get_llm_client()
    except RuntimeError:
        llm = None
    inquiry, _ = await create_inquiry(
        session,
        payload.account_id,
        payload.channel,
        payload.content,
        None,
        llm,
        routing_manager_id=routing_manager_id,
    )
    return inquiry


@router.get("")
async def inbox(
    session: Session,
    staff: CurrentStaff,
    scope: Literal["unassigned", "mine", "my_region", "all"] | None = None,
    sort_by: Literal["priority", "latest"] = "priority",
    q: str | None = None,
    inquiry_status: Annotated[
        Literal["open", "routed", "resolved"] | None, Query(alias="status")
    ] = None,
    assignee_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict[str, object]]:
    region_keywords = await manager_region_keywords(session, staff)
    region_account_ids = (
        await manager_account_ids(session, staff) if staff.role == "manager" else set()
    )
    if scope:
        selected_scope = scope
    elif staff.role == "manager":
        selected_scope = "my_region"
    else:
        selected_scope = "all" if staff.role == "owner" else "unassigned"
    if selected_scope == "my_region" and staff.role != "manager":
        raise HTTPException(status_code=403, detail="지역 문의 범위는 매니저만 볼 수 있습니다.")
    if staff.role == "rep" and assignee_id not in {None, staff.id}:
        raise HTTPException(status_code=403, detail="다른 담당자의 문의는 조회할 수 없습니다.")
    if staff.role == "manager" and not region_keywords:
        return []
    current_assignee = (
        select(Assignment.assignee_id)
        .where(Assignment.inquiry_id == Inquiry.id)
        .order_by(Assignment.assigned_at.desc(), Assignment.id.desc())
        .limit(1)
        .correlate(Inquiry)
        .scalar_subquery()
    )
    current_assignee_name = (
        select(Staff.name)
        .join(Assignment, Assignment.assignee_id == Staff.id)
        .where(Assignment.inquiry_id == Inquiry.id)
        .order_by(Assignment.assigned_at.desc(), Assignment.id.desc())
        .limit(1)
        .correlate(Inquiry)
        .scalar_subquery()
    )
    routing_manager_name = (
        select(Staff.name)
        .where(Staff.id == Inquiry.routing_manager_id)
        .correlate(Inquiry)
        .scalar_subquery()
    )
    statement = (
        select(
            Inquiry,
            Score,
            Partner,
            current_assignee.label("assignee_id"),
            Account.name.label("account_name"),
            current_assignee_name.label("assignee_name"),
            routing_manager_name.label("routing_manager_name"),
        )
        .join(Account, Account.id == Inquiry.account_id)
        .outerjoin(Score, Score.inquiry_id == Inquiry.id)
        .outerjoin(Partner, Partner.id == Inquiry.partner_id)
        .where(Account.deleted_at.is_(None))
    )
    if staff.role == "manager":
        statement = statement.where(Account.id.in_(region_account_ids))
    if selected_scope == "mine":
        statement = statement.where(current_assignee == staff.id)
    elif selected_scope == "unassigned":
        statement = statement.where(current_assignee.is_(None), Inquiry.status == "open")
    elif selected_scope == "my_region":
        statement = statement.where(Account.id.in_(region_account_ids))
    if q:
        statement = statement.where(
            or_(Inquiry.content.ilike(f"%{q}%"), Account.name.ilike(f"%{q}%"))
        )
    if inquiry_status:
        statement = statement.where(Inquiry.status == inquiry_status)
    if assignee_id:
        statement = statement.where(current_assignee == assignee_id)
    if sort_by == "priority":
        statement = statement.order_by(
            case((Score.id.is_(None), 0), else_=1),
            Score.total_score.desc(),
            Inquiry.created_at.desc(),
        )
    else:
        statement = statement.order_by(Inquiry.created_at.desc())
    rows = (await session.execute(statement.limit(limit).offset(offset))).all()
    return [
        {
            "id": inquiry.id,
            "account_id": inquiry.account_id,
            "account_name": account_name,
            "content": inquiry.content,
            "status": inquiry.status,
            "created_at": inquiry.created_at,
            "assignee_id": assignee_id,
            "assignee_name": assignee_name,
            "routing_manager_id": inquiry.routing_manager_id,
            "routing_manager_name": routing_manager_name,
            "partner": None
            if partner is None
            else {
                "id": partner.id,
                "name": partner.name,
                "address": partner.address,
                "phone": partner.phone,
                "region": partner.region,
                "partner_type": partner.partner_type,
                "verification_source": partner.verification_source,
                "verified_at": partner.verified_at,
                "is_active": partner.is_active,
            },
            "nearby_store_search": _nearby_store_search_from_raw(inquiry.raw_conversation),
            "score": None
            if score is None
            else {
                "fit": score.fit_score,
                "intent": score.intent_score,
                "recency": score.recency_score,
                "total": score.total_score,
                "category": score.intent_category,
                "confidence": score.intent_confidence,
                "reasoning": score.reasoning,
            },
        }
        for inquiry, score, partner, assignee_id, account_name, assignee_name, routing_manager_name in rows
    ]


@router.post("/{inquiry_id}/score")
async def retry_score(inquiry_id: int, session: Session, staff: CurrentStaff) -> dict[str, object]:
    inquiry = await session.get(Inquiry, inquiry_id)
    if not inquiry:
        raise HTTPException(status_code=404, detail="문의를 찾을 수 없습니다.")
    await require_inquiry_access(session, inquiry, staff)
    try:
        score = await score_inquiry(session, inquiry_id, get_llm_client())
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    record_audit(session, staff, "inquiry.rescore", "inquiry", inquiry_id)
    await session.commit()
    return {"inquiry_id": inquiry_id, "total_score": score.total_score}


@router.post("/{inquiry_id}/assign")
async def reassign(
    inquiry_id: int,
    payload: ManualAssignmentRequest,
    session: Session,
    manager: ManagerStaff,
) -> dict[str, object]:
    inquiry = await session.get(Inquiry, inquiry_id)
    if not inquiry:
        raise HTTPException(status_code=404, detail="문의를 찾을 수 없습니다.")
    await require_inquiry_access(session, inquiry, manager)
    try:
        assignment = await manually_assign(session, inquiry, payload.assignee_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    record_audit(
        session,
        manager,
        "inquiry.assign",
        "inquiry",
        inquiry.id,
        {"assignee_id": str(payload.assignee_id)},
    )
    await session.commit()
    return {"assignment_id": assignment.id, "status": inquiry.status}


@router.post("/{inquiry_id}/claim")
async def claim(inquiry_id: int, session: Session, staff: CurrentStaff) -> dict[str, object]:
    try:
        inquiry, assignment = await claim_inquiry(session, inquiry_id, staff)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    record_audit(
        session,
        staff,
        "inquiry.claim",
        "inquiry",
        inquiry.id,
        {"assignee_id": str(staff.id)},
    )
    await session.commit()
    return {"assignment_id": assignment.id, "status": inquiry.status}


@router.patch("/{inquiry_id}/partner")
async def link_partner(
    inquiry_id: int,
    payload: PartnerLinkRequest,
    session: Session,
    manager: ManagerStaff,
) -> dict[str, object]:
    inquiry = await session.get(Inquiry, inquiry_id)
    if not inquiry:
        raise HTTPException(status_code=404, detail="문의를 찾을 수 없습니다.")
    await require_inquiry_access(session, inquiry, manager)
    partner = await session.get(Partner, payload.partner_id) if payload.partner_id else None
    if payload.partner_id and (not partner or not partner.is_active):
        raise HTTPException(status_code=404, detail="활성 검증 파트너를 찾을 수 없습니다.")
    region_keywords = await manager_region_keywords(session, manager)
    if partner and region_keywords and not partner_matches_region_keywords(partner, region_keywords):
        raise HTTPException(status_code=403, detail="내 담당 지역의 파트너만 연결할 수 있습니다.")
    inquiry.partner_id = partner.id if partner else None
    record_audit(
        session,
        manager,
        "inquiry.partner_link",
        "inquiry",
        inquiry.id,
        {"partner_id": inquiry.partner_id},
    )
    await session.commit()
    return {"inquiry_id": inquiry.id, "partner_id": inquiry.partner_id}


@router.patch("/{inquiry_id}/status", response_model=InquiryResponse)
async def update_status(
    inquiry_id: int,
    payload: InquiryStatusRequest,
    session: Session,
    staff: CurrentStaff,
) -> Inquiry:
    if payload.status not in ("open", "resolved"):
        raise HTTPException(status_code=422, detail="문의는 완료하거나 다시 열 수 있습니다.")
    inquiry = await session.get(Inquiry, inquiry_id)
    if not inquiry:
        raise HTTPException(status_code=404, detail="문의를 찾을 수 없습니다.")
    await require_inquiry_access(session, inquiry, staff)
    if inquiry.status != payload.status:
        previous = inquiry.status
        inquiry.status = payload.status
        record_audit(
            session,
            staff,
            "inquiry.status_change",
            "inquiry",
            inquiry.id,
            {"from": previous, "to": inquiry.status},
        )
        await session.commit()
        await session.refresh(inquiry)
    return inquiry


@router.patch("/{inquiry_id}/intent")
async def correct_intent(
    inquiry_id: int,
    payload: IntentCorrectionRequest,
    session: Session,
    staff: CurrentStaff,
) -> dict[str, object]:
    inquiry = await session.get(Inquiry, inquiry_id)
    if not inquiry:
        raise HTTPException(status_code=404, detail="문의를 찾을 수 없습니다.")
    await require_inquiry_access(session, inquiry, staff)
    score = await session.scalar(select(Score).where(Score.inquiry_id == inquiry_id))
    if not score:
        raise HTTPException(status_code=404, detail="수정할 점수를 찾을 수 없습니다.")
    previous = score.intent_category
    score.intent_category = payload.category
    score.intent_score = INTENT_POINTS[payload.category]
    score.intent_confidence = payload.confidence
    score.total_score = calculate_total(score.fit_score, score.intent_score, score.recency_score)
    score.reasoning = {**score.reasoning, "intent": f"담당자 수정: {payload.reasoning}"}
    record_audit(
        session,
        staff,
        "inquiry.intent_correct",
        "inquiry",
        inquiry_id,
        {"from": previous, "to": payload.category},
    )
    await session.commit()
    return {
        "inquiry_id": inquiry_id,
        "category": score.intent_category,
        "intent_score": score.intent_score,
        "total_score": score.total_score,
    }


@router.post(
    "/{inquiry_id}/opportunity",
    response_model=OpportunityResponse,
    status_code=201,
)
async def convert_to_opportunity(
    inquiry_id: int,
    payload: InquiryConversionRequest,
    session: Session,
    staff: CurrentStaff,
) -> Opportunity:
    inquiry = await session.get(Inquiry, inquiry_id)
    if not inquiry:
        raise HTTPException(status_code=404, detail="문의를 찾을 수 없습니다.")
    await require_inquiry_access(session, inquiry, staff)
    account = await session.get(Account, inquiry.account_id)
    if not account or account.deleted_at is not None:
        raise HTTPException(status_code=404, detail="활성 고객사를 찾을 수 없습니다.")
    if await session.scalar(select(Opportunity.id).where(Opportunity.inquiry_id == inquiry.id)):
        raise HTTPException(status_code=409, detail="이미 영업기회로 전환된 문의입니다.")
    assignee_id = await session.scalar(
        select(Assignment.assignee_id)
        .where(Assignment.inquiry_id == inquiry.id)
        .order_by(Assignment.assigned_at.desc(), Assignment.id.desc())
        .limit(1)
    )
    assignee = await session.get(Staff, assignee_id) if assignee_id else None
    if not assignee or not assignee.is_active or assignee.role != "rep":
        if staff.role == "rep" and staff.is_active:
            assignee_id = staff.id
        else:
            raise HTTPException(
                status_code=422,
                detail="활성 영업 담당자를 먼저 배정해주세요.",
            )
    opportunity = Opportunity(
        account_id=inquiry.account_id,
        inquiry_id=inquiry.id,
        assignee_id=assignee_id,
        title=payload.title,
        amount=payload.amount,
        probability=payload.probability,
        expected_close_date=payload.expected_close_date,
        stage="qualify",
    )
    session.add(opportunity)
    try:
        await session.flush()
        session.add(
            OpportunityStageHistory(
                opportunity_id=opportunity.id, stage="qualify", changed_by=staff.id
            )
        )
        inquiry.status = "resolved"
        record_audit(
            session,
            staff,
            "inquiry.convert",
            "inquiry",
            inquiry.id,
            {"opportunity_id": opportunity.id},
        )
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(status_code=409, detail="이미 전환된 문의입니다.") from error
    await session.refresh(opportunity)
    return opportunity
