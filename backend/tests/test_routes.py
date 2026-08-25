import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.models import (
    Account,
    Assignment,
    Inquiry,
    Lead,
    OutboundDraft,
    Product,
    QueryLog,
    Staff,
)
from app.routes import outbound, public, search
from app.routes.inquiries import _nearby_store_search_from_raw, inbox
from app.schemas import (
    ChatMessage,
    ChatTurnRequest,
    IntakeFields,
    PublicSubmissionRequest,
    SearchRequest,
)


class DraftLLM:
    async def structured(self, _prompt: str, result_type: type) -> object:
        return result_type(subject="맞춤 제안", body="숙박업 운영 환경에 맞춘 제안입니다.")


class IntakeLLM:
    async def structured(self, _prompt: str, result_type: type) -> object:
        return result_type(
            message="접수 준비가 됐습니다.",
            fields=IntakeFields(
                business_name="삭제 고객사",
                phone="010-2222-3333",
                inquiry="냉장고 문의",
                purchase_stage="견적 요청",
                purchase_timing="1개월 이내",
            ),
        )


class MultiTurnIntakeLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def structured(self, _prompt: str, result_type: type) -> object:
        self.calls += 1
        if self.calls == 1:
            fields = IntakeFields(
                business_name="다온 호텔",
                phone="010-1234-5678",
                inquiry="객실용 냉장고 견적",
                business_type="숙박업",
                room_count=12,
                product="냉장고",
                location="서울특별시 중구",
                purchase_stage="견적 요청",
                purchase_timing="1개월 이내",
            )
        else:
            fields = IntakeFields(quantity=6)
        return result_type(message="확인했습니다.", fields=fields)


class MissingInquiryIntakeLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def structured(self, _prompt: str, result_type: type) -> object:
        self.calls += 1
        fields = (
            IntakeFields(
                business_name="잼민호텔",
                business_type="숙박업",
                product="세탁기",
                quantity=8,
                location="서울 성수동",
            )
            if self.calls == 1
            else IntakeFields(phone="010-2222-5555")
        )
        return result_type(message="확인했습니다.", fields=fields)


class SearchLLM:
    def __init__(self, response: str = "", fail: bool = False) -> None:
        self.response = response
        self.fail = fail

    async def text(self, _prompt: str) -> str:
        if self.fail:
            raise RuntimeError("secret provider detail")
        return self.response


def test_nearby_store_raw_parser_preserves_empty_result_and_ignores_legacy_data() -> None:
    raw: list[object] = [
        None,
        {"stores": [{"name": "잘못된 레거시 항목"}]},
        {
            "type": "nearby_store_search",
            "status": "no_results",
            "message": "검색 결과 없음",
            "stores": [],
        },
    ]

    assert _nearby_store_search_from_raw(raw) == {
        "status": "no_results",
        "message": "검색 결과 없음",
        "stores": [],
    }
    assert _nearby_store_search_from_raw({"type": "nearby_store_search"}) is None
    assert _nearby_store_search_from_raw(42) is None


@pytest.mark.asyncio
async def test_public_chat_accumulates_sparse_llm_fields_across_turns(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm = MultiTurnIntakeLLM()
    monkeypatch.setattr(public, "get_llm_client", lambda: llm)
    request = Request({"type": "http", "client": ("127.0.0.1", 1)})
    first_messages = [ChatMessage(role="user", content="냉장고 견적이 필요해요")]
    first = await public.chat.__wrapped__(
        request, ChatTurnRequest(messages=first_messages), session
    )
    second = await public.chat.__wrapped__(
        request,
        ChatTurnRequest(
            messages=[*first_messages, ChatMessage(role="user", content="6대요")],
            fields=first.fields,
        ),
        session,
    )

    assert second.ready_for_analysis is True
    assert second.fields.model_dump() == {**first.fields.model_dump(), "quantity": 6}


@pytest.mark.asyncio
async def test_public_chat_reuses_product_request_as_missing_inquiry_summary(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm = MissingInquiryIntakeLLM()
    monkeypatch.setattr(public, "get_llm_client", lambda: llm)
    request = Request({"type": "http", "client": ("127.0.0.1", 1)})
    details = ChatMessage(role="user", content="서울 성수동 잼민호텔 세탁기 8대")
    first = await public.chat.__wrapped__(
        request, ChatTurnRequest(messages=[details]), session
    )
    second = await public.chat.__wrapped__(
        request,
        ChatTurnRequest(
            messages=[details, ChatMessage(role="user", content="01022225555")],
            fields=first.fields,
        ),
        session,
    )

    assert second.fields.inquiry == details.content
    assert "어떤 제품이 얼마나" not in second.message


@pytest.mark.asyncio
async def test_inbox_includes_account_and_latest_assignee_names(session: AsyncSession) -> None:
    manager = Staff(
        id=uuid.uuid4(),
        name="관리자",
        email="manager@example.test",
        hashed_password="not-used",
        role="owner",
    )
    old_rep = Staff(
        id=uuid.uuid4(),
        name="이전 담당자",
        email="old@example.test",
        hashed_password="not-used",
        role="manager",
    )
    current_rep = Staff(
        id=uuid.uuid4(),
        name="현재 담당자",
        email="current@example.test",
        hashed_password="not-used",
        role="manager",
    )
    account = Account(name="가상호텔", phone="01022223333", attributes={})
    session.add_all([manager, old_rep, current_rep, account])
    await session.flush()
    inquiry = Inquiry(
        account_id=account.id,
        channel="web",
        content="객실 냉장고 문의",
        raw_conversation=[
            {
                "type": "nearby_store_search",
                "status": "success",
                "message": "검색 완료",
                "stores": [
                    {"name": "다온 전문점", "address": "서울 중구", "phone": "02-123-4567"}
                ],
            }
        ],
    )
    session.add(inquiry)
    await session.flush()
    now = datetime.now(timezone.utc)
    session.add_all(
        [
            Assignment(
                inquiry_id=inquiry.id,
                assignee_id=old_rep.id,
                assigned_at=now - timedelta(days=1),
                method="round_robin",
            ),
            Assignment(
                inquiry_id=inquiry.id,
                assignee_id=current_rep.id,
                assigned_at=now,
                method="manual",
            ),
        ]
    )
    await session.flush()

    result = await inbox(session, manager, scope="all", sort_by="priority")

    assert result[0]["account_name"] == "가상호텔"
    assert result[0]["assignee_id"] == current_rep.id
    assert result[0]["assignee_name"] == "현재 담당자"
    assert result[0]["nearby_store_search"] == {
        "status": "success",
        "message": "검색 완료",
        "stores": [
            {"name": "다온 전문점", "address": "서울 중구", "phone": "02-123-4567"}
        ],
    }


@pytest.mark.asyncio
async def test_inbox_returns_empty_nearby_stores_for_staff_inquiry(session: AsyncSession) -> None:
    manager = Staff(
        id=uuid.uuid4(),
        name="관리자",
        email="nearby-manager@example.test",
        hashed_password="not-used",
        role="owner",
    )
    account = Account(name="가상호텔", phone="01099998888", attributes={})
    session.add_all([manager, account])
    await session.flush()
    session.add(
        Inquiry(
            account_id=account.id,
            channel="staff",
            content="직원 등록 문의",
            raw_conversation=[None, {"stores": []}, "legacy"],  # type: ignore[list-item]
        )
    )
    await session.flush()

    result = await inbox(session, manager, scope="all", sort_by="priority")

    assert result[0]["nearby_store_search"] is None


@pytest.mark.asyncio
async def test_outbound_draft_payload_and_dashboard_mode(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    staff = Staff(
        id=uuid.uuid4(),
        name="관리자",
        email="manager@example.test",
        hashed_password="not-used",
        role="manager",
    )
    assigned_rep = Staff(
        id=uuid.uuid4(),
        name="김담당",
        email="assigned-rep@example.test",
        hashed_password="not-used",
        role="rep",
    )
    lead = Lead(
        name="가상펜션",
        address="서울",
        years_in_business=8,
        business_type="펜션",
        assignee_id=assigned_rep.id,
        raw_data={},
        lead_score=80,
        lead_score_reasoning={"years_in_business": "교체 수요 예상"},
    )
    product = Product(
        name="객실용 냉장고",
        brand="LG",
        category="냉장고",
        price=500_000,
        product_url="https://example.test/product",
    )
    session.add_all([staff, assigned_rep])
    await session.flush()
    session.add_all([lead, product])
    await session.flush()
    first = OutboundDraft(
        lead_id=lead.id,
        sequence_step=1,
        subject="첫 제안",
        body="첫 본문",
        reviewed_by=staff.id,
        send_mode="dry_run",
        sent_at=datetime.now(timezone.utc),
    )
    session.add(first)
    await session.flush()
    monkeypatch.setattr(outbound, "get_llm_client", lambda: DraftLLM())
    monkeypatch.setattr(
        outbound,
        "get_settings",
        lambda: SimpleNamespace(outbound_email_mode="dry_run"),
    )

    created = await outbound.generate_draft(lead.id, session, staff)
    drafts = await outbound.list_drafts(lead.id, session, staff)
    summary = await outbound.dashboard(session, staff)

    assert created["subject"] == "[공급 계약 제안] 맞춤 제안"
    assert created["body"] == (
        "안녕하세요. LG E PARTNER PORTAL 담당자 김담당입니다.\n\n"
        "숙박업 운영 환경에 맞춘 제안입니다.\n\n"
        "구체적인 공급 수량과 일정, 계약 조건은 검토 후 협의를 통해 정리하겠습니다.\n\n"
        "감사합니다.\n김담당 드림"
    )
    assert created["reviewed"] is False
    assert [draft["sequence_step"] for draft in drafts] == [2, 1]
    assert drafts[1]["reviewed"] is True
    assert summary["outbound_email_mode"] == "dry_run"


@pytest.mark.asyncio
async def test_public_flow_never_matches_soft_deleted_account(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    account = Account(
        name="삭제 고객사",
        phone="01022223333",
        attributes={},
        deleted_at=datetime.now(timezone.utc),
    )
    session.add(account)
    await session.commit()
    monkeypatch.setattr(public, "get_llm_client", lambda: IntakeLLM())
    request = Request({"type": "http", "client": ("127.0.0.1", 1)})
    messages = [ChatMessage(role="user", content="문의합니다")]
    fields = IntakeFields(
        business_name="삭제 고객사",
        phone="010-2222-3333",
        inquiry="냉장고 문의",
        business_type="제조업",
        product="냉장고",
        quantity=1,
        location="서울",
        purchase_stage="견적 요청",
        purchase_timing="1개월 이내",
    )

    turn = await public.chat.__wrapped__(
        request, ChatTurnRequest(messages=messages, fields=fields), session
    )
    assert turn.returning_customer is False

    with pytest.raises(HTTPException) as error:
        await public.submit.__wrapped__(
            request,
            PublicSubmissionRequest(messages=messages, fields=fields),
            session,
        )
    assert error.value.status_code == 409
    assert "복구" in str(error.value.detail)
    assert await session.scalar(select(Inquiry.id)) is None


@pytest.mark.asyncio
async def test_public_chat_reports_returning_customer_without_name(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    session.add(Account(name="노출 금지 업체명", phone="01022223333", attributes={}))
    await session.commit()
    monkeypatch.setattr(public, "get_llm_client", lambda: IntakeLLM())
    request = Request({"type": "http", "client": ("127.0.0.1", 1)})

    turn = await public.chat.__wrapped__(
        request,
        ChatTurnRequest(messages=[ChatMessage(role="user", content="문의합니다")]),
        session,
    )

    assert turn.returning_customer is True
    assert "노출 금지 업체명" not in turn.model_dump_json()


@pytest.mark.parametrize(
    ("llm", "category"),
    [
        (SearchLLM(fail=True), "generation_error"),
        (SearchLLM("DROP TABLE accounts"), "validation_error"),
        (SearchLLM("SELECT accounts.name FROM accounts"), "execution_error"),
    ],
)
@pytest.mark.asyncio
async def test_nl2sql_failures_are_audited(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    llm: SearchLLM,
    category: str,
) -> None:
    staff = Staff(
        id=uuid.uuid4(),
        name="검색 담당자",
        email=f"{category}@example.test",
        hashed_password="not-used",
        role="owner",
    )
    session.add(staff)
    await session.commit()
    monkeypatch.setattr(
        search,
        "get_settings",
        lambda: SimpleNamespace(effective_database_readonly_url="sqlite+aiosqlite:///:memory:"),
    )
    monkeypatch.setattr(search, "get_llm_client", lambda: llm)

    with pytest.raises(HTTPException) as raised:
        await search.natural_language_search(
            SearchRequest(question="고객사를 찾아줘"), session, staff
        )

    log = await session.scalar(select(QueryLog))
    assert log is not None
    assert log.success is False
    assert log.error_category == category
    assert "secret" not in (log.error_message or "")
    assert log.row_count == 0
    if category == "validation_error":
        assert raised.value.detail == "안전하지 않은 SQL이 거부되었습니다."
        assert log.error_message != raised.value.detail
