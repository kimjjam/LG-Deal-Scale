import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.localdata import SERVICE_ID, parse_localdata_rows
from app.models import (
    Account,
    AuditLog,
    Inquiry,
    Interaction,
    Lead,
    Opportunity,
    OpportunityStageHistory,
    OutboundDraft,
    Product,
    Score,
    Staff,
    Task,
)
from app.routes import public
from app.routes.accounts import export_accounts, import_accounts
from app.routes.crm import dashboard
from app.routes.outbound import (
    edit_draft,
    export_leads,
    generate_draft,
    import_leads,
    record_actual_contact,
    stop_sequence,
)
from app.routes.public import _fallback_turn, _intake_complete, _public_price, _relevant_products
from app.schemas import (
    ChatMessage,
    CsvTextRequest,
    DraftEditRequest,
    IntakeFields,
    ManualContactRequest,
    PublicSubmissionRequest,
)
from app.scoring import calculate_fit


def staff(role: str = "manager") -> Staff:
    return Staff(
        id=uuid.uuid4(),
        name=role,
        email=f"{uuid.uuid4()}@example.test",
        hashed_password="not-used",
        role=role,
    )


@pytest.mark.asyncio
async def test_outbound_edit_context_contact_and_stop_are_safe(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = staff()
    lead = Lead(
        name="가상호텔",
        raw_data={},
        lead_score=70,
        lead_score_reasoning={},
        pipeline_stage="approved",
    )
    product = Product(
        name="객실 냉장고",
        brand="LG",
        category="냉장고",
        price=Decimal(500000),
        product_url="https://example.test/fridge",
    )
    session.add_all([manager, lead, product])
    await session.flush()
    first = OutboundDraft(
        lead_id=lead.id,
        sequence_step=1,
        subject="이전 제목",
        body="이전 본문",
        reviewed_by=manager.id,
        send_mode="dry_run",
        sent_at=datetime.now(timezone.utc),
    )
    session.add(first)
    await session.commit()
    prompts: list[str] = []

    class LLM:
        async def structured(self, prompt: str, result_type: type) -> object:
            prompts.append(prompt)
            return result_type(subject="후속 제목", body="후속 본문")

    monkeypatch.setattr("app.routes.outbound.get_llm_client", lambda: LLM())
    monkeypatch.setattr(
        "app.routes.outbound.get_settings",
        lambda: SimpleNamespace(outbound_email_mode="dry_run"),
    )
    created = await generate_draft(lead.id, session, manager)
    assert "이전 제목" in prompts[0] and "이전 본문" in prompts[0]

    second = await session.get(OutboundDraft, created["id"])
    assert second is not None
    second.reviewed_by = manager.id
    lead.pipeline_stage = "approved"
    await session.commit()
    edited = await edit_draft(
        second.id,
        DraftEditRequest(subject="수정 제목", body="수정 본문"),
        session,
        manager,
    )
    assert edited["reviewed"] is False
    assert lead.pipeline_stage == "draft_generated"

    payload = ManualContactRequest(channel="phone", note="대표와 통화")
    await record_actual_contact(lead.id, payload, session, manager)
    await record_actual_contact(lead.id, payload, session, manager)
    assert lead.pipeline_stage == "contacted"
    assert (
        await session.scalar(
            select(func.count(AuditLog.id)).where(AuditLog.action == "lead.actual_contact")
        )
        == 2
    )
    await stop_sequence(lead.id, session, manager)
    await stop_sequence(lead.id, session, manager)
    assert lead.pipeline_stage == "dropped"
    assert (
        await session.scalar(
            select(func.count(AuditLog.id)).where(AuditLog.action == "lead.sequence_stop")
        )
        == 1
    )
    with pytest.raises(HTTPException):
        await record_actual_contact(lead.id, payload, session, manager)


@pytest.mark.asyncio
async def test_csv_imports_are_atomic_and_exports_exclude_deleted(
    session: AsyncSession,
) -> None:
    manager = staff()
    session.add(manager)
    await session.commit()
    invalid_accounts = CsvTextRequest(
        csv_text='name,phone,attributes\n정상,010-1111-2222,"{}"\n오류,12,"{}"'
    )
    result = await import_accounts(invalid_accounts, session, manager)
    assert result["imported_count"] == 0 and result["errors"]
    assert await session.scalar(select(func.count(Account.id))) == 0

    valid_accounts = CsvTextRequest(
        csv_text='name,phone,attributes\n정상,010-1111-2222,"{""room_count"": 8}"'
    )
    result = await import_accounts(valid_accounts, session, manager)
    assert result == {"imported_count": 1, "errors": []}
    account = await session.scalar(select(Account))
    assert account is not None
    account.deleted_at = datetime.now(timezone.utc)
    await session.commit()
    exported = await export_accounts(session, manager)
    assert "정상" not in exported.body.decode("utf-8")

    invalid_leads = CsvTextRequest(csv_text="name,lead_score\n정상,80\n오류,101")
    result = await import_leads(invalid_leads, session, manager)
    assert result["imported_count"] == 0
    assert await session.scalar(select(func.count(Lead.id))) == 0
    result = await import_leads(
        CsvTextRequest(csv_text="name,lead_score,business_type\n가상숙소,80,호텔"),
        session,
        manager,
    )
    assert result == {"imported_count": 1, "errors": []}
    assert "가상숙소" in (await export_leads(session, manager)).body.decode("utf-8")

    rejected = await import_leads(
        CsvTextRequest(csv_text="name,lead_score,pipeline_stage\n완료위장,80,converted"),
        session,
        manager,
    )
    assert rejected["imported_count"] == 0 and rejected["errors"]


@pytest.mark.asyncio
async def test_csv_exports_neutralize_formula_cells(session: AsyncSession) -> None:
    manager = staff()
    account = Account(name='=HYPERLINK("bad")', phone="01011112222", attributes={})
    lead = Lead(
        name="+CMD",
        address="@formula",
        raw_data={},
        lead_score=50,
        lead_score_reasoning={},
    )
    session.add_all([manager, account, lead])
    await session.commit()
    assert "'=HYPERLINK" in (await export_accounts(session, manager)).body.decode("utf-8")
    exported_leads = (await export_leads(session, manager)).body.decode("utf-8")
    assert "'+CMD" in exported_leads and "'@formula" in exported_leads


@pytest.mark.asyncio
async def test_csv_row_cap_and_rep_export_visibility(session: AsyncSession) -> None:
    manager, rep = staff(), staff("rep")
    visible = Account(name="담당 고객", phone="01055556666", attributes={})
    hidden = Account(name="비담당 고객", phone="01077778888", attributes={})
    session.add_all([manager, rep, visible, hidden])
    await session.flush()
    session.add(
        Opportunity(
            account_id=visible.id,
            assignee_id=rep.id,
            title="담당 영업기회",
            probability=10,
            stage="qualify",
        )
    )
    await session.commit()
    exported = (await export_accounts(session, rep)).body.decode("utf-8")
    assert "담당 고객" in exported and "비담당 고객" not in exported

    oversized = "name,phone\n" + "\n".join(f"고객{index},010{index:08d}" for index in range(501))
    result = await import_accounts(CsvTextRequest(csv_text=oversized), session, manager)
    assert result["imported_count"] == 0
    assert "500행" in result["errors"][0]["error"]


@pytest.mark.asyncio
async def test_public_product_filter_and_returning_attributes(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    account = Account(
        name="재방문 호텔",
        phone="01022223333",
        attributes={"room_count": 12, "business_type": "모텔", "unrelated": "keep"},
    )
    fridge = Product(
        name="객실 냉장고",
        brand="LG",
        category="냉장고",
        price=Decimal(500000),
        usage_context="guest_room",
        is_verified=True,
        product_url="https://example.test/fridge",
    )
    competitor = Product(
        name="타사 객실 냉장고",
        brand="Competitor",
        category="냉장고",
        price=Decimal(450000),
        usage_context="guest_room",
        is_verified=True,
        product_url="https://example.test/competitor-fridge",
    )
    side_by_side = Product(
        name="LG 양문형 냉장고",
        brand="LG",
        category="냉장고",
        price=Decimal(1900000),
        usage_context="residential_large",
        is_verified=True,
        product_url="https://example.test/side-by-side",
    )
    washer = Product(
        name="상업용 세탁기",
        brand="LG",
        category="세탁기",
        price=Decimal(900000),
        is_verified=True,
        product_url="https://example.test/washer",
    )
    session.add_all([account, fridge, competitor, side_by_side, washer])
    await session.commit()
    prompts: list[str] = []
    captured_raw: list[object] = []
    fit_at_creation: list[int] = []

    class LLM:
        async def text(self, prompt: str) -> str:
            prompts.append(prompt)
            return "추천 분석"

    async def fake_create_inquiry(
        session: AsyncSession,
        account_id: int,
        channel: str,
        content: str,
        raw: object,
        llm: object,
        routing_manager_id: object = None,
        partner_id: int | None = None,
    ) -> tuple[Inquiry, bool]:
        del channel, llm, routing_manager_id, partner_id
        captured_raw.append(raw)
        current_account = await session.get(Account, account_id)
        assert current_account
        fit_at_creation.append(calculate_fit(current_account.attributes)[0])
        inquiry = Inquiry(account_id=account_id, channel="public_web", content=content)
        session.add(inquiry)
        await session.commit()
        return inquiry, False

    monkeypatch.setattr(public, "get_llm_client", lambda: LLM())
    monkeypatch.setattr(public, "create_inquiry", fake_create_inquiry)
    monkeypatch.setattr(
        public,
        "_nearby_stores",
        lambda _location: _async_value(([], "no_results", "검색 결과 없음")),
    )
    request = Request({"type": "http", "client": ("127.0.0.1", 1)})
    response = await public.submit.__wrapped__(
        request,
        PublicSubmissionRequest(
            messages=[ChatMessage(role="user", content="냉장고 문의")],
            fields=IntakeFields(
                business_name="재방문 호텔",
                phone=account.phone,
                inquiry="객실 냉장고가 필요합니다",
                product="냉장고 6대",
                business_type="호텔",
                room_count=20,
                purchase_stage="견적 요청",
                purchase_timing="1개월 이내",
            ),
        ),
        session,
    )
    assert [item.name for item in response.products] == ["객실 냉장고", "타사 객실 냉장고"]
    assert all(item.price is None for item in response.products)
    assert all(item.price_label == "사업자 가격 상담 필요" for item in response.products)
    assert "양문형 냉장고" not in prompts[0]
    assert "상업용 세탁기" not in prompts[0]
    assert account.attributes == {
        "room_count": 20,
        "business_type": "호텔",
        "unrelated": "keep",
    }
    assert fit_at_creation == [60]
    assert response.inquiry_id
    assert response.nearby_store_status == "no_results"
    assert response.nearby_store_message == "검색 결과 없음"
    assert captured_raw[0][-1]["type"] == "intake_fields"  # type: ignore[index]
    stored = await session.get(Inquiry, response.inquiry_id)
    assert stored and "구매 단계: 견적 요청" in stored.content
    assert "구매 시기: 1개월 이내" in stored.content
    assert stored.raw_conversation and stored.raw_conversation[-1] == {
        "type": "nearby_store_search",
        "status": "no_results",
        "message": "검색 결과 없음",
        "stores": [],
    }

    async def failed_store_search(_location: str | None) -> object:
        raise RuntimeError("provider credential detail")

    monkeypatch.setattr(public, "_nearby_stores", failed_store_search)
    failed_response = await public.submit.__wrapped__(
        request,
        PublicSubmissionRequest(
            messages=[ChatMessage(role="user", content="냉장고 문의")],
            fields=IntakeFields(
                business_name="재방문 카페",
                phone=account.phone,
                inquiry="카페용 냉장고가 필요합니다",
                product="냉장고 2대",
                business_type="카페",
                seat_count=30,
                location="서울 중구",
                purchase_stage="견적 요청",
                purchase_timing="1개월 이내",
            ),
        ),
        session,
    )
    failed_stored = await session.get(Inquiry, failed_response.inquiry_id)
    assert failed_response.nearby_store_status == "failed"
    assert "credential" not in failed_response.nearby_store_message
    assert account.attributes == {
        "business_type": "카페",
        "seat_count": 30,
        "unrelated": "keep",
    }
    assert fit_at_creation == [60, 60]
    assert failed_stored and failed_stored.raw_conversation
    assert failed_stored.raw_conversation[-1]["status"] == "failed"


@pytest.mark.asyncio
async def test_nearby_store_search_reports_missing_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        public,
        "get_settings",
        lambda: SimpleNamespace(naver_client_id=None, naver_client_secret=None),
    )

    stores, status, message = await public._nearby_stores("서울 중구")

    assert stores == []
    assert status == "not_configured"
    assert "이용할 수 없습니다" in message


@pytest.mark.asyncio
async def test_nearby_store_search_reports_missing_location(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        public,
        "get_settings",
        lambda: SimpleNamespace(naver_client_id=None, naver_client_secret=None),
    )

    stores, status, message = await public._nearby_stores(None)

    assert stores == []
    assert status == "location_missing"
    assert "지역 정보" in message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("items", "expected_status"),
    [
        ([], "no_results"),
        (
            [
                None,
                {
                    "title": "<b>다온 전문점</b>",
                    "roadAddress": "서울 중구",
                    "telephone": "02-123-4567",
                },
            ],
            "success",
        ),
    ],
)
async def test_nearby_store_search_distinguishes_empty_and_successful_results(
    monkeypatch: pytest.MonkeyPatch,
    items: list[object],
    expected_status: str,
) -> None:
    class Client:
        async def __aenter__(self):  # type: ignore[no-untyped-def]
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, *_args: object, **_kwargs: object) -> httpx.Response:
            return httpx.Response(
                200,
                json={"items": items},
                request=httpx.Request("GET", "https://example.test"),
            )

    monkeypatch.setattr(
        public,
        "get_settings",
        lambda: SimpleNamespace(naver_client_id="configured", naver_client_secret="configured"),
    )
    monkeypatch.setattr(public.httpx, "AsyncClient", lambda **_kwargs: Client())

    stores, status, _message = await public._nearby_stores("서울 중구")

    assert status == expected_status
    assert stores == (
        [{"name": "다온 전문점", "address": "서울 중구", "phone": "02-123-4567"}] if items else []
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{}, {"items": {}}, {"items": [None, {"title": ""}]}])
async def test_nearby_store_search_rejects_malformed_provider_results(
    monkeypatch: pytest.MonkeyPatch,
    payload: object,
) -> None:
    class Client:
        async def __aenter__(self):  # type: ignore[no-untyped-def]
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, *_args: object, **_kwargs: object) -> httpx.Response:
            return httpx.Response(
                200,
                json=payload,
                request=httpx.Request("GET", "https://example.test"),
            )

    monkeypatch.setattr(
        public,
        "get_settings",
        lambda: SimpleNamespace(naver_client_id="configured", naver_client_secret="configured"),
    )
    monkeypatch.setattr(public.httpx, "AsyncClient", lambda **_kwargs: Client())

    stores, status, _message = await public._nearby_stores("서울 중구")

    assert stores == []
    assert status == "failed"


async def _async_value(value: object) -> object:
    return value


def test_product_filter_handles_compound_terms_without_catalog_fallback() -> None:
    fridge = Product(name="객실 냉장고", brand="LG", category="냉장고", price=1, product_url="x")
    washer = Product(name="상업용 세탁기", brand="LG", category="세탁기", price=1, product_url="x")
    products = [fridge, washer]
    assert (
        _relevant_products(products, IntakeFields(inquiry="냉장고와 세탁기가 필요합니다"))
        == products
    )
    assert _relevant_products(products, IntakeFields(inquiry="에어컨 문의")) == []


@pytest.mark.parametrize(
    "business_type",
    ["숙박업", "호텔업", "관광호텔", "리조트", "  모텔  "],
)
def test_lodging_fridge_filter_rejects_large_residential_models(
    business_type: str,
) -> None:
    guest_room = Product(
        name="객실용 소형 냉장고",
        brand="LG",
        category="냉장고",
        price=1,
        usage_context="guest_room",
        product_url="guest",
    )
    side_by_side = Product(
        name="양문형 냉장고",
        brand="LG",
        category="냉장고",
        price=1,
        usage_context="residential_large",
        product_url="large",
    )
    assert _relevant_products(
        [guest_room, side_by_side],
        IntakeFields(business_type=business_type, inquiry="객실 냉장고 6대 필요"),
    ) == [guest_room]


def test_intake_requires_purchase_stage_and_timing() -> None:
    base = {
        "business_name": "가상 모텔",
        "phone": "01012345678",
        "inquiry": "객실 냉장고 견적",
    }
    assert not _intake_complete(IntakeFields(**base))
    complete = {**base, "purchase_stage": "견적 요청", "purchase_timing": "즉시"}
    assert not _intake_complete(IntakeFields(**complete, business_type="호텔"))
    assert _intake_complete(IntakeFields(**complete, business_type="호텔", room_count=12))
    assert _intake_complete(IntakeFields(**complete, business_type="제조업"))


@pytest.mark.parametrize(
    ("business_type", "field"),
    [
        ("호텔", "room_count"),
        ("카페", "seat_count"),
        ("사무실", "employee_count"),
        ("소매업", "store_count"),
    ],
)
def test_supported_industry_requires_its_scale(business_type: str, field: str) -> None:
    base = {
        "business_name": "가상 업체",
        "phone": "01012345678",
        "inquiry": "가전 견적",
        "business_type": business_type,
        "purchase_stage": "견적 요청",
        "purchase_timing": "즉시",
    }
    assert not _intake_complete(IntakeFields(**base))
    assert _intake_complete(IntakeFields(**base, **{field: 10}))


def test_fallback_finishes_required_questions_before_optional_questions() -> None:
    fields = IntakeFields()
    steps = [
        ("업체명을", {"business_name": "가상 모텔"}),
        ("전화번호를", {"phone": "01012345678"}),
        ("제품이 얼마나", {"inquiry": "냉장고 6대 견적"}),
        ("견적 요청", {"purchase_stage": "견적 요청"}),
        ("구매 시기", {"purchase_timing": "1개월 이내"}),
        ("업종", {"business_type": "카페"}),
        ("좌석", {"seat_count": 30}),
    ]
    for expected, update in steps:
        assert expected in _fallback_turn(fields).message
        fields = fields.model_copy(update=update)
    assert _intake_complete(fields)
    assert "버튼" in _fallback_turn(fields).message
    assert "담당자에게 전달" in _fallback_turn(fields).message


def test_wholesale_price_requires_its_own_source_and_verification_date() -> None:
    product = Product(
        name="객실용 냉장고",
        brand="LG",
        category="냉장고",
        price=Decimal(400000),
        price_type="wholesale",
        is_verified=True,
        product_url="https://example.test/product",
    )
    assert _public_price(product) == (None, "사업자 가격 상담 필요")
    product.price_source_url = "https://example.test/wholesale-quote"
    assert _public_price(product) == (None, "사업자 가격 상담 필요")
    product.price_verified_at = datetime.now(timezone.utc).date()
    assert _public_price(product) == (400000.0, "사업자 가격 400,000원")


def test_product_metadata_migration_does_not_guess_unknown_fridge_usage() -> None:
    migration = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0004_product_recommendation_metadata.py"
    ).read_text(encoding="utf-8")
    assert "WHEN category = '냉장고'" not in migration
    assert "%/s834mee111" in migration
    assert "%/RS84DB5002CW/" in migration


@pytest.mark.asyncio
async def test_follow_up_requires_sent_reviewed_draft_and_invalid_llm_is_502(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = staff()
    lead = Lead(name="가상호텔", raw_data={}, lead_score=70, lead_score_reasoning={})
    session.add_all([manager, lead])
    await session.flush()
    previous = OutboundDraft(
        lead_id=lead.id,
        sequence_step=1,
        subject="이전",
        body="본문",
        reviewed_by=manager.id,
        send_mode="dry_run",
    )
    session.add(previous)
    await session.commit()
    with pytest.raises(HTTPException) as unsent:
        await generate_draft(lead.id, session, manager)
    assert unsent.value.status_code == 409

    previous.sent_at = datetime.now(timezone.utc)
    await session.commit()

    class InvalidLLM:
        async def structured(self, _prompt: str, result_type: type) -> object:
            return result_type(subject="x" * 301, body="본문")

    monkeypatch.setattr("app.routes.outbound.get_llm_client", lambda: InvalidLLM())
    with pytest.raises(HTTPException) as invalid:
        await generate_draft(lead.id, session, manager)
    assert invalid.value.status_code == 502


@pytest.mark.asyncio
async def test_dashboard_uses_explicit_deterministic_denominators(
    session: AsyncSession,
) -> None:
    manager, rep = staff(), staff("rep")
    account = Account(name="고객", phone="01033334444", attributes={})
    session.add_all([manager, rep, account])
    await session.flush()
    inquiries = [
        Inquiry(account_id=account.id, channel="web", content=f"문의 {index}") for index in range(2)
    ]
    session.add_all(inquiries)
    await session.flush()
    won = Opportunity(
        account_id=account.id,
        inquiry_id=inquiries[0].id,
        assignee_id=rep.id,
        title="수주",
        amount=Decimal(1000),
        probability=100,
        stage="won",
    )
    lost = Opportunity(
        account_id=account.id,
        inquiry_id=inquiries[1].id,
        assignee_id=rep.id,
        title="실주",
        amount=Decimal(500),
        probability=0,
        stage="lost",
        loss_reason="예산",
    )
    open_opportunity = Opportunity(
        account_id=account.id,
        assignee_id=rep.id,
        title="제안",
        amount=Decimal(2000),
        probability=5,
        stage="propose",
    )
    session.add_all([won, lost, open_opportunity])
    await session.flush()
    start = datetime.now(timezone.utc) - timedelta(hours=10)
    session.add_all(
        [
            OpportunityStageHistory(
                opportunity_id=won.id, stage="qualify", changed_by=manager.id, changed_at=start
            ),
            OpportunityStageHistory(
                opportunity_id=won.id,
                stage="won",
                changed_by=manager.id,
                changed_at=start + timedelta(hours=4),
            ),
            Task(
                account_id=account.id,
                assignee_id=rep.id,
                title="기한 초과",
                due_at=datetime.now(timezone.utc) - timedelta(days=1),
            ),
            Interaction(account_id=account.id, staff_id=rep.id, type="call"),
            Score(
                inquiry_id=inquiries[0].id,
                fit_score=80,
                intent_score=80,
                intent_category="구매임박",
                intent_confidence=0.9,
                recency_score=60,
                total_score=80,
                reasoning={},
                llm_provider="test",
                model_name="test",
            ),
            Score(
                inquiry_id=inquiries[1].id,
                fit_score=50,
                intent_score=50,
                intent_category="정보탐색",
                intent_confidence=0.8,
                recency_score=60,
                total_score=50,
                reasoning={},
                llm_provider="test",
                model_name="test",
            ),
        ]
    )
    await session.commit()
    result = await dashboard(session, manager)
    assert result["weighted_amount"] == 1100.0
    assert result["closed_conversion"] == {
        "won": 1,
        "lost": 1,
        "denominator": 2,
        "rate": 0.5,
        "definition": "won / (won + lost)",
    }
    assert result["tasks"] == {"open": 1, "overdue": 1}
    assert result["average_stage_hours"]["qualify"] == 4.0
    assert result["average_stage_hours"]["propose"] is not None
    assert result["rep_stats"][0]["activity_count"] == 1
    assert (
        next(item for item in result["ai_score_buckets"] if item["range"] == "80-100")[
            "won_conversion"
        ]
        == 1.0
    )


def test_localdata_fixture_parser_uses_confirmed_mapping_without_filtering() -> None:
    assert SERVICE_ID == "03_11_03_P"
    row = {
        "bplcNm": "가상호텔",
        "rdnWhlAddr": "서울 도로명",
        "siteWhlAddr": "서울 지번",
        "apvPermYmd": "20240102",
        "uptaeNm": "관광호텔",
        "trdStateNm": "영업/정상",
        "trdStateGbn": "01",
        "dtlStateNm": "정상",
        "dtlStateGbn": "01",
        "mgtNo": "M-1",
    }
    parsed = parse_localdata_rows({"result": {"body": {"rows": [row]}}})
    assert parsed[0]["address"] == "서울 도로명"
    assert parsed[0]["license_date"].isoformat() == "2024-01-02"
    assert parsed[0]["status_name"] == "영업/정상"
    assert parsed[0]["detailed_status_name"] == "정상"
    assert parsed[0]["management_number"] == "M-1"
    assert parsed[0]["raw_data"] == row
    assert parse_localdata_rows({"result": {"body": {"rows": {"@class": "list"}}}}) == []


def test_localdata_parser_rejects_undocumented_shapes() -> None:
    with pytest.raises(TypeError):
        parse_localdata_rows({"result": {"body": {"rows": {"unexpected": True}}}})


def test_localdata_parser_rejects_process_errors_and_bad_dates() -> None:
    with pytest.raises(ValueError, match="인증 실패"):
        parse_localdata_rows(
            {
                "result": {
                    "header": {"process": {"code": "ERROR", "message": "인증 실패"}},
                    "body": {"rows": []},
                }
            }
        )
    with pytest.raises(ValueError, match="잘못된 인허가일자"):
        parse_localdata_rows(
            {"result": {"body": {"rows": [{"bplcNm": "가상호텔", "apvPermYmd": "bad"}]}}}
        )


def test_localdata_address_uses_trimmed_lot_address_fallback() -> None:
    parsed = parse_localdata_rows(
        {
            "result": {
                "body": {
                    "rows": [
                        {
                            "bplcNm": "가상호텔",
                            "rdnWhlAddr": "   ",
                            "siteWhlAddr": "  서울 지번  ",
                        }
                    ]
                }
            }
        }
    )
    assert parsed[0]["address"] == "서울 지번"
