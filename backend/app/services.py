import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy import exists, func, select, update
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
from app.schemas import IntentResult, normalize_region_text, region_keyword_matches
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


async def manager_region_keywords(session: AsyncSession, staff: Staff) -> set[str]:
    if staff.role != "manager":
        return set()
    return {
        keyword
        for keyword in await session.scalars(
            select(SalesRegion.match_keyword).where(
                SalesRegion.manager_id == staff.id,
                SalesRegion.is_active.is_(True),
            )
        )
    }


def partner_matches_region_keywords(partner: Partner, keywords: set[str]) -> bool:
    return any(
        region_keyword_matches(partner.region, keyword, bidirectional=True)
        for keyword in keywords
    )


def account_matches_region_keywords(account: Account, keywords: set[str]) -> bool:
    location = account.attributes.get("location") if isinstance(account.attributes, dict) else None
    return isinstance(location, str) and any(
        region_keyword_matches(location, keyword) for keyword in keywords
    )


async def manager_account_ids(session: AsyncSession, staff: Staff) -> set[int]:
    regions = await _active_region_assignments(session)
    if not any(manager_id == staff.id for _, manager_id in regions):
        return set()
    # ponytail: normalized locations live in JSON; materialize a column if account volume makes this scan slow.
    accounts = list(
        (await session.scalars(select(Account).where(Account.deleted_at.is_(None)))).all()
    )
    return {
        account.id
        for account in accounts
        if staff.id
        in _winning_region_manager_ids(
            account.attributes.get("location")
            if isinstance(account.attributes, dict)
            else None,
            regions,
        )
    }


async def require_inquiry_access(
    session: AsyncSession, inquiry: Inquiry, staff: Staff
) -> None:
    if staff.role == "owner":
        return
    if staff.role == "manager":
        account = await session.get(Account, inquiry.account_id)
        location = (
            account.attributes.get("location")
            if account and isinstance(account.attributes, dict)
            else None
        )
        if staff.id not in await regional_manager_ids(session, location):
            raise HTTPException(status_code=403, detail="내 담당 지역의 문의만 조회하거나 변경할 수 있습니다.")
        return
    assignee_id = await session.scalar(
        select(Assignment.assignee_id)
        .where(Assignment.inquiry_id == inquiry.id)
        .order_by(Assignment.assigned_at.desc(), Assignment.id.desc())
        .limit(1)
    )
    if assignee_id != staff.id:
        raise HTTPException(status_code=403, detail="배정된 담당자만 변경할 수 있습니다.")


async def _active_region_assignments(
    session: AsyncSession,
) -> list[tuple[str, uuid.UUID]]:
    return list(
        (
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
    )


def _winning_region_manager_ids(
    location: object, regions: list[tuple[str, uuid.UUID]]
) -> set[uuid.UUID]:
    if not isinstance(location, str):
        return set()
    matches = [
        (normalize_region_text(keyword), manager_id)
        for keyword, manager_id in regions
        if region_keyword_matches(location, keyword)
    ]
    if not matches:
        return set()
    winning_keyword = max((keyword for keyword, _ in matches), key=lambda item: (len(item), item))
    return {manager_id for keyword, manager_id in matches if keyword == winning_keyword}


async def regional_manager_ids(session: AsyncSession, location: str | None) -> set[uuid.UUID]:
    return _winning_region_manager_ids(location, await _active_region_assignments(session))


async def regional_manager_id(session: AsyncSession, location: str | None) -> uuid.UUID | None:
    manager_ids = await regional_manager_ids(session, location)
    if not manager_ids:
        return None
    workloads = dict(
        (
            await session.execute(
                select(Inquiry.routing_manager_id, func.count(Inquiry.id))
                .where(
                    Inquiry.routing_manager_id.in_(manager_ids),
                    Inquiry.status.in_(("open", "routed")),
                )
                .group_by(Inquiry.routing_manager_id)
            )
        ).all()
    )
    # ponytail: ties use UUID order; add row locking only if simultaneous intake causes skew.
    return min(manager_ids, key=lambda item: (workloads.get(item, 0), str(item)))


async def curated_partner_id(session: AsyncSession, location: str | None) -> int | None:
    if not location:
        return None
    partners = list(
        (await session.scalars(select(Partner).where(Partner.is_active.is_(True)))).all()
    )
    matches = [
        (partner, normalize_region_text(partner.region))
        for partner in partners
        if region_keyword_matches(location, partner.region)
    ]
    return (
        min(
            matches,
            key=lambda item: (
                -len(item[1]),
                item[0].partner_type != "총판",
                item[0].id,
            ),
        )[0].id
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
