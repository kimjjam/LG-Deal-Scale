from datetime import datetime, timezone
from decimal import Decimal
from itertools import pairwise
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import record_audit
from app.database import get_session
from app.models import (
    Account,
    Assignment,
    Contact,
    Inquiry,
    Interaction,
    Opportunity,
    OpportunityStageHistory,
    Score,
    Staff,
    Task,
)
from app.routes.accounts import active_account
from app.schemas import (
    ActivityCreate,
    ActivityResponse,
    OpportunityCreate,
    OpportunityResponse,
    OpportunityUpdate,
    TaskCreate,
    TaskResponse,
    TaskUpdate,
)
from app.security import (
    CurrentStaff,
    accessible_account_ids,
    require_account_access,
)

router = APIRouter(prefix="/api/crm", tags=["crm"])
Session = Annotated[AsyncSession, Depends(get_session)]
PageLimit = Annotated[int, Query(ge=1, le=200)]
PageOffset = Annotated[int, Query(ge=0)]
STAGE_PROBABILITIES = {
    "qualify": 0.10,
    "develop": 0.30,
    "propose": 0.60,
    "won": 1.00,
    "lost": 0.00,
}


async def _active_rep(session: AsyncSession, staff_id: object) -> Staff:
    staff = await session.scalar(
        select(Staff).where(Staff.id == staff_id, Staff.is_active.is_(True), Staff.role == "rep")
    )
    if not staff:
        raise HTTPException(status_code=404, detail="활성 영업 담당자를 찾을 수 없습니다.")
    return staff


def _require_owner_or_manager(record_assignee_id: object, staff: Staff) -> None:
    if staff.role == "rep" and record_assignee_id != staff.id:
        raise HTTPException(status_code=403, detail="담당자만 변경할 수 있습니다.")


async def _validate_links(
    session: AsyncSession,
    account_id: int,
    staff: Staff,
    *,
    contact_id: int | None = None,
    inquiry_id: int | None = None,
    opportunity_id: int | None = None,
) -> None:
    await active_account(session, account_id)
    await require_account_access(session, account_id, staff)
    if inquiry_id is not None and staff.role == "rep":
        latest_assignee = await session.scalar(
            select(Assignment.assignee_id)
            .where(Assignment.inquiry_id == inquiry_id)
            .order_by(Assignment.assigned_at.desc(), Assignment.id.desc())
            .limit(1)
        )
        if latest_assignee != staff.id:
            raise HTTPException(status_code=403, detail="문의 담당자만 변경할 수 있습니다.")
    checks = (
        (Contact, contact_id, Contact.account_id, Contact.deleted_at.is_(None), "담당자"),
        (Inquiry, inquiry_id, Inquiry.account_id, None, "문의"),
        (Opportunity, opportunity_id, Opportunity.account_id, None, "영업기회"),
    )
    for model, record_id, owner_column, active_filter, label in checks:
        if record_id is None:
            continue
        conditions = [model.id == record_id, owner_column == account_id]
        if active_filter is not None:
            conditions.append(active_filter)
        if not await session.scalar(select(model.id).where(*conditions)):
            raise HTTPException(
                status_code=404, detail=f"같은 고객사의 {label}를 찾을 수 없습니다."
            )


async def _opportunity(
    session: AsyncSession,
    opportunity_id: int,
    staff: Staff,
    *,
    for_update: bool = False,
) -> Opportunity:
    statement = select(Opportunity).where(Opportunity.id == opportunity_id)
    if for_update:
        statement = statement.with_for_update()
    opportunity = await session.scalar(statement)
    if not opportunity:
        raise HTTPException(status_code=404, detail="영업기회를 찾을 수 없습니다.")
    await active_account(session, opportunity.account_id)
    await require_account_access(session, opportunity.account_id, staff)
    return opportunity


@router.get("/dashboard")
async def dashboard(session: Session, staff: CurrentStaff) -> dict[str, object]:
    account_scope = accessible_account_ids(staff.id) if staff.role == "rep" else None
    opportunity_query = select(Opportunity).join(Account).where(Account.deleted_at.is_(None))
    task_query = select(Task).join(Account).where(Account.deleted_at.is_(None))
    activity_query = select(Interaction).join(Account).where(Account.deleted_at.is_(None))
    history_query = (
        select(OpportunityStageHistory)
        .join(Opportunity)
        .join(Account)
        .where(Account.deleted_at.is_(None))
    )
    score_query = (
        select(Score.total_score, Opportunity.stage)
        .join(Inquiry, Inquiry.id == Score.inquiry_id)
        .join(Account, Account.id == Inquiry.account_id)
        .outerjoin(Opportunity, Opportunity.inquiry_id == Inquiry.id)
        .where(Account.deleted_at.is_(None))
    )
    if account_scope is not None:
        opportunity_query = opportunity_query.where(Opportunity.account_id.in_(account_scope))
        task_query = task_query.where(Task.account_id.in_(account_scope))
        activity_query = activity_query.where(Interaction.account_id.in_(account_scope))
        history_query = history_query.where(Opportunity.account_id.in_(account_scope))
        score_query = score_query.where(Inquiry.account_id.in_(account_scope))
    opportunities = list((await session.scalars(opportunity_query)).all())
    tasks = list((await session.scalars(task_query)).all())
    activities = list((await session.scalars(activity_query)).all())
    histories = list(
        (
            await session.scalars(
                history_query.order_by(
                    OpportunityStageHistory.opportunity_id,
                    OpportunityStageHistory.changed_at,
                    OpportunityStageHistory.id,
                )
            )
        ).all()
    )
    score_rows = (await session.execute(score_query)).all()

    pipeline = {stage: {"count": 0, "amount": 0.0} for stage in STAGE_PROBABILITIES}
    weighted_amount = Decimal(0)
    for opportunity in opportunities:
        amount = float(opportunity.amount or 0)
        pipeline[opportunity.stage]["count"] += 1
        pipeline[opportunity.stage]["amount"] += amount
        weighted_amount += (opportunity.amount or Decimal(0)) * opportunity.probability / 100
    won = pipeline["won"]["count"]
    lost = pipeline["lost"]["count"]
    closed = won + lost
    now = datetime.now(timezone.utc)

    rep_query = select(Staff).where(Staff.role == "rep", Staff.is_active.is_(True))
    if staff.role == "rep":
        rep_query = rep_query.where(Staff.id == staff.id)
    rep_rows = list((await session.scalars(rep_query)).all())
    rep_stats = []
    for rep in rep_rows:
        rep_opportunities = [item for item in opportunities if item.assignee_id == rep.id]
        rep_stats.append(
            {
                "staff_id": str(rep.id),
                "name": rep.name,
                "activity_count": sum(item.staff_id == rep.id for item in activities),
                "opportunity_count": len(rep_opportunities),
                "won_count": sum(item.stage == "won" for item in rep_opportunities),
                "won_amount": sum(
                    float(item.amount or 0) for item in rep_opportunities if item.stage == "won"
                ),
            }
        )

    stage_seconds: dict[str, list[float]] = {stage: [] for stage in STAGE_PROBABILITIES}
    by_opportunity: dict[int, list[OpportunityStageHistory]] = {}
    for history in histories:
        by_opportunity.setdefault(history.opportunity_id, []).append(history)
    for items in by_opportunity.values():
        for current, following in pairwise(items):
            stage_seconds[current.stage].append(
                (
                    following.changed_at.replace(
                        tzinfo=following.changed_at.tzinfo or timezone.utc
                    )
                    - current.changed_at.replace(tzinfo=current.changed_at.tzinfo or timezone.utc)
                ).total_seconds()
            )
        current = items[-1]
        stage_seconds[current.stage].append(
            (
                now
                - current.changed_at.replace(tzinfo=current.changed_at.tzinfo or timezone.utc)
            ).total_seconds()
        )
    for opportunity in opportunities:
        if opportunity.id not in by_opportunity:
            stage_seconds[opportunity.stage].append(
                (
                    now
                    - opportunity.created_at.replace(
                        tzinfo=opportunity.created_at.tzinfo or timezone.utc
                    )
                ).total_seconds()
            )

    bucket_specs = (
        (0, 40, "0-39"),
        (40, 60, "40-59"),
        (60, 80, "60-79"),
        (80, 101, "80-100"),
    )
    score_buckets = []
    for lower, upper, label in bucket_specs:
        rows = [row for row in score_rows if lower <= row.total_score < upper]
        bucket_won = sum(row.stage == "won" for row in rows)
        bucket_closed = sum(row.stage in {"won", "lost"} for row in rows)
        score_buckets.append(
            {
                "range": label,
                "scored_inquiries": len(rows),
                "closed_opportunities": bucket_closed,
                "won_opportunities": bucket_won,
                "won_conversion": round(bucket_won / bucket_closed, 4) if bucket_closed else None,
            }
        )
    return {
        "pipeline": pipeline,
        "weighted_amount": float(round(weighted_amount, 2)),
        "stage_probabilities": STAGE_PROBABILITIES,
        "closed_conversion": {
            "won": won,
            "lost": lost,
            "denominator": closed,
            "rate": round(won / closed, 4) if closed else None,
            "definition": "won / (won + lost)",
        },
        "tasks": {
            "open": sum(item.status == "pending" for item in tasks),
            "overdue": sum(
                item.status == "pending"
                and item.due_at.replace(tzinfo=item.due_at.tzinfo or timezone.utc) < now
                for item in tasks
            ),
        },
        "rep_stats": rep_stats,
        "average_stage_hours": {
            stage: round(sum(values) / len(values) / 3600, 2) if values else None
            for stage, values in stage_seconds.items()
        },
        "ai_score_buckets": score_buckets,
    }


@router.get("/opportunities", response_model=list[OpportunityResponse])
async def list_opportunities(
    session: Session,
    staff: CurrentStaff,
    q: str | None = None,
    stage: Literal["qualify", "develop", "propose", "won", "lost"] | None = None,
    assignee_id: UUID | None = None,
    account_id: int | None = None,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
) -> list[Opportunity]:
    statement = (
        select(Opportunity)
        .join(Account, Account.id == Opportunity.account_id)
        .where(Account.deleted_at.is_(None))
    )
    if staff.role == "rep":
        statement = statement.where(Opportunity.account_id.in_(accessible_account_ids(staff.id)))
    if q:
        statement = statement.where(
            or_(Opportunity.title.ilike(f"%{q}%"), Account.name.ilike(f"%{q}%"))
        )
    if stage:
        statement = statement.where(Opportunity.stage == stage)
    if assignee_id:
        statement = statement.where(Opportunity.assignee_id == assignee_id)
    if account_id:
        statement = statement.where(Opportunity.account_id == account_id)
    return list(
        (
            await session.scalars(
                statement.order_by(Opportunity.updated_at.desc()).limit(limit).offset(offset)
            )
        ).all()
    )


@router.post(
    "/opportunities",
    response_model=OpportunityResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_opportunity(
    payload: OpportunityCreate, session: Session, staff: CurrentStaff
) -> Opportunity:
    if payload.stage == "lost" and not payload.loss_reason:
        raise HTTPException(status_code=422, detail="실주 사유가 필요합니다.")
    await _active_rep(session, payload.assignee_id)
    _require_owner_or_manager(payload.assignee_id, staff)
    if payload.inquiry_id is not None:
        raise HTTPException(status_code=422, detail="문의 전환 API를 사용해주세요.")
    await _validate_links(
        session,
        payload.account_id,
        staff,
    )
    if payload.lead_id is not None:
        raise HTTPException(status_code=422, detail="리드 전환 API를 사용해주세요.")
    opportunity = Opportunity(**payload.model_dump())
    session.add(opportunity)
    await session.flush()
    session.add(
        OpportunityStageHistory(
            opportunity_id=opportunity.id, stage=opportunity.stage, changed_by=staff.id
        )
    )
    record_audit(session, staff, "opportunity.create", "opportunity", opportunity.id)
    await session.commit()
    await session.refresh(opportunity)
    return opportunity


@router.get("/opportunities/{opportunity_id}", response_model=OpportunityResponse)
async def get_opportunity(
    opportunity_id: int, session: Session, staff: CurrentStaff
) -> Opportunity:
    return await _opportunity(session, opportunity_id, staff)


@router.patch("/opportunities/{opportunity_id}", response_model=OpportunityResponse)
async def update_opportunity(
    opportunity_id: int,
    payload: OpportunityUpdate,
    session: Session,
    staff: CurrentStaff,
) -> Opportunity:
    opportunity = await _opportunity(session, opportunity_id, staff, for_update=True)
    _require_owner_or_manager(opportunity.assignee_id, staff)
    changes = payload.model_dump(exclude_unset=True)
    new_stage = changes.get("stage", opportunity.stage)
    new_loss_reason = changes.get("loss_reason", opportunity.loss_reason)
    allowed_transitions = {
        "qualify": {"develop", "propose", "won", "lost"},
        "develop": {"propose", "won", "lost"},
        "propose": {"won", "lost"},
        "won": set(),
        "lost": set(),
    }
    if new_stage != opportunity.stage and new_stage not in allowed_transitions[opportunity.stage]:
        raise HTTPException(status_code=409, detail="허용되지 않는 영업기회 단계 변경입니다.")
    if new_stage == "lost" and not new_loss_reason:
        raise HTTPException(status_code=422, detail="실주 사유가 필요합니다.")
    if new_stage != "lost":
        changes["loss_reason"] = None
    if "assignee_id" in changes:
        await _active_rep(session, changes["assignee_id"])
        if staff.role == "rep" and changes["assignee_id"] != staff.id:
            raise HTTPException(status_code=403, detail="본인에게만 배정할 수 있습니다.")
    old_stage = opportunity.stage
    for key, value in changes.items():
        setattr(opportunity, key, value)
    if old_stage != opportunity.stage:
        session.add(
            OpportunityStageHistory(
                opportunity_id=opportunity.id,
                stage=opportunity.stage,
                changed_by=staff.id,
            )
        )
    record_audit(
        session,
        staff,
        "opportunity.update",
        "opportunity",
        opportunity.id,
        {"stage_from": old_stage, "stage_to": opportunity.stage},
    )
    await session.commit()
    await session.refresh(opportunity)
    return opportunity


@router.get("/activities", response_model=list[ActivityResponse])
async def list_activities(
    session: Session,
    staff: CurrentStaff,
    account_id: int | None = None,
    inquiry_id: int | None = None,
    opportunity_id: int | None = None,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
) -> list[Interaction]:
    statement = select(Interaction).join(Account).where(Account.deleted_at.is_(None))
    if staff.role == "rep":
        statement = statement.where(Interaction.account_id.in_(accessible_account_ids(staff.id)))
    if account_id:
        statement = statement.where(Interaction.account_id == account_id)
    if inquiry_id:
        statement = statement.where(Interaction.inquiry_id == inquiry_id)
    if opportunity_id:
        statement = statement.where(Interaction.opportunity_id == opportunity_id)
    return list(
        (
            await session.scalars(
                statement.order_by(Interaction.created_at.desc()).limit(limit).offset(offset)
            )
        ).all()
    )


@router.post("/activities", response_model=ActivityResponse, status_code=status.HTTP_201_CREATED)
async def create_activity(
    payload: ActivityCreate, session: Session, staff: CurrentStaff
) -> Interaction:
    await _validate_links(
        session,
        payload.account_id,
        staff,
        contact_id=payload.contact_id,
        inquiry_id=payload.inquiry_id,
        opportunity_id=payload.opportunity_id,
    )
    values = payload.model_dump(exclude={"staff_id"})
    activity = Interaction(**values, staff_id=staff.id)
    session.add(activity)
    await session.flush()
    record_audit(session, staff, "activity.create", "interaction", activity.id)
    await session.commit()
    await session.refresh(activity)
    return activity


async def _task(session: AsyncSession, task_id: int, staff: Staff) -> Task:
    task = await session.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="할 일을 찾을 수 없습니다.")
    await active_account(session, task.account_id)
    await require_account_access(session, task.account_id, staff)
    return task


@router.get("/tasks", response_model=list[TaskResponse])
async def list_tasks(
    session: Session,
    staff: CurrentStaff,
    scope: Literal["mine", "all"] = "mine",
    q: str | None = None,
    task_status: Annotated[Literal["pending", "completed"] | None, Query(alias="status")] = None,
    assignee_id: UUID | None = None,
    overdue: bool | None = None,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
) -> list[Task]:
    statement = select(Task).join(Account).where(Account.deleted_at.is_(None))
    if staff.role == "rep":
        statement = statement.where(Task.account_id.in_(accessible_account_ids(staff.id)))
    if scope == "mine":
        statement = statement.where(Task.assignee_id == staff.id)
    if q:
        statement = statement.where(Task.title.ilike(f"%{q}%"))
    if task_status:
        statement = statement.where(Task.status == task_status)
    if assignee_id:
        statement = statement.where(Task.assignee_id == assignee_id)
    if overdue is not None:
        overdue_condition = Task.due_at < datetime.now(timezone.utc)
        statement = statement.where(overdue_condition if overdue else ~overdue_condition)
    return list(
        (
            await session.scalars(statement.order_by(Task.due_at.asc()).limit(limit).offset(offset))
        ).all()
    )


@router.post("/tasks", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(payload: TaskCreate, session: Session, staff: CurrentStaff) -> Task:
    await _active_rep(session, payload.assignee_id)
    _require_owner_or_manager(payload.assignee_id, staff)
    await _validate_links(
        session,
        payload.account_id,
        staff,
        inquiry_id=payload.inquiry_id,
        opportunity_id=payload.opportunity_id,
    )
    task = Task(**payload.model_dump())
    session.add(task)
    await session.flush()
    record_audit(session, staff, "task.create", "task", task.id)
    await session.commit()
    await session.refresh(task)
    return task


@router.patch("/tasks/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: int, payload: TaskUpdate, session: Session, staff: CurrentStaff
) -> Task:
    task = await _task(session, task_id, staff)
    _require_owner_or_manager(task.assignee_id, staff)
    changes = payload.model_dump(exclude_unset=True)
    if "assignee_id" in changes:
        await _active_rep(session, changes["assignee_id"])
        if staff.role == "rep" and changes["assignee_id"] != staff.id:
            raise HTTPException(status_code=403, detail="본인에게만 배정할 수 있습니다.")
    for key, value in changes.items():
        setattr(task, key, value)
    if "status" in changes:
        task.completed_at = datetime.now(timezone.utc) if task.status == "completed" else None
    record_audit(session, staff, "task.update", "task", task.id, {"fields": sorted(changes)})
    await session.commit()
    await session.refresh(task)
    return task


@router.post("/tasks/{task_id}/complete", response_model=TaskResponse)
async def complete_task(task_id: int, session: Session, staff: CurrentStaff) -> Task:
    task = await _task(session, task_id, staff)
    _require_owner_or_manager(task.assignee_id, staff)
    if task.status != "completed":
        task.status = "completed"
        task.completed_at = datetime.now(timezone.utc)
        record_audit(session, staff, "task.complete", "task", task.id)
        await session.commit()
        await session.refresh(task)
    return task
