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
from app.routes.inquiries import inbox
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
            ),
        )


class SearchLLM:
    def __init__(self, response: str = "", fail: bool = False) -> None:
        self.response = response
        self.fail = fail

    async def text(self, _prompt: str) -> str:
        if self.fail:
            raise RuntimeError("secret provider detail")
        return self.response


@pytest.mark.asyncio
async def test_inbox_includes_account_and_latest_assignee_names(session: AsyncSession) -> None:
    manager = Staff(
        id=uuid.uuid4(),
        name="관리자",
        email="manager@example.test",
        hashed_password="not-used",
        role="manager",
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
    inquiry = Inquiry(account_id=account.id, channel="web", content="객실 냉장고 문의")
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


@pytest.mark.asyncio
async def test_outbound_draft_payload_and_dashboard_mode(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    staff = Staff(
        id=uuid.uuid4(),
        name="담당자",
        email="rep@example.test",
        hashed_password="not-used",
        role="manager",
    )
    lead = Lead(
        name="가상펜션",
        address="서울",
        years_in_business=8,
        business_type="펜션",
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
    session.add_all([staff, lead, product])
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

    assert created["subject"] == "맞춤 제안"
    assert created["body"] == "숙박업 운영 환경에 맞춘 제안입니다."
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
        role="manager",
    )
    session.add(staff)
    await session.commit()
    monkeypatch.setattr(
        search,
        "get_settings",
        lambda: SimpleNamespace(effective_database_readonly_url="sqlite+aiosqlite:///:memory:"),
    )
    monkeypatch.setattr(search, "get_llm_client", lambda: llm)

    with pytest.raises(HTTPException):
        await search.natural_language_search(
            SearchRequest(question="고객사를 찾아줘"), session, staff
        )

    log = await session.scalar(select(QueryLog))
    assert log is not None
    assert log.success is False
    assert log.error_category == category
    assert "secret" not in (log.error_message or "")
    assert log.row_count == 0
