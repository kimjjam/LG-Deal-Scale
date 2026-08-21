import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm import LLMClient
from app.models import Account, Assignment, Inquiry, Interaction, Score, Staff, utcnow
from app.prompts import intent_prompt
from app.schemas import IntentResult
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
        "scoring_version": "v1",
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


async def auto_assign(session: AsyncSession, inquiry: Inquiry) -> Assignment:
    prior_inquiry_id = await session.scalar(
        select(Inquiry.id)
        .where(
            Inquiry.account_id == inquiry.account_id,
            Inquiry.id != inquiry.id,
            Inquiry.status.in_(("open", "routed")),
        )
        .order_by(Inquiry.created_at.desc())
        .limit(1)
    )
    assignee_id: uuid.UUID | None = None
    if prior_inquiry_id:
        prior_assignee_id = await session.scalar(
            select(Assignment.assignee_id)
            .where(Assignment.inquiry_id == prior_inquiry_id)
            .order_by(Assignment.assigned_at.desc(), Assignment.id.desc())
            .limit(1)
        )
        if prior_assignee_id:
            assignee_id = await session.scalar(
                select(Staff.id).where(
                    Staff.id == prior_assignee_id,
                    Staff.role == "rep",
                    Staff.is_active.is_(True),
                )
            )
    if assignee_id is None:
        reps = list(
            (
                await session.scalars(
                    select(Staff)
                    .where(Staff.role == "rep", Staff.is_active.is_(True))
                    .order_by(Staff.email)
                    .with_for_update()
                )
            ).all()
        )
        if not reps:
            raise RuntimeError("No sales representatives available")
        last_assignee = await session.scalar(
            select(Assignment.assignee_id)
            .join(Staff, Staff.id == Assignment.assignee_id)
            .where(
                Staff.role == "rep",
                Staff.is_active.is_(True),
                Assignment.method == "round_robin",
            )
            .order_by(Assignment.assigned_at.desc(), Assignment.id.desc())
            .limit(1)
        )
        rep_ids = [rep.id for rep in reps]
        index = (rep_ids.index(last_assignee) + 1) % len(rep_ids) if last_assignee in rep_ids else 0
        assignee_id = rep_ids[index]
    assignment = Assignment(inquiry_id=inquiry.id, assignee_id=assignee_id, method="round_robin")
    inquiry.status = "routed"
    session.add(assignment)
    await session.flush()
    return assignment


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


async def create_inquiry(
    session: AsyncSession,
    account_id: int,
    channel: str,
    content: str,
    raw_conversation: list[dict[str, Any]] | None,
    llm: LLMClient | None,
) -> tuple[Inquiry, bool]:
    inquiry = Inquiry(
        account_id=account_id,
        channel=channel,
        content=content,
        raw_conversation=raw_conversation,
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
    try:
        inquiry = await session.get(Inquiry, inquiry_id) or inquiry
        await auto_assign(session, inquiry)
        await session.commit()
    except Exception:  # noqa: BLE001 - assignment failure must not undo the saved inquiry
        await session.rollback()
        inquiry = await session.get(Inquiry, inquiry_id) or inquiry
    return inquiry, scoring_failed
