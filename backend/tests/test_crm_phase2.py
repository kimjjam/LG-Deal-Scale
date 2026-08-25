import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Account,
    Assignment,
    Contact,
    Inquiry,
    Interaction,
    Opportunity,
    Product,
    SalesRegion,
    Score,
    Staff,
    Task,
)
from app.routes.accounts import account_data_quality, account_name_candidates
from app.routes.crm import (
    create_activity,
    create_task,
    dashboard,
    get_opportunity,
    list_activities,
    list_opportunities,
    list_tasks,
    list_verified_products,
    replace_opportunity_items,
    update_opportunity,
)
from app.schemas import (
    ActivityCreate,
    ActivityResponse,
    OpportunityItemInput,
    OpportunityItemsReplace,
    OpportunityUpdate,
    TaskCreate,
    seoul_business_date,
    seoul_day_bounds,
)


async def base(session: AsyncSession) -> tuple[Staff, Staff, Staff, Account]:
    manager = Staff(
        id=uuid.uuid4(),
        name="관리자",
        email=f"m-{uuid.uuid4()}@test.dev",
        hashed_password="x",
        role="manager",
    )
    rep = Staff(
        id=uuid.uuid4(),
        name="담당자",
        email=f"r-{uuid.uuid4()}@test.dev",
        hashed_password="x",
        role="rep",
    )
    other = Staff(
        id=uuid.uuid4(),
        name="다른 담당자",
        email=f"o-{uuid.uuid4()}@test.dev",
        hashed_password="x",
        role="rep",
    )
    account = Account(
        name="가상 호텔",
        phone=str(uuid.uuid4().int)[:11],
        attributes={"location": "서울강남구"},
    )
    session.add_all([manager, rep, other, account])
    await session.flush()
    session.add(SalesRegion(region_name="서울", match_keyword="서울", manager_id=manager.id))
    await session.commit()
    return manager, rep, other, account


@pytest.mark.asyncio
async def test_today_filter_and_monthly_forecast(session: AsyncSession) -> None:
    manager, rep, _, account = await base(session)
    start, end = seoul_day_bounds()
    session.add_all(
        [
            Task(
                account_id=account.id,
                assignee_id=rep.id,
                title="오늘",
                due_at=start + timedelta(hours=1),
            ),
            Task(
                account_id=account.id,
                assignee_id=rep.id,
                title="내일",
                due_at=end + timedelta(hours=1),
            ),
            Opportunity(
                account_id=account.id,
                assignee_id=rep.id,
                title="9월",
                amount=Decimal(1000),
                probability=30,
                expected_close_date=date(2026, 9, 2),
                stage="develop",
            ),
            Opportunity(
                account_id=account.id,
                assignee_id=rep.id,
                title="날짜 없음",
                amount=Decimal(500),
                probability=50,
                stage="qualify",
            ),
            Opportunity(
                account_id=account.id,
                assignee_id=rep.id,
                title="종결",
                amount=Decimal(900),
                probability=100,
                expected_close_date=date(2026, 9, 3),
                stage="won",
            ),
        ]
    )
    await session.commit()
    today = await list_tasks(session, rep, due_today=True)
    assert [task.title for task in today] == ["오늘"]
    result = await dashboard(session, manager)
    assert result["tasks"]["due_today"] == 1
    assert result["forecast"] == {
        "months": [{"month": "2026-09", "count": 1, "amount": 1000.0, "weighted_amount": 300.0}],
        "missing_close_date": 1,
    }


@pytest.mark.asyncio
async def test_item_replace_rbac_total_and_price_snapshot(session: AsyncSession) -> None:
    manager, rep, other, account = await base(session)
    product = Product(
        name="검증 냉장고",
        brand="가상",
        category="냉장고",
        price=Decimal(1200),
        product_url="https://example.test",
        price_type="wholesale",
        price_source_url="https://example.test/prices/fridge",
        price_verified_at=seoul_business_date(),
        is_verified=True,
    )
    opportunity = Opportunity(
        account_id=account.id, assignee_id=rep.id, title="객실 교체", amount=None, probability=30
    )
    session.add_all([product, opportunity])
    await session.commit()
    payload = OpportunityItemsReplace(
        expected_updated_at=opportunity.updated_at,
        items=[
            OpportunityItemInput(
                product_id=product.id, product_name="조작 이름", quantity=2, unit_price=1
            )
        ]
    )
    with pytest.raises(HTTPException) as denied:
        await replace_opportunity_items(opportunity.id, payload, session, other)
    assert denied.value.status_code == 403
    updated = await replace_opportunity_items(opportunity.id, payload, session, manager)
    assert updated.amount == Decimal(2400)
    assert updated.items[0].product_name == "검증 냉장고"
    assert updated.items[0].unit_price == Decimal(1200)
    product.price = Decimal(2000)
    await session.commit()
    assert updated.items[0].unit_price == Decimal(1200)
    saved_item = updated.items[0]
    updated = await update_opportunity(
        opportunity.id,
        OpportunityUpdate(
            expected_updated_at=updated.updated_at,
            title="객실 교체 수정",
            amount=1,
            items=[
                OpportunityItemInput(
                    id=saved_item.id,
                    product_id=product.id,
                    product_name=product.name,
                    quantity=3,
                    unit_price=product.price,
                )
            ],
        ),
        session,
        manager,
    )
    assert updated.title == "객실 교체 수정"
    assert updated.amount == Decimal(3600)
    assert updated.items[0].id == saved_item.id
    assert updated.items[0].unit_price == Decimal(1200)
    reselected = await update_opportunity(
        opportunity.id,
        OpportunityUpdate(
            expected_updated_at=updated.updated_at,
            items=[
                OpportunityItemInput(
                    product_id=product.id,
                    product_name=product.name,
                    quantity=3,
                    unit_price=1,
                )
            ]
        ),
        session,
        manager,
    )
    assert reselected.amount == Decimal(6000)
    assert reselected.items[0].unit_price == Decimal(2000)

    manual = reselected.items[0]
    manual.product_id = None
    manual.product_name = "수동 제품"
    manual.unit_price = Decimal(100)
    await session.commit()
    edited = await replace_opportunity_items(
        opportunity.id,
        OpportunityItemsReplace(
            expected_updated_at=reselected.updated_at,
            items=[
                OpportunityItemInput(
                    id=manual.id,
                    product_name="수동 제품 수정",
                    quantity=4,
                    unit_price=Decimal(250),
                )
            ],
        ),
        session,
        manager,
    )
    assert edited.items[0].product_name == "수동 제품 수정"
    assert edited.items[0].unit_price == Decimal(250)
    assert edited.amount == Decimal(1000)


@pytest.mark.asyncio
async def test_catalog_and_selection_require_current_trusted_business_price(
    session: AsyncSession,
) -> None:
    manager, rep, _, account = await base(session)
    current = seoul_business_date()
    fresh = Product(
        name="최신 사업자가",
        brand="가상",
        category="냉장고",
        price=Decimal(1200),
        price_type="wholesale",
        price_source_url="https://example.test/prices/fresh",
        price_verified_at=current,
        product_url="https://example.test/products/fresh",
        is_verified=True,
    )
    stale = Product(
        name="만료된 사업자가",
        brand="가상",
        category="냉장고",
        price=Decimal(900),
        price_type="wholesale",
        price_source_url="https://example.test/prices/stale",
        price_verified_at=current - timedelta(days=31),
        product_url="https://example.test/products/stale",
        is_verified=True,
    )
    opportunity = Opportunity(
        account_id=account.id, assignee_id=rep.id, title="가격 검증", probability=10
    )
    session.add_all([fresh, stale, opportunity])
    await session.commit()

    assert [product.id for product in await list_verified_products(session, manager)] == [
        fresh.id
    ]
    with pytest.raises(HTTPException, match="사업자 가격"):
        await replace_opportunity_items(
            opportunity.id,
            OpportunityItemsReplace(
                expected_updated_at=opportunity.updated_at,
                items=[
                    OpportunityItemInput(
                        product_id=stale.id,
                        product_name=stale.name,
                        quantity=1,
                        unit_price=stale.price,
                    )
                ],
            ),
            session,
            manager,
        )
    updated = await replace_opportunity_items(
        opportunity.id,
        OpportunityItemsReplace(
            expected_updated_at=opportunity.updated_at,
            items=[
                OpportunityItemInput(
                    product_name="수동 견적 항목", quantity=1, unit_price=500
                )
            ],
        ),
        session,
        manager,
    )
    assert updated.items[0].product_id is None
    assert updated.amount == Decimal(500)


@pytest.mark.asyncio
async def test_opportunity_patch_rejects_stale_token_and_advances_item_only_update(
    session: AsyncSession,
) -> None:
    manager, rep, _, account = await base(session)
    opportunity = Opportunity(
        account_id=account.id, assignee_id=rep.id, title="동시성 확인", probability=10
    )
    session.add(opportunity)
    await session.commit()
    await replace_opportunity_items(
        opportunity.id,
        OpportunityItemsReplace(
            expected_updated_at=opportunity.updated_at,
            items=[
                OpportunityItemInput(product_name="A", quantity=1, unit_price=100),
                OpportunityItemInput(product_name="B", quantity=2, unit_price=100),
            ]
        ),
        session,
        manager,
    )
    first_token = opportunity.updated_at
    same_total = await replace_opportunity_items(
        opportunity.id,
        OpportunityItemsReplace(
            expected_updated_at=first_token,
            items=[
                OpportunityItemInput(
                    id=opportunity.items[0].id,
                    product_name="A",
                    quantity=2,
                    unit_price=100,
                ),
                OpportunityItemInput(
                    id=opportunity.items[1].id,
                    product_name="B",
                    quantity=1,
                    unit_price=100,
                ),
            ],
        ),
        session,
        manager,
    )
    assert same_total.amount == Decimal(300)
    assert same_total.updated_at > first_token
    with pytest.raises(HTTPException) as stale_items:
        await replace_opportunity_items(
            opportunity.id,
            OpportunityItemsReplace(expected_updated_at=first_token, items=[]),
            session,
            manager,
        )
    assert stale_items.value.status_code == 409
    stale_token = opportunity.updated_at
    fresh = await update_opportunity(
        opportunity.id,
        OpportunityUpdate(expected_updated_at=stale_token, title="최신 수정"),
        session,
        manager,
    )
    fresh_token = fresh.updated_at
    fresh_amount = fresh.amount
    with pytest.raises(HTTPException) as stale:
        await update_opportunity(
            opportunity.id,
            OpportunityUpdate(expected_updated_at=stale_token, title="느린 수정"),
            session,
            manager,
        )
    assert stale.value.status_code == 409
    item_only = await update_opportunity(
        opportunity.id,
        OpportunityUpdate(
            expected_updated_at=fresh_token,
            items=[
                OpportunityItemInput(
                    id=fresh.items[0].id, product_name="A", quantity=2, unit_price=100
                ),
                OpportunityItemInput(
                    id=fresh.items[1].id, product_name="B", quantity=1, unit_price=100
                ),
            ],
        ),
        session,
        manager,
    )
    assert item_only.amount == fresh_amount
    assert item_only.updated_at > fresh_token


@pytest.mark.asyncio
async def test_rep_opportunity_reads_are_assignee_owned_on_shared_account(
    session: AsyncSession,
) -> None:
    manager, rep, other, account = await base(session)
    own = Opportunity(
        account_id=account.id, assignee_id=rep.id, title="내 딜", probability=10
    )
    foreign = Opportunity(
        account_id=account.id, assignee_id=other.id, title="타인 딜", probability=10
    )
    session.add_all([own, foreign])
    await session.commit()

    rep_rows = await list_opportunities(session, rep)
    assert [row.id for row in rep_rows] == [own.id]
    assert (await get_opportunity(own.id, session, rep)).id == own.id
    with pytest.raises(HTTPException) as denied:
        await get_opportunity(foreign.id, session, rep)
    assert denied.value.status_code == 403
    assert {row.id for row in await list_opportunities(session, manager)} == {own.id, foreign.id}


@pytest.mark.asyncio
async def test_rep_activity_and_task_links_use_record_ownership_on_shared_account(
    session: AsyncSession,
) -> None:
    manager, rep, other, account = await base(session)
    own = Opportunity(account_id=account.id, assignee_id=rep.id, title="내 딜", probability=10)
    foreign = Opportunity(
        account_id=account.id, assignee_id=other.id, title="타인 딜", probability=10
    )
    session.add_all([own, foreign])
    await session.commit()

    own_activity = await create_activity(
        ActivityCreate(
            account_id=account.id,
            opportunity_id=own.id,
            type="note",
            content="내 활동",
        ),
        session,
        rep,
    )
    await create_activity(
        ActivityCreate(
            account_id=account.id,
            opportunity_id=foreign.id,
            type="note",
            content="타인 활동",
        ),
        session,
        manager,
    )
    own_task = await create_task(
        TaskCreate(
            account_id=account.id,
            assignee_id=rep.id,
            opportunity_id=own.id,
            title="내 할 일",
            due_at=datetime.now(timezone.utc) + timedelta(days=1),
        ),
        session,
        rep,
    )
    await create_task(
        TaskCreate(
            account_id=account.id,
            assignee_id=other.id,
            opportunity_id=foreign.id,
            title="타인 할 일",
            due_at=datetime.now(timezone.utc) + timedelta(days=1),
        ),
        session,
        manager,
    )

    with pytest.raises(HTTPException) as activity_denied:
        await create_activity(
            ActivityCreate(
                account_id=account.id,
                opportunity_id=foreign.id,
                type="note",
                content="침범",
            ),
            session,
            rep,
        )
    assert activity_denied.value.status_code == 403
    with pytest.raises(HTTPException) as task_denied:
        await create_task(
            TaskCreate(
                account_id=account.id,
                assignee_id=rep.id,
                opportunity_id=foreign.id,
                title="침범",
                due_at=datetime.now(timezone.utc) + timedelta(days=1),
            ),
            session,
            rep,
        )
    assert task_denied.value.status_code == 403

    assert [row.id for row in await list_activities(session, rep)] == [own_activity.id]
    assert [row.id for row in await list_tasks(session, rep, scope="all")] == [own_task.id]


@pytest.mark.asyncio
async def test_opportunity_update_rolls_back_metadata_when_items_are_invalid(
    session: AsyncSession,
) -> None:
    manager, rep, _, account = await base(session)
    opportunity = Opportunity(
        account_id=account.id, assignee_id=rep.id, title="원래 제목", probability=10
    )
    session.add(opportunity)
    await session.commit()
    with pytest.raises(HTTPException) as invalid:
        await update_opportunity(
            opportunity.id,
            OpportunityUpdate(
                expected_updated_at=opportunity.updated_at,
                title="저장되면 안 됨",
                items=[
                    OpportunityItemInput(
                        product_id=999999,
                        product_name="없는 제품",
                        quantity=1,
                        unit_price=100,
                    )
                ],
            ),
            session,
            manager,
        )
    assert invalid.value.status_code == 422
    opportunity_id = opportunity.id
    session.expire(opportunity)
    assert (await session.get(Opportunity, opportunity_id)).title == "원래 제목"


@pytest.mark.asyncio
async def test_deleting_last_item_preserves_submitted_manual_amount(
    session: AsyncSession,
) -> None:
    manager, rep, _, account = await base(session)
    opportunity = Opportunity(
        account_id=account.id, assignee_id=rep.id, title="수동 금액 전환", probability=10
    )
    session.add(opportunity)
    await session.commit()
    await replace_opportunity_items(
        opportunity.id,
        OpportunityItemsReplace(
            expected_updated_at=opportunity.updated_at,
            items=[
                OpportunityItemInput(
                    product_name="직접 입력 제품", quantity=1, unit_price=1000
                )
            ]
        ),
        session,
        manager,
    )

    updated = await update_opportunity(
        opportunity.id,
        OpportunityUpdate(expected_updated_at=opportunity.updated_at, items=[], amount=2500),
        session,
        manager,
    )
    assert updated.items == []
    assert updated.amount == Decimal(2500)


@pytest.mark.asyncio
async def test_safe_duplicate_warnings(session: AsyncSession) -> None:
    manager, _, _, account = await base(session)
    second = Account(
        name="가상호텔",
        phone=str(uuid.uuid4().int)[:11],
        attributes={"location": "서울강남구"},
    )
    session.add(second)
    session.add_all(
        [
            Contact(
                account_id=account.id, name="A", phone="01012345678", email="same@example.test"
            ),
            Contact(
                account_id=account.id, name="B", phone="01012345678", email="same@example.test"
            ),
        ]
    )
    await session.commit()
    candidates = await account_name_candidates(" 가상  호텔 ", session, manager)
    assert {row["id"] for row in candidates} == {account.id, second.id}
    warning = await account_data_quality(account.id, session, manager)
    assert {row["field"] for row in warning["duplicate_contacts"]} == {"phone", "email"}


def test_call_and_email_require_nonblank_content() -> None:
    with pytest.raises(ValueError):
        ActivityCreate(account_id=1, type="call", content="  ")
    assert ActivityCreate(account_id=1, type="note", content=None).content is None
    legacy = Interaction(account_id=1, type="call", content=None)
    legacy.id = 1
    legacy.created_at = datetime.now(timezone.utc)
    assert ActivityResponse.model_validate(legacy).content is None


@pytest.mark.asyncio
async def test_rep_dashboard_uses_record_owners_within_shared_account(
    session: AsyncSession,
) -> None:
    _, rep, other, account = await base(session)
    own_inquiry = Inquiry(account_id=account.id, channel="web", content="내 문의")
    other_inquiry = Inquiry(account_id=account.id, channel="web", content="다른 문의")
    session.add_all([own_inquiry, other_inquiry])
    await session.flush()
    session.add_all(
        [
            Assignment(inquiry_id=own_inquiry.id, assignee_id=rep.id, method="round_robin"),
            Assignment(inquiry_id=other_inquiry.id, assignee_id=other.id, method="round_robin"),
            Score(
                inquiry_id=own_inquiry.id,
                fit_score=80,
                intent_score=80,
                intent_category="구매임박",
                intent_confidence=0.9,
                recency_score=60,
                total_score=80,
                reasoning={},
                scoring_version="v1",
                llm_provider="test",
                model_name="test",
            ),
            Score(
                inquiry_id=other_inquiry.id,
                fit_score=20,
                intent_score=20,
                intent_category="정보탐색",
                intent_confidence=0.9,
                recency_score=20,
                total_score=20,
                reasoning={},
                scoring_version="v1",
                llm_provider="test",
                model_name="test",
            ),
            Opportunity(
                account_id=account.id,
                inquiry_id=own_inquiry.id,
                assignee_id=rep.id,
                title="내 딜",
                amount=Decimal(100),
                probability=50,
                expected_close_date=date(2026, 9, 1),
            ),
            Opportunity(
                account_id=account.id,
                inquiry_id=other_inquiry.id,
                assignee_id=other.id,
                title="다른 딜",
                amount=Decimal(900),
                probability=50,
                expected_close_date=date(2026, 9, 1),
            ),
            Task(
                account_id=account.id,
                assignee_id=rep.id,
                title="내 할 일",
                due_at=datetime.now(timezone.utc),
            ),
            Task(
                account_id=account.id,
                assignee_id=other.id,
                title="다른 할 일",
                due_at=datetime.now(timezone.utc),
            ),
            Interaction(account_id=account.id, staff_id=rep.id, type="note", content="내 활동"),
            Interaction(
                account_id=account.id, staff_id=other.id, type="note", content="다른 활동"
            ),
        ]
    )
    await session.commit()

    result = await dashboard(session, rep)
    assert result["pipeline"]["qualify"] == {"count": 1, "amount": 100.0}
    assert result["forecast"]["months"][0]["amount"] == 100.0
    assert result["tasks"]["open"] == 1
    assert result["rep_stats"][0]["activity_count"] == 1
    assert next(row for row in result["ai_score_buckets"] if row["range"] == "80-100")[
        "scored_inquiries"
    ] == 1
    assert next(row for row in result["ai_score_buckets"] if row["range"] == "0-39")[
        "scored_inquiries"
    ] == 0
