import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models import Lead, OutboundDraft, Staff
from app.routes import outbound
from app.routes.outbound import (
    change_stage,
    generate_draft,
    list_leads,
    record_actual_contact,
    review_draft,
    update_lead,
)
from app.schemas import LeadStageRequest, LeadUpdateRequest, ManualContactRequest
from app.security import get_current_staff


async def staff_member(session: AsyncSession, role: str, name: str) -> Staff:
    staff = Staff(
        id=uuid.uuid4(),
        name=name,
        email=f"{uuid.uuid4()}@example.test",
        hashed_password="not-used",
        role=role,
    )
    session.add(staff)
    await session.flush()
    return staff


@pytest.mark.asyncio
async def test_lead_update_requests_row_lock() -> None:
    manager = Staff(
        id=uuid.uuid4(),
        name="관리자",
        email="lock-manager@example.test",
        hashed_password="not-used",
        role="manager",
    )
    lead = Lead(
        id=1,
        name="잠금 검증 리드",
        source="csv",
        raw_data={},
        lead_score=70,
        lead_score_reasoning={},
    )
    fake_session = MagicMock()
    fake_session.scalar = AsyncMock(return_value=lead)
    fake_session.commit = AsyncMock()

    await update_lead(
        lead.id, LeadUpdateRequest(contact_name="담당자"), fake_session, manager
    )

    statement = fake_session.scalar.await_args.args[0]
    assert statement._for_update_arg is not None


@pytest.mark.asyncio
async def test_manager_assigns_and_rep_operates_only_own_lead(session: AsyncSession) -> None:
    manager = await staff_member(session, "manager", "관리자")
    rep = await staff_member(session, "rep", "담당자")
    other = await staff_member(session, "rep", "다른 담당자")
    lead = Lead(
        name="테스트 숙박업체",
        source="csv",
        raw_data={},
        lead_score=70,
        lead_score_reasoning={},
        pipeline_stage="approved",
    )
    session.add(lead)
    await session.commit()

    await update_lead(lead.id, LeadUpdateRequest(assignee_id=rep.id), session, manager)
    due_at = datetime.now(timezone.utc) + timedelta(days=1)
    await update_lead(
        lead.id,
        LeadUpdateRequest(contact_phone="010-1234-5678", next_action_at=due_at),
        session,
        rep,
    )
    assert lead.contact_phone == "01012345678"
    assert lead.pipeline_stage == "follow_up_due"

    rows = await list_leads(session, rep, None, None, None, True, 50, 0)
    assert [row["id"] for row in rows] == [lead.id]
    with pytest.raises(HTTPException) as denied:
        await update_lead(lead.id, LeadUpdateRequest(contact_name="가로채기"), session, other)
    assert denied.value.status_code == 403

    await record_actual_contact(
        lead.id, ManualContactRequest(channel="phone", note="통화 완료"), session, rep
    )
    assert lead.pipeline_stage == "contacted"
    assert lead.next_action_at is None


@pytest.mark.asyncio
async def test_follow_up_draft_returns_to_review_stage(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    rep = await staff_member(session, "rep", "담당자")
    lead = Lead(
        name="후속 리드",
        source="csv",
        raw_data={},
        lead_score=80,
        lead_score_reasoning={},
        assignee_id=rep.id,
        pipeline_stage="follow_up_due",
        next_action_at=datetime.now(timezone.utc),
    )
    session.add(lead)
    await session.flush()
    previous = OutboundDraft(
        lead_id=lead.id,
        sequence_step=1,
        subject="첫 제목",
        body="첫 본문",
        reviewed_by=rep.id,
        send_mode="dry_run",
        sent_at=datetime.now(timezone.utc),
    )
    session.add(previous)
    await session.commit()

    class LLM:
        async def structured(self, _prompt: str, result_type: type) -> object:
            return result_type(subject="후속 제목", body="후속 본문")

    monkeypatch.setattr("app.routes.outbound.get_llm_client", lambda: LLM())
    monkeypatch.setattr(
        "app.routes.outbound.get_settings",
        lambda: SimpleNamespace(outbound_email_mode="dry_run"),
    )
    await generate_draft(lead.id, session, rep)
    assert lead.pipeline_stage == "draft_generated"
    assert lead.next_action_at is None


@pytest.mark.asyncio
async def test_sent_draft_cannot_be_reviewed_again(session: AsyncSession) -> None:
    rep = await staff_member(session, "rep", "담당자")
    lead = Lead(
        name="발송 완료 리드",
        source="csv",
        raw_data={},
        lead_score=80,
        lead_score_reasoning={},
        assignee_id=rep.id,
        pipeline_stage="approved",
    )
    session.add(lead)
    await session.flush()
    draft = OutboundDraft(
        lead_id=lead.id,
        sequence_step=1,
        subject="제목",
        body="본문",
        send_mode="dry_run",
        sent_at=datetime.now(timezone.utc),
    )
    session.add(draft)
    await session.commit()

    with pytest.raises(HTTPException) as error:
        await review_draft(draft.id, session, rep)
    assert error.value.status_code == 409
    assert draft.reviewed_by is None


def test_next_action_requires_aware_future_datetime() -> None:
    with pytest.raises(ValidationError):
        LeadUpdateRequest(
            next_action_at=datetime(2099, 1, 1, tzinfo=timezone.utc).replace(tzinfo=None)
        )
    with pytest.raises(ValidationError):
        LeadUpdateRequest(next_action_at=datetime.now(timezone.utc) - timedelta(seconds=1))


@pytest.mark.asyncio
async def test_follow_up_stage_keeps_schedule_coherent(session: AsyncSession) -> None:
    rep = await staff_member(session, "rep", "담당자")
    lead = Lead(
        name="후속 일정 리드",
        source="csv",
        raw_data={},
        lead_score=60,
        lead_score_reasoning={},
        assignee_id=rep.id,
        pipeline_stage="approved",
    )
    session.add(lead)
    await session.commit()

    due_at = datetime.now(timezone.utc) + timedelta(days=1)
    await update_lead(lead.id, LeadUpdateRequest(next_action_at=due_at), session, rep)
    with pytest.raises(HTTPException) as error:
        await update_lead(lead.id, LeadUpdateRequest(next_action_at=None), session, rep)
    assert error.value.status_code == 422
    assert lead.next_action_at == due_at

    await change_stage(lead.id, LeadStageRequest(pipeline_stage="contacted"), session, rep)
    assert lead.next_action_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize("pipeline_stage", ["discovered", "draft_generated"])
async def test_schedule_cannot_bypass_stage_transitions(
    session: AsyncSession, pipeline_stage: str
) -> None:
    rep = await staff_member(session, "rep", "담당자")
    lead = Lead(
        name="일정 제한 리드",
        source="csv",
        raw_data={},
        lead_score=60,
        lead_score_reasoning={},
        assignee_id=rep.id,
        pipeline_stage=pipeline_stage,
    )
    session.add(lead)
    await session.commit()

    with pytest.raises(HTTPException) as error:
        await update_lead(
            lead.id,
            LeadUpdateRequest(next_action_at=datetime.now(timezone.utc) + timedelta(days=1)),
            session,
            rep,
        )
    assert error.value.status_code == 409
    assert lead.pipeline_stage == pipeline_stage
    assert lead.next_action_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("PATCH", "/api/outbound/leads/{lead_id}", {"contact_name": "가로채기"}),
        ("PUT", "/api/outbound/leads/{lead_id}/stage", {"pipeline_stage": "dropped"}),
        ("POST", "/api/outbound/leads/{lead_id}/convert", "convert"),
        ("POST", "/api/outbound/leads/{lead_id}/drafts", None),
        ("POST", "/api/outbound/drafts/{draft_id}/review", None),
        ("POST", "/api/outbound/drafts/{draft_id}/send", None),
        ("POST", "/api/outbound/leads/{lead_id}/stop", None),
    ],
)
async def test_unowned_mutation_routes_return_403(
    session: AsyncSession,
    method: str,
    path: str,
    payload: dict[str, str] | str | None,
) -> None:
    rep = await staff_member(session, "rep", "담당자")
    other = await staff_member(session, "rep", "다른 담당자")
    lead = Lead(
        name="타인 리드",
        source="csv",
        raw_data={},
        lead_score=90,
        lead_score_reasoning={},
        assignee_id=other.id,
    )
    session.add(lead)
    await session.flush()
    draft = OutboundDraft(
        lead_id=lead.id,
        sequence_step=1,
        subject="타인 제목",
        body="타인 본문",
        reviewed_by=other.id,
        send_mode="dry_run",
        sent_at=datetime.now(timezone.utc),
    )
    session.add(draft)
    await session.commit()

    app = FastAPI()
    app.include_router(outbound.router)

    async def override_staff() -> Staff:
        return rep

    async def override_session():
        yield session

    app.dependency_overrides[get_current_staff] = override_staff
    app.dependency_overrides[get_session] = override_session
    request_payload = (
        {
            "phone": "01012345678",
            "assignee_id": str(rep.id),
            "opportunity_title": "가로채기",
        }
        if payload == "convert"
        else payload
    )
    url = path.format(lead_id=lead.id, draft_id=draft.id)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.request(method, url, json=request_payload)

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_rep_outbound_routes_are_scoped_to_owned_records(session: AsyncSession) -> None:
    rep = await staff_member(session, "rep", "담당자")
    other = await staff_member(session, "rep", "다른 담당자")
    owned = Lead(
        name="내 리드",
        contact_phone="01011112222",
        source="csv",
        raw_data={},
        lead_score=80,
        lead_score_reasoning={},
        assignee_id=rep.id,
    )
    unowned = Lead(
        name="타인 리드",
        contact_phone="01099998888",
        source="csv",
        raw_data={},
        lead_score=90,
        lead_score_reasoning={},
        assignee_id=other.id,
    )
    session.add_all([owned, unowned])
    await session.flush()
    owned_draft = OutboundDraft(
        lead_id=owned.id, sequence_step=1, subject="내 제목", body="내 본문", send_mode="dry_run"
    )
    unowned_draft = OutboundDraft(
        lead_id=unowned.id,
        sequence_step=1,
        subject="타인 제목",
        body="타인 본문",
        reviewed_by=other.id,
        send_mode="dry_run",
        sent_at=datetime.now(timezone.utc),
    )
    session.add_all([owned_draft, unowned_draft])
    await session.commit()

    app = FastAPI()
    app.include_router(outbound.router)

    async def override_staff() -> Staff:
        return rep

    async def override_session():
        yield session

    app.dependency_overrides[get_current_staff] = override_staff
    app.dependency_overrides[get_session] = override_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listed = await client.get("/api/outbound/leads")
        owned_drafts = await client.get(f"/api/outbound/leads/{owned.id}/drafts")
        denied_drafts = await client.get(f"/api/outbound/leads/{unowned.id}/drafts")
        edited = await client.patch(
            f"/api/outbound/drafts/{owned_draft.id}",
            json={"subject": "수정 제목", "body": "수정 본문"},
        )
        denied_edit = await client.patch(
            f"/api/outbound/drafts/{unowned_draft.id}",
            json={"subject": "가로채기", "body": "가로채기"},
        )
        contacted = await client.post(
            f"/api/outbound/leads/{owned.id}/actual-contact",
            json={"channel": "phone", "note": "통화"},
        )
        denied_contact = await client.post(
            f"/api/outbound/leads/{unowned.id}/actual-contact",
            json={"channel": "phone", "note": "가로채기"},
        )
        exported = await client.get("/api/outbound/leads/export.csv")
        summary = await client.get("/api/outbound/dashboard")

    assert [row["id"] for row in listed.json()] == [owned.id]
    assert owned_drafts.status_code == edited.status_code == contacted.status_code == 200
    assert denied_drafts.status_code == denied_edit.status_code == denied_contact.status_code == 403
    assert "01011112222" in exported.text and "01099998888" not in exported.text
    assert summary.json()["pipeline"] == {"contacted": 1}
    assert summary.json()["sequence_distribution"] == {"1": 1}
