import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, Assignment, Inquiry, Lead, OutboundDraft, Product, Staff
from app.routes import outbound
from app.routes.inquiries import inbox


class DraftLLM:
    async def structured(self, _prompt: str, result_type: type) -> object:
        return result_type(subject="맞춤 제안", body="숙박업 운영 환경에 맞춘 제안입니다.")


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
        role="rep",
    )
    current_rep = Staff(
        id=uuid.uuid4(),
        name="현재 담당자",
        email="current@example.test",
        hashed_password="not-used",
        role="rep",
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
        role="rep",
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
