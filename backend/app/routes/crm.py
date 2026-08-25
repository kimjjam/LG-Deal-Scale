from datetime import datetime, timedelta, timezone
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
    OpportunityItem,
    OpportunityStageHistory,
    Product,
    Score,
    Staff,
    Task,
)
from app.product_pricing import trusted_business_price
from app.routes.accounts import active_account
from app.schemas import (
    OPPORTUNITY_AMOUNT_MAX,
    ActivityCreate,
    ActivityResponse,
    OpportunityCreate,
    OpportunityItemsReplace,
    OpportunityResponse,
    OpportunityUpdate,
    TaskCreate,
    TaskResponse,
    TaskUpdate,
    VerifiedProductResponse,
    seoul_day_bounds,
)
from app.security import (
    CurrentStaff,
    account_scope_ids,
    require_account_access,
)
from app.services import require_inquiry_access

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
    if inquiry_id is not None:
        inquiry = await session.get(Inquiry, inquiry_id)
        if inquiry and inquiry.account_id == account_id:
            await require_inquiry_access(session, inquiry, staff)
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
    if opportunity_id is not None and staff.role == "rep":
        assignee_id = await session.scalar(
            select(Opportunity.assignee_id).where(
                Opportunity.id == opportunity_id,
                Opportunity.account_id == account_id,
            )
        )
        if assignee_id != staff.id:
            raise HTTPException(status_code=403, detail="영업기회 담당자만 연결할 수 있습니다.")


def _check_expected_updated_at(opportunity: Opportunity, expected_updated_at: datetime) -> None:
    expected = expected_updated_at.replace(
        tzinfo=expected_updated_at.tzinfo or timezone.utc
    ).astimezone(timezone.utc)
    current = opportunity.updated_at.replace(
        tzinfo=opportunity.updated_at.tzinfo or timezone.utc
    ).astimezone(timezone.utc)
    if expected != current:
        raise HTTPException(
            status_code=409,
            detail="다른 사용자가 먼저 수정했습니다. 최신 내용을 다시 불러와주세요.",
        )


def _touch_opportunity(opportunity: Opportunity) -> None:
    current = opportunity.updated_at.replace(
        tzinfo=opportunity.updated_at.tzinfo or timezone.utc
    ).astimezone(timezone.utc)
    opportunity.updated_at = max(datetime.now(timezone.utc), current + timedelta(microseconds=1))


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
    _require_owner_or_manager(opportunity.assignee_id, staff)
    return opportunity


@router.get("/dashboard")
async def dashboard(session: Session, staff: CurrentStaff) -> dict[str, object]:
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
    scoped_ids = await account_scope_ids(session, staff)
    if scoped_ids is not None:
        opportunity_query = opportunity_query.where(Account.id.in_(scoped_ids))
        task_query = task_query.where(Account.id.in_(scoped_ids))
        activity_query = activity_query.where(Account.id.in_(scoped_ids))
        history_query = history_query.where(Account.id.in_(scoped_ids))
        score_query = score_query.where(Account.id.in_(scoped_ids))
    if staff.role == "rep":
        latest_assignee = (
            select(Assignment.assignee_id)
            .where(Assignment.inquiry_id == Inquiry.id)
            .order_by(Assignment.assigned_at.desc(), Assignment.id.desc())
            .limit(1)
            .correlate(Inquiry)
            .scalar_subquery()
        )
        opportunity_query = opportunity_query.where(Opportunity.assignee_id == staff.id)
        task_query = task_query.where(Task.assignee_id == staff.id)
        activity_query = activity_query.where(Interaction.staff_id == staff.id)
        history_query = history_query.where(Opportunity.assignee_id == staff.id)
        score_query = score_query.where(latest_assignee == staff.id)
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
    forecast_by_month: dict[str, dict[str, float | int]] = {}
    missing_close_date = 0
    for opportunity in opportunities:
        amount = float(opportunity.amount or 0)
        pipeline[opportunity.stage]["count"] += 1
        pipeline[opportunity.stage]["amount"] += amount
        weighted_amount += (opportunity.amount or Decimal(0)) * opportunity.probability / 100
        if opportunity.stage not in {"won", "lost"}:
            if opportunity.expected_close_date is None:
                missing_close_date += 1
            else:
                month = opportunity.expected_close_date.strftime("%Y-%m")
                bucket = forecast_by_month.setdefault(
                    month, {"month": month, "count": 0, "amount": 0.0, "weighted_amount": 0.0}
                )
                bucket["count"] += 1
                bucket["amount"] += float(opportunity.amount or 0)
                bucket["weighted_amount"] += float(
                    (opportunity.amount or Decimal(0)) * opportunity.probability / 100
                )
    won = pipeline["won"]["count"]
    lost = pipeline["lost"]["count"]
    closed = won + lost
    now = datetime.now(timezone.utc)
    today_start, tomorrow_start = seoul_day_bounds(now)

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
                    following.changed_at.replace(tzinfo=following.changed_at.tzinfo or timezone.utc)
                    - current.changed_at.replace(tzinfo=current.changed_at.tzinfo or timezone.utc)
                ).total_seconds()
            )
        current = items[-1]
        stage_seconds[current.stage].append(
            (
                now - current.changed_at.replace(tzinfo=current.changed_at.tzinfo or timezone.utc)
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
        "forecast": {
            "months": [
                {
                    **forecast_by_month[month],
                    "amount": round(float(forecast_by_month[month]["amount"]), 2),
                    "weighted_amount": round(float(forecast_by_month[month]["weighted_amount"]), 2),
                }
                for month in sorted(forecast_by_month)
            ],
            "missing_close_date": missing_close_date,
        },
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
            "due_today": sum(
                item.status == "pending"
                and today_start
                <= item.due_at.replace(tzinfo=item.due_at.tzinfo or timezone.utc)
                < tomorrow_start
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
    scoped_ids = await account_scope_ids(session, staff)
    if scoped_ids is not None:
        statement = statement.where(Account.id.in_(scoped_ids))
    if staff.role == "rep":
        statement = statement.where(Opportunity.assignee_id == staff.id)
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


@router.get("/products", response_model=list[VerifiedProductResponse])
async def list_verified_products(session: Session, _staff: CurrentStaff) -> list[Product]:
    products = list(
        (
            await session.scalars(
                select(Product).where(Product.is_verified.is_(True)).order_by(Product.name)
            )
        ).all()
    )
    return [product for product in products if trusted_business_price(product)[0] is not None]


@router.put("/opportunities/{opportunity_id}/items", response_model=OpportunityResponse)
async def replace_opportunity_items(
    opportunity_id: int,
    payload: OpportunityItemsReplace,
    session: Session,
    staff: CurrentStaff,
) -> Opportunity:
    opportunity = await _opportunity(session, opportunity_id, staff, for_update=True)
    _require_owner_or_manager(opportunity.assignee_id, staff)
    _check_expected_updated_at(opportunity, payload.expected_updated_at)
    await _replace_opportunity_items(session, opportunity, payload)
    _touch_opportunity(opportunity)
    record_audit(
        session,
        staff,
        "opportunity.items_replace",
        "opportunity",
        opportunity.id,
        {"item_count": len(payload.items)},
    )
    await session.commit()
    await session.refresh(opportunity, ["items"])
    return opportunity


async def _replace_opportunity_items(
    session: AsyncSession,
    opportunity: Opportunity,
    payload: OpportunityItemsReplace,
) -> None:
    existing = {item.id: item for item in opportunity.items}
    requested_ids = {item.id for item in payload.items if item.id is not None}
    if len(requested_ids) != sum(item.id is not None for item in payload.items):
        raise HTTPException(status_code=422, detail="제품 항목 ID를 중복 사용할 수 없습니다.")
    if requested_ids - existing.keys():
        raise HTTPException(status_code=422, detail="현재 영업기회의 제품 항목만 수정할 수 있습니다.")
    product_ids = {
        item.product_id
        for item in payload.items
        if item.product_id is not None
        and (item.id not in existing or existing[item.id].product_id != item.product_id)
    }
    products = {
        product.id: product
        for product in (
            await session.scalars(
                select(Product).where(Product.id.in_(product_ids), Product.is_verified.is_(True))
            )
        ).all()
        if trusted_business_price(product)[0] is not None
    }
    if len(products) != len(product_ids):
        raise HTTPException(
            status_code=422, detail="현재 검증된 사업자 가격이 있는 제품만 선택할 수 있습니다."
        )
    prepared = []
    total = Decimal(0)
    for item in payload.items:
        retained = existing.get(item.id)
        if retained is not None and retained.product_id == item.product_id and item.product_id is not None:
            product_name, unit_price = retained.product_name, retained.unit_price
        else:
            product = products.get(item.product_id)
            product_name = product.name if product else item.product_name
            unit_price = product.price if product else item.unit_price
        total += unit_price * item.quantity
        prepared.append((item, retained, product_name, unit_price))
    if total > OPPORTUNITY_AMOUNT_MAX:
        raise HTTPException(status_code=422, detail="제품 합계가 영업기회 금액 한도를 초과합니다.")
    rows: list[OpportunityItem] = []
    for item, retained, product_name, unit_price in prepared:
        if retained is None:
            retained = OpportunityItem(
                opportunity_id=opportunity.id,
                product_id=item.product_id,
            )
        retained.product_id = item.product_id
        retained.product_name = product_name
        retained.quantity = item.quantity
        retained.unit_price = unit_price
        rows.append(retained)
    for removed_id in existing.keys() - requested_ids:
        await session.delete(existing[removed_id])
    session.add_all(item for item in rows if item.id is None)
    opportunity.amount = total if rows else None


@router.patch("/opportunities/{opportunity_id}", response_model=OpportunityResponse)
async def update_opportunity(
    opportunity_id: int,
    payload: OpportunityUpdate,
    session: Session,
    staff: CurrentStaff,
) -> Opportunity:
    opportunity = await _opportunity(session, opportunity_id, staff, for_update=True)
    _check_expected_updated_at(opportunity, payload.expected_updated_at)
    changes = payload.model_dump(
        exclude_unset=True, exclude={"items", "expected_updated_at"}
    )
    if payload.items:
        changes.pop("amount", None)
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
    if payload.items is not None:
        await _replace_opportunity_items(
            session,
            opportunity,
            OpportunityItemsReplace(
                expected_updated_at=payload.expected_updated_at, items=payload.items
            ),
        )
    old_stage = opportunity.stage
    for key, value in changes.items():
        setattr(opportunity, key, value)
    _touch_opportunity(opportunity)
    if payload.items is None and opportunity.items:
        opportunity.amount = opportunity.items_total
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
        {
            "stage_from": old_stage,
            "stage_to": opportunity.stage,
            "item_count": len(payload.items) if payload.items is not None else None,
        },
    )
    await session.commit()
    await session.refresh(opportunity, ["items"])
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
    scoped_ids = await account_scope_ids(session, staff)
    if scoped_ids is not None:
        statement = statement.where(Account.id.in_(scoped_ids))
    if staff.role == "rep":
        statement = statement.where(Interaction.staff_id == staff.id)
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
    due_today: bool | None = None,
) -> list[Task]:
    statement = select(Task).join(Account).where(Account.deleted_at.is_(None))
    scoped_ids = await account_scope_ids(session, staff)
    if scoped_ids is not None:
        statement = statement.where(Account.id.in_(scoped_ids))
    if staff.role == "rep":
        statement = statement.where(Task.assignee_id == staff.id)
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
    if due_today is not None:
        today_start, tomorrow_start = seoul_day_bounds()
        due_today_condition = Task.due_at >= today_start
        due_today_condition &= Task.due_at < tomorrow_start
        statement = statement.where(due_today_condition if due_today else ~due_today_condition)
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
