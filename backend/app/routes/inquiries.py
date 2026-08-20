from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.llm import get_llm_client
from app.models import Account, Assignment, Inquiry, Score, Staff
from app.schemas import InquiryCreate, InquiryResponse, ManualAssignmentRequest
from app.security import CurrentStaff, ManagerStaff
from app.services import create_inquiry, manually_assign, score_inquiry

router = APIRouter(prefix="/api/inquiries", tags=["inquiries"])
Session = Annotated[AsyncSession, Depends(get_session)]


@router.post("", response_model=InquiryResponse)
async def create(payload: InquiryCreate, session: Session, _staff: CurrentStaff) -> Inquiry:
    try:
        llm = get_llm_client()
    except RuntimeError:
        llm = None
    inquiry, _ = await create_inquiry(
        session, payload.account_id, payload.channel, payload.content, None, llm
    )
    return inquiry


@router.get("")
async def inbox(
    session: Session,
    staff: CurrentStaff,
    scope: Literal["mine", "all"] | None = None,
    sort_by: Literal["priority", "latest"] = "priority",
) -> list[dict[str, object]]:
    selected_scope = scope or ("all" if staff.role == "manager" else "mine")
    current_assignee = (
        select(Assignment.assignee_id)
        .where(Assignment.inquiry_id == Inquiry.id)
        .order_by(Assignment.assigned_at.desc())
        .limit(1)
        .correlate(Inquiry)
        .scalar_subquery()
    )
    current_assignee_name = (
        select(Staff.name)
        .join(Assignment, Assignment.assignee_id == Staff.id)
        .where(Assignment.inquiry_id == Inquiry.id)
        .order_by(Assignment.assigned_at.desc())
        .limit(1)
        .correlate(Inquiry)
        .scalar_subquery()
    )
    statement = (
        select(
            Inquiry,
            Score,
            current_assignee.label("assignee_id"),
            Account.name.label("account_name"),
            current_assignee_name.label("assignee_name"),
        )
        .join(Account, Account.id == Inquiry.account_id)
        .outerjoin(Score, Score.inquiry_id == Inquiry.id)
    )
    if selected_scope == "mine":
        statement = statement.where(current_assignee == staff.id)
    if sort_by == "priority":
        statement = statement.order_by(
            case((Score.id.is_(None), 0), else_=1),
            Score.total_score.desc(),
            Inquiry.created_at.desc(),
        )
    else:
        statement = statement.order_by(Inquiry.created_at.desc())
    rows = (await session.execute(statement)).all()
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
        for inquiry, score, assignee_id, account_name, assignee_name in rows
    ]


@router.post("/{inquiry_id}/score")
async def retry_score(inquiry_id: int, session: Session, _staff: CurrentStaff) -> dict[str, object]:
    try:
        score = await score_inquiry(session, inquiry_id, get_llm_client())
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    await session.commit()
    return {"inquiry_id": inquiry_id, "total_score": score.total_score}


@router.post("/{inquiry_id}/assign")
async def reassign(
    inquiry_id: int,
    payload: ManualAssignmentRequest,
    session: Session,
    _manager: ManagerStaff,
) -> dict[str, object]:
    inquiry = await session.get(Inquiry, inquiry_id)
    if not inquiry:
        raise HTTPException(status_code=404, detail="문의를 찾을 수 없습니다.")
    try:
        assignment = await manually_assign(session, inquiry, payload.assignee_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    await session.commit()
    return {"assignment_id": assignment.id, "status": inquiry.status}
