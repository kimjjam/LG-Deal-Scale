import uuid
from typing import Any

from sqlalchemy import exists, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm import LLMClient
from app.models import (
    Account,
    Assignment,
    Inquiry,
    Interaction,
    Partner,
    SalesRegion,
    Score,
    Staff,
    utcnow,
)
from app.prompts import intent_prompt
from app.schemas import IntentResult, normalize_region_text
from app.scoring import INTENT_POINTS, calculate_fit, calculate_recency, calculate_total


async def classify_intent(content: str, llm: LLMClient) -> IntentResult:
    return await llm.structured(intent_prompt(content), IntentResult)


async def score_inquiry(session: AsyncSession, inquiry_id: int, llm: LLMClient) -> Score:
    inquiry = await session.get(Inquiry, inquiry_id)
    if not inquiry:
        raise ValueError("Inquiry not found")
    account = await session.get(Account, inquiry.account_id)
    if not account:
        raise ValueError("Account not found")
    last_interaction = await session.scalar(
        select(Interaction.created_at)
        .where(
            Interaction.account_id == inquiry.account_id,
            Interaction.created_at < inquiry.created_at,
        )
        .order_by(Interaction.created_at.desc())
        .limit(1)
    )
    fit, fit_reason = calculate_fit(account.attributes)
    intent = await classify_intent(inquiry.content, llm)
    intent_score = INTENT_POINTS[intent.category]
    recency, recency_reason = calculate_recency(last_interaction, inquiry.created_at)
    values = {
        "inquiry_id": inquiry.id,
        "fit_score": fit,
        "intent_score": intent_score,
        "intent_category": intent.category,
        "intent_confidence": intent.confidence,
        "recency_score": recency,
        "total_score": calculate_total(fit, intent_score, recency),
        "reasoning": {
            "fit": fit_reason,
            "intent": intent.reasoning,
            "recency": recency_reason,
        },
        "scoring_version": "v2",
        "llm_provider": llm.provider,
        "model_name": llm.model,
        "updated_at": utcnow(),
    }
    if session.get_bind().dialect.name == "postgresql":
        statement = (
            pg_insert(Score)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[Score.inquiry_id],
                set_={key: value for key, value in values.items() if key != "inquiry_id"},
            )
        )
        await session.execute(statement)
        await session.flush()
        score = await session.scalar(select(Score).where(Score.inquiry_id == inquiry.id))
    else:
        score = await session.scalar(select(Score).where(Score.inquiry_id == inquiry.id))
        if score:
            for key, value in values.items():
                setattr(score, key, value)
        else:
            score = Score(**values)
            session.add(score)
        await session.flush()
    if not score:
        raise RuntimeError("Score upsert failed")
    return score


async def manually_assign(
    session: AsyncSession, inquiry: Inquiry, assignee_id: uuid.UUID
) -> Assignment:
    assignee = await session.get(Staff, assignee_id)
    if not assignee or assignee.role != "rep" or not assignee.is_active:
        raise ValueError("Sales representative not found")
    assignment = Assignment(inquiry_id=inquiry.id, assignee_id=assignee_id, method="manual")
    inquiry.status = "routed"
    session.add(assignment)
    await session.flush()
    return assignment


async def claim_inquiry(
    session: AsyncSession, inquiry_id: int, staff: Staff
) -> tuple[Inquiry, Assignment]:
    if staff.role != "rep" or not staff.is_active:
        raise PermissionError("활성 영업 담당자만 문의를 담당할 수 있습니다.")
    transition = await session.execute(
        update(Inquiry)
        .where(
            Inquiry.id == inquiry_id,
            Inquiry.status == "open",
            ~exists().where(Assignment.inquiry_id == Inquiry.id),
        )
        .values(status="routed")
    )
    if transition.rowcount != 1:
        if not await session.get(Inquiry, inquiry_id):
            raise ValueError("문의를 찾을 수 없습니다.")
        raise RuntimeError("이미 담당자가 지정된 문의입니다.")
    inquiry = await session.get(Inquiry, inquiry_id)
    if not inquiry:
        raise ValueError("문의를 찾을 수 없습니다.")
    assignment = Assignment(inquiry_id=inquiry_id, assignee_id=staff.id, method="claimed")
    session.add(assignment)
    await session.flush()
    return inquiry, assignment


async def regional_manager_id(session: AsyncSession, location: str | None) -> uuid.UUID | None:
    if not location:
        return None
    regions = (
        await session.execute(
            select(SalesRegion.match_keyword, SalesRegion.manager_id)
            .join(Staff, Staff.id == SalesRegion.manager_id)
            .where(
                SalesRegion.is_active.is_(True),
                Staff.is_active.is_(True),
                Staff.role == "manager",
            )
        )
    ).all()
    normalized = normalize_region_text(location)
    matches = [
        (normalize_region_text(keyword), manager_id)
        for keyword, manager_id in regions
        if normalize_region_text(keyword) in normalized
    ]
    return (
        max(matches, key=lambda item: (len(item[0]), item[0], str(item[1])))[1] if matches else None
    )


async def curated_partner_id(session: AsyncSession, location: str | None) -> int | None:
    if not location:
        return None
    normalized = normalize_region_text(location)
    partners = list(
        (await session.scalars(select(Partner).where(Partner.is_active.is_(True)))).all()
    )
    matches = [
        partner for partner in partners if normalize_region_text(partner.region) in normalized
    ]
    return (
        min(
            matches, key=lambda partner: (-len(normalize_region_text(partner.region)), partner.id)
        ).id
        if matches
        else None
    )


async def create_inquiry(
    session: AsyncSession,
    account_id: int,
    channel: str,
    content: str,
    raw_conversation: list[dict[str, Any]] | None,
    llm: LLMClient | None,
    routing_manager_id: uuid.UUID | None = None,
    partner_id: int | None = None,
) -> tuple[Inquiry, bool]:
    inquiry = Inquiry(
        account_id=account_id,
        channel=channel,
        content=content,
        raw_conversation=raw_conversation,
        routing_manager_id=routing_manager_id,
        partner_id=partner_id,
    )
    session.add(inquiry)
    await session.commit()
    await session.refresh(inquiry)
    inquiry_id = inquiry.id
    scoring_failed = llm is None
    if llm:
        try:
            await score_inquiry(session, inquiry_id, llm)
            await session.commit()
        except Exception:  # noqa: BLE001 - scoring failure must not undo the saved inquiry
            await session.rollback()
            scoring_failed = True
    inquiry = await session.get(Inquiry, inquiry_id) or inquiry
    return inquiry, scoring_failed
