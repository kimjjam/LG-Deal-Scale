import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.requests import Request

from app.database import Base, get_session
from app.models import Account, Assignment, Inquiry, Partner, SalesRegion, Staff
from app.routes import public
from app.routes.crm import create_activity
from app.routes.inquiries import claim, inbox, link_partner, retry_score
from app.routes.inquiries import router as inquiries_router
from app.routes.partners import list_partners
from app.routes.partners import router as partners_router
from app.routes.staff import update_staff_active, update_staff_role
from app.schemas import (
    ActivityCreate,
    ChatMessage,
    IntakeFields,
    PartnerCreate,
    PartnerLinkRequest,
    PublicSubmissionRequest,
    SalesRegionCreate,
    StaffActiveUpdate,
    StaffRoleUpdate,
    normalize_region_text,
    seoul_business_date,
)
from app.security import get_current_staff
from app.services import (
    claim_inquiry,
    create_inquiry,
    curated_partner_id,
    manually_assign,
    regional_manager_id,
)


def staff(role: str, name: str, active: bool = True) -> Staff:
    return Staff(
        id=uuid.uuid4(),
        name=name,
        email=f"{uuid.uuid4()}@example.test",
        hashed_password="not-used",
        role=role,
        is_active=active,
    )


@pytest.mark.asyncio
async def test_create_stays_open_and_unassigned_then_claim_is_single_winner(
    session: AsyncSession,
) -> None:
    rep = staff("rep", "담당자")
    account = Account(name="가상호텔", phone="01011112222", attributes={})
    session.add_all([rep, account])
    await session.commit()
    inquiry, _ = await create_inquiry(session, account.id, "web", "문의", None, None)

    assert inquiry.status == "open"
    assert (
        await session.scalar(
            select(func.count()).select_from(Assignment).where(Assignment.inquiry_id == inquiry.id)
        )
        == 0
    )

    result = await claim(inquiry.id, session, rep)
    assert result["status"] == "routed"
    assignment = await session.scalar(select(Assignment).where(Assignment.inquiry_id == inquiry.id))
    assert assignment and assignment.method == "claimed" and assignment.assignee_id == rep.id
    with pytest.raises(HTTPException) as conflict:
        await claim(inquiry.id, session, staff("rep", "다른 담당자"))
    assert conflict.value.status_code == 409


@pytest.mark.asyncio
async def test_claim_has_one_winner_across_independent_sessions(tmp_path: Path) -> None:
    database = tmp_path / "claims.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database.as_posix()}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    rep_one, rep_two = staff("rep", "첫 담당자"), staff("rep", "둘째 담당자")
    async with sessions() as setup:
        account = Account(name="동시성 고객", phone="01012121212", attributes={})
        setup.add_all([rep_one, rep_two, account])
        await setup.flush()
        inquiry = Inquiry(account_id=account.id, channel="web", content="동시 문의")
        setup.add(inquiry)
        await setup.commit()
        inquiry_id = inquiry.id

    async def attempt(rep_id: uuid.UUID) -> str:
        async with sessions() as worker:
            rep = await worker.get(Staff, rep_id)
            assert rep
            try:
                await claim_inquiry(worker, inquiry_id, rep)
                await worker.commit()
                return "won"
            except RuntimeError:
                await worker.rollback()
                return "conflict"

    outcomes = await asyncio.gather(attempt(rep_one.id), attempt(rep_two.id))
    assert sorted(outcomes) == ["conflict", "won"]
    async with sessions() as check:
        assert (
            await check.scalar(
                select(func.count())
                .select_from(Assignment)
                .where(Assignment.inquiry_id == inquiry_id)
            )
            == 1
        )
    await engine.dispose()


@pytest.mark.asyncio
async def test_rep_unassigned_scope_and_manager_manual_assignment(session: AsyncSession) -> None:
    manager, rep = staff("manager", "매니저"), staff("rep", "담당자")
    account = Account(name="가상모텔", phone="01033334444", attributes={})
    session.add_all([manager, rep, account])
    await session.flush()
    inquiry = Inquiry(account_id=account.id, channel="web", content="냉장고 문의")
    session.add(inquiry)
    await session.flush()

    assert [row["id"] for row in await inbox(session, rep, scope="unassigned")] == [inquiry.id]
    assignment = await manually_assign(session, inquiry, rep.id)
    await session.commit()
    assert assignment.method == "manual"
    assert await inbox(session, rep, scope="unassigned") == []
    assert [row["id"] for row in await inbox(session, rep, scope="mine")] == [inquiry.id]


@pytest.mark.asyncio
async def test_rep_can_read_all_but_cannot_filter_foreign_or_mutate_unclaimed_inquiries(
    session: AsyncSession,
) -> None:
    rep, other = staff("rep", "담당자"), staff("rep", "다른 담당자")
    account = Account(name="권한 고객", phone="01044445555", attributes={})
    session.add_all([rep, other, account])
    await session.flush()
    inquiry = Inquiry(
        account_id=account.id,
        channel="web",
        content="문의",
        routing_manager_id=rep.id,
    )
    session.add(inquiry)
    await session.flush()

    assert [row["id"] for row in await inbox(session, rep, scope="all")] == [inquiry.id]
    with pytest.raises(HTTPException) as foreign_filter:
        await inbox(session, rep, scope="mine", assignee_id=other.id)
    assert foreign_filter.value.status_code == 403
    with pytest.raises(HTTPException) as retry:
        await retry_score(inquiry.id, session, rep)
    assert retry.value.status_code == 403


@pytest.mark.asyncio
async def test_owner_and_rep_cannot_use_manager_region_scope(session: AsyncSession) -> None:
    owner, rep = staff("owner", "총관리자"), staff("rep", "담당자")
    session.add_all([owner, rep])
    await session.flush()
    for actor in (owner, rep):
        with pytest.raises(HTTPException) as denied:
            await inbox(session, actor, scope="my_region")
        assert denied.value.status_code == 403


@pytest.mark.asyncio
async def test_account_access_does_not_allow_activity_on_unclaimed_inquiry(
    session: AsyncSession,
) -> None:
    rep = staff("rep", "담당자")
    account = Account(name="같은 고객", phone="01045454545", attributes={})
    session.add_all([rep, account])
    await session.flush()
    owned = Inquiry(account_id=account.id, channel="web", content="내 문의", status="routed")
    unclaimed = Inquiry(account_id=account.id, channel="web", content="미배정 문의")
    session.add_all([owned, unclaimed])
    await session.flush()
    session.add(Assignment(inquiry_id=owned.id, assignee_id=rep.id, method="claimed"))
    await session.flush()
    with pytest.raises(HTTPException) as denied:
        await create_activity(
            ActivityCreate(
                account_id=account.id,
                inquiry_id=unclaimed.id,
                type="note",
            ),
            session,
            rep,
        )
    assert denied.value.status_code == 403


@pytest.mark.asyncio
async def test_region_uses_longest_active_manager_match_and_inbox_displays_it(
    session: AsyncSession,
) -> None:
    broad = staff("manager", "서울 매니저")
    specific = staff("manager", "강남 매니저")
    inactive = staff("manager", "비활성 매니저", False)
    account = Account(name="가상펜션", phone="01055556666", attributes={})
    session.add_all([broad, specific, inactive, account])
    await session.flush()
    session.add_all(
        [
            SalesRegion(region_name="서울", match_keyword="서울", manager_id=broad.id),
            SalesRegion(region_name="서울 강남", match_keyword="서울 강남", manager_id=specific.id),
            SalesRegion(region_name="부산", match_keyword="부산", manager_id=inactive.id),
        ]
    )
    await session.flush()

    assert await regional_manager_id(session, "서울특별시 강남구 역삼동") == specific.id
    assert await regional_manager_id(session, "부산 해운대") is None
    assert await regional_manager_id(session, None) is None
    inquiry = Inquiry(
        account_id=account.id,
        channel="web",
        content="문의",
        routing_manager_id=specific.id,
    )
    session.add(inquiry)
    await session.flush()
    result = await inbox(session, broad, scope="all")
    assert result[0]["routing_manager_id"] == specific.id
    assert result[0]["routing_manager_name"] == "강남 매니저"
    assert [row["id"] for row in await inbox(session, specific, scope="my_region")] == [inquiry.id]
    assert await inbox(session, broad, scope="my_region") == []


@pytest.mark.asyncio
async def test_equal_length_region_match_has_deterministic_tie_breaker(
    session: AsyncSession,
) -> None:
    first, second = staff("manager", "첫 매니저"), staff("manager", "둘째 매니저")
    session.add_all([first, second])
    await session.flush()
    session.add_all(
        [
            SalesRegion(region_name="가", match_keyword="서울강", manager_id=first.id),
            SalesRegion(region_name="나", match_keyword="울강남", manager_id=second.id),
        ]
    )
    await session.flush()
    assert await regional_manager_id(session, "서울강남") == second.id


def test_partner_region_inputs_are_normalized_and_validated() -> None:
    region = SalesRegionCreate(
        region_name="  서울 강남  ",
        match_keyword=" 서울특별시 강남 ",
        manager_id=uuid.uuid4(),
    )
    assert region.region_name == "서울 강남"
    assert region.match_keyword == "서울강남"
    assert normalize_region_text(" 서울시 강남 ") == "서울강남"
    partner = PartnerCreate(
        name="  검증 파트너 ",
        address=" 서울 중구 ",
        phone="  ",
        region=" 서울 ",
        partner_type="전문점",
        verification_source=" 계약서 ",
        verified_at=seoul_business_date(),
    )
    assert partner.name == "검증 파트너" and partner.phone is None
    with pytest.raises(ValueError):
        SalesRegionCreate(region_name="서울", match_keyword="   ", manager_id=uuid.uuid4())
    with pytest.raises(ValueError):
        PartnerCreate(
            name="파트너",
            address="서울",
            region="서울",
            partner_type="전문점",
            verification_source="계약서",
            verified_at=seoul_business_date() + timedelta(days=1),
        )


def test_seoul_business_date_changes_at_kst_midnight() -> None:
    assert seoul_business_date(datetime(2026, 8, 20, 14, 59, tzinfo=timezone.utc)) == date(
        2026, 8, 20
    )
    assert seoul_business_date(datetime(2026, 8, 20, 15, 0, tzinfo=timezone.utc)) == date(
        2026, 8, 21
    )


@pytest.mark.asyncio
async def test_curated_partners_are_separate_from_naver_candidates(session: AsyncSession) -> None:
    viewer = staff("rep", "조회자")
    account = Account(name="고객", phone="01077778888", attributes={})
    curated = Partner(
        name="수동 검증 파트너",
        address="서울 중구",
        region="서울",
        partner_type="전문점",
        verification_source="내부 계약서",
        verified_at=date(2026, 8, 21),
    )
    session.add_all([viewer, account, curated])
    await session.flush()
    session.add(
        Inquiry(
            account_id=account.id,
            channel="public_web",
            content="문의",
            raw_conversation=[
                {
                    "type": "nearby_store_search",
                    "status": "success",
                    "message": "후보",
                    "stores": [{"name": "네이버 후보", "address": "서울", "phone": ""}],
                }
            ],
        )
    )
    await session.flush()
    assert [partner.name for partner in await list_partners(session, viewer)] == [
        "수동 검증 파트너"
    ]


@pytest.mark.asyncio
async def test_curated_partner_auto_match_and_inbox_contract(session: AsyncSession) -> None:
    manager = staff("manager", "매니저")
    account = Account(name="고객", phone="01078787878", attributes={})
    broad = Partner(
        name="서울",
        address="서울",
        region="서울",
        partner_type="총판",
        verification_source="계약",
        verified_at=date(2026, 8, 20),
    )
    specific = Partner(
        name="강남",
        address="서울 강남",
        phone="02-111-2222",
        region="서울 강남",
        partner_type="전문점",
        verification_source="현장 확인",
        verified_at=date(2026, 8, 21),
    )
    session.add_all([manager, account, broad, specific])
    await session.flush()

    matched_id = await curated_partner_id(session, "서울특별시 강남구")
    assert matched_id == specific.id
    assert await curated_partner_id(session, "제주 서귀포") is None
    inquiry, _ = await create_inquiry(
        session,
        account.id,
        "public_web",
        "문의",
        [{"type": "nearby_store_search", "status": "no_results", "message": "없음", "stores": []}],
        None,
        partner_id=matched_id,
    )
    row = (await inbox(session, manager, scope="all"))[0]
    assert row["id"] == inquiry.id
    assert row["partner"] == {
        "id": specific.id,
        "name": "강남",
        "address": "서울 강남",
        "phone": "02-111-2222",
        "region": "서울 강남",
        "partner_type": "전문점",
        "verification_source": "현장 확인",
        "verified_at": date(2026, 8, 21),
        "is_active": True,
    }
    assert row["nearby_store_search"]["status"] == "no_results"


@pytest.mark.asyncio
async def test_manager_can_link_and_unlink_active_curated_partner(session: AsyncSession) -> None:
    manager = staff("manager", "매니저")
    account = Account(name="고객", phone="01079797979", attributes={})
    partner = Partner(
        name="검증점",
        address="서울",
        region="서울",
        partner_type="전문점",
        verification_source="계약",
        verified_at=date(2026, 8, 21),
    )
    session.add_all([manager, account, partner])
    await session.flush()
    inquiry = Inquiry(account_id=account.id, channel="web", content="문의")
    session.add(inquiry)
    await session.flush()

    assert (
        await link_partner(inquiry.id, PartnerLinkRequest(partner_id=partner.id), session, manager)
    )["partner_id"] == partner.id
    assert (await link_partner(inquiry.id, PartnerLinkRequest(partner_id=None), session, manager))[
        "partner_id"
    ] is None


def test_rep_cannot_link_curated_partner() -> None:
    app = FastAPI()
    app.include_router(inquiries_router)
    rep = staff("rep", "조회자")

    async def override_staff() -> Staff:
        return rep

    async def override_session() -> AsyncIterator[None]:
        yield None

    app.dependency_overrides[get_current_staff] = override_staff
    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as client:
        response = client.patch("/api/inquiries/1/partner", json={"partner_id": None})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_public_submit_auto_links_longest_active_partner(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    session.add_all(
        [
            Partner(
                name="서울점",
                address="서울",
                region="서울",
                partner_type="전문점",
                verification_source="계약",
                verified_at=date(2026, 8, 21),
            ),
            Partner(
                name="강남점",
                address="강남",
                region="서울 강남",
                partner_type="전문점",
                verification_source="계약",
                verified_at=date(2026, 8, 21),
            ),
        ]
    )
    await session.commit()
    monkeypatch.setattr(public, "get_llm_client", lambda: (_ for _ in ()).throw(RuntimeError()))

    async def no_stores(_location: str | None) -> tuple[list[object], str, str]:
        return [], "no_results", "없음"

    monkeypatch.setattr(public, "_nearby_stores", no_stores)
    result = await public.submit.__wrapped__(
        Request({"type": "http", "client": ("127.0.0.1", 1)}),
        PublicSubmissionRequest(
            messages=[ChatMessage(role="user", content="에어컨 문의")],
            fields=IntakeFields(
                business_name="가상 카페",
                phone="01098989898",
                inquiry="에어컨 2대",
                business_type="카페",
                seat_count=20,
                product="에어컨",
                quantity=2,
                location="서울특별시 강남구",
                purchase_stage="견적 요청",
                purchase_timing="즉시",
            ),
        ),
        session,
    )
    inquiry = await session.get(Inquiry, result.inquiry_id)
    partner = await session.get(Partner, inquiry.partner_id if inquiry else None)
    assert partner and partner.name == "강남점"


@pytest.mark.asyncio
async def test_staff_change_rejected_while_active_region_is_owned(session: AsyncSession) -> None:
    owner, manager = staff("owner", "총관리자"), staff("manager", "지역 매니저")
    session.add_all([owner, manager])
    await session.flush()
    session.add(SalesRegion(region_name="서울", match_keyword="서울", manager_id=manager.id))
    await session.commit()
    with pytest.raises(HTTPException, match="활성 지역 담당 매핑") as role_blocked:
        await update_staff_role(manager.id, StaffRoleUpdate(role="rep"), session, owner)
    with pytest.raises(HTTPException, match="활성 지역 담당 매핑") as active_blocked:
        await update_staff_active(manager.id, StaffActiveUpdate(is_active=False), session, owner)
    assert role_blocked.value.status_code == active_blocked.value.status_code == 409


@pytest.mark.asyncio
async def test_unresolved_region_inquiry_blocks_staff_change_after_mapping_deactivation(
    session: AsyncSession,
) -> None:
    owner = staff("owner", "총관리자")
    demoted = staff("manager", "역할 변경 대상")
    deactivated = staff("manager", "비활성 대상")
    account = Account(name="고객", phone="01091919191", attributes={})
    session.add_all([owner, demoted, deactivated, account])
    await session.flush()
    session.add_all(
        [
            SalesRegion(
                region_name="서울", match_keyword="서울", manager_id=demoted.id, is_active=False
            ),
            SalesRegion(
                region_name="부산",
                match_keyword="부산",
                manager_id=deactivated.id,
                is_active=False,
            ),
            Inquiry(
                account_id=account.id,
                channel="web",
                content="열린 문의",
                routing_manager_id=demoted.id,
            ),
            Inquiry(
                account_id=account.id,
                channel="web",
                content="배정 문의",
                status="routed",
                routing_manager_id=deactivated.id,
            ),
        ]
    )
    await session.commit()

    with pytest.raises(HTTPException, match="미해결 지역 문의") as role_blocked:
        await update_staff_role(demoted.id, StaffRoleUpdate(role="rep"), session, owner)
    with pytest.raises(HTTPException, match="미해결 지역 문의") as active_blocked:
        await update_staff_active(
            deactivated.id, StaffActiveUpdate(is_active=False), session, owner
        )
    assert role_blocked.value.status_code == active_blocked.value.status_code == 409

    for inquiry in (
        await session.scalars(
            select(Inquiry).where(Inquiry.routing_manager_id.in_((demoted.id, deactivated.id)))
        )
    ).all():
        inquiry.status = "resolved"
    await session.commit()
    assert (
        await update_staff_role(demoted.id, StaffRoleUpdate(role="rep"), session, owner)
    ).role == "rep"
    assert (
        await update_staff_active(
            deactivated.id, StaffActiveUpdate(is_active=False), session, owner
        )
    ).is_active is False


@pytest.mark.asyncio
async def test_inbox_preserves_inactive_partner_status(session: AsyncSession) -> None:
    manager = staff("manager", "매니저")
    account = Account(name="고객", phone="01092929292", attributes={})
    partner = Partner(
        name="과거 파트너",
        address="서울",
        region="서울",
        partner_type="전문점",
        verification_source="과거 계약",
        verified_at=date(2026, 8, 1),
        is_active=False,
    )
    session.add_all([manager, account, partner])
    await session.flush()
    session.add(
        Inquiry(
            account_id=account.id,
            channel="web",
            content="문의",
            partner_id=partner.id,
        )
    )
    await session.flush()
    row = (await inbox(session, manager, scope="all"))[0]
    assert row["partner"]["name"] == "과거 파트너"
    assert row["partner"]["is_active"] is False


def test_rep_cannot_manage_partner_records() -> None:
    app = FastAPI()
    app.include_router(partners_router)
    rep = staff("rep", "조회자")

    async def override_staff() -> Staff:
        return rep

    async def override_session() -> AsyncIterator[None]:
        yield None

    app.dependency_overrides[get_current_staff] = override_staff
    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as client:
        response = client.post(
            "/api/partners-regions/partners",
            json={
                "name": "파트너",
                "address": "서울",
                "region": "서울",
                "partner_type": "전문점",
                "verification_source": "계약서",
                "verified_at": "2026-08-21",
            },
        )
    assert response.status_code == 403
