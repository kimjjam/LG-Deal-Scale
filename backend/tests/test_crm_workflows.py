import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from typing_extensions import Self

from app.database import get_session
from app.models import (
    Account,
    Assignment,
    AuditLog,
    Inquiry,
    Lead,
    Opportunity,
    OpportunityStageHistory,
    OutboundDraft,
    Score,
    Staff,
    Task,
)
from app.routes.accounts import account_overview, create_account, update_account
from app.routes.accounts import router as accounts_router
from app.routes.crm import (
    complete_task,
    create_activity,
    create_task,
    list_opportunities,
    list_tasks,
    update_opportunity,
    update_task,
)
from app.routes.crm import router as crm_router
from app.routes.inquiries import convert_to_opportunity, correct_intent, update_status
from app.routes.outbound import change_stage, convert_lead, review_draft, safe_send
from app.schemas import (
    AccountCreate,
    ActivityCreate,
    InquiryConversionRequest,
    InquiryStatusRequest,
    IntentCorrectionRequest,
    IntentResult,
    LeadConversionRequest,
    LeadStageRequest,
    OpportunityCreate,
    OpportunityUpdate,
    TaskCreate,
    TaskUpdate,
)
from app.security import get_current_staff
from app.services import manually_assign, score_inquiry


class IntentLLM:
    provider = "test"
    model = "test-model"

    async def structured(self, _prompt: str, result_type: type[IntentResult]) -> IntentResult:
        return result_type(category="정보탐색", confidence=0.8, reasoning="정보를 요청함")


async def crm_base(session: AsyncSession) -> tuple[Staff, Staff, Account]:
    manager = Staff(
        id=uuid.uuid4(),
        name="관리자",
        email=f"manager-{uuid.uuid4()}@example.test",
        hashed_password="not-used",
        role="manager",
    )
    rep = Staff(
        id=uuid.uuid4(),
        name="담당자",
        email=f"rep-{uuid.uuid4()}@example.test",
        hashed_password="not-used",
        role="rep",
    )
    account = Account(
        name="가상호텔", phone=str(uuid.uuid4().int)[:11], attributes={"room_count": 20}
    )
    session.add_all([manager, rep, account])
    await session.commit()
    return manager, rep, account


@pytest.mark.asyncio
async def test_inquiry_to_opportunity_full_lifecycle(session: AsyncSession) -> None:
    manager, rep, account = await crm_base(session)
    inquiry = Inquiry(account_id=account.id, channel="web", content="냉장고 20대 견적")
    session.add(inquiry)
    await session.flush()
    session.add(Assignment(inquiry_id=inquiry.id, assignee_id=rep.id, method="round_robin"))
    await session.commit()

    opportunity = await convert_to_opportunity(
        inquiry.id,
        InquiryConversionRequest(title="객실 냉장고 교체", amount=10_000_000),
        session,
        manager,
    )

    assert opportunity.assignee_id == rep.id
    assert inquiry.status == "resolved"
    assert await session.scalar(
        select(OpportunityStageHistory.id).where(
            OpportunityStageHistory.opportunity_id == opportunity.id,
            OpportunityStageHistory.stage == "qualify",
        )
    )
    assert await session.scalar(select(AuditLog.id).where(AuditLog.action == "inquiry.convert"))
    with pytest.raises(HTTPException) as duplicate:
        await convert_to_opportunity(
            inquiry.id,
            InquiryConversionRequest(title="중복 전환"),
            session,
            manager,
        )
    assert duplicate.value.status_code == 409

    await update_status(inquiry.id, InquiryStatusRequest(status="open"), session, manager)
    assert inquiry.status == "open"


@pytest.mark.asyncio
async def test_unassigned_rep_cannot_change_inquiry(session: AsyncSession) -> None:
    manager, assigned_rep, account = await crm_base(session)
    other_rep = Staff(
        id=uuid.uuid4(),
        name="미배정 담당자",
        email=f"unassigned-{uuid.uuid4()}@example.test",
        hashed_password="not-used",
        role="rep",
    )
    inquiry = Inquiry(account_id=account.id, channel="web", content="문의")
    session.add_all([other_rep, inquiry])
    await session.flush()
    session.add(
        Assignment(
            inquiry_id=inquiry.id,
            assignee_id=assigned_rep.id,
            method="round_robin",
        )
    )
    await session.commit()

    with pytest.raises(HTTPException) as forbidden:
        await update_status(inquiry.id, InquiryStatusRequest(status="resolved"), session, other_rep)
    assert forbidden.value.status_code == 403

    resolved = await update_status(
        inquiry.id, InquiryStatusRequest(status="resolved"), session, manager
    )
    assert resolved.status == "resolved"


@pytest.mark.asyncio
async def test_intent_correction_updates_score_and_audit(session: AsyncSession) -> None:
    manager, _, account = await crm_base(session)
    inquiry = Inquiry(account_id=account.id, channel="web", content="문의")
    session.add(inquiry)
    await session.flush()
    score = Score(
        inquiry_id=inquiry.id,
        fit_score=50,
        intent_score=40,
        intent_category="정보탐색",
        intent_confidence=0.5,
        recency_score=20,
        total_score=40.5,
        reasoning={"fit": "기존", "intent": "기존", "recency": "기존"},
        scoring_version="v1",
        llm_provider="test",
        model_name="test",
    )
    session.add(score)
    await session.commit()

    result = await correct_intent(
        inquiry.id,
        IntentCorrectionRequest(category="구매임박", reasoning="수량과 납기를 확인함"),
        session,
        manager,
    )

    assert result["intent_score"] == 100
    assert score.reasoning["intent"].startswith("담당자 수정:")
    assert await session.scalar(
        select(AuditLog.id).where(AuditLog.action == "inquiry.intent_correct")
    )


@pytest.mark.asyncio
async def test_lead_conversion_reuses_account_and_is_idempotent(
    session: AsyncSession,
) -> None:
    manager, rep, account = await crm_base(session)
    lead = Lead(
        name="발굴 호텔",
        address="서울",
        business_type="호텔",
        source="localdata",
        raw_data={},
        lead_score=80,
        lead_score_reasoning={"business_type": "호텔"},
        pipeline_stage="discovered",
    )
    session.add(lead)
    await session.commit()
    payload = LeadConversionRequest(
        phone=account.phone,
        assignee_id=rep.id,
        opportunity_title="신규 교체 제안",
    )

    opportunity = await convert_lead(lead.id, payload, session, manager)

    assert opportunity.account_id == account.id
    assert lead.pipeline_stage == "converted"
    with pytest.raises(HTTPException) as duplicate:
        await convert_lead(lead.id, payload, session, manager)
    assert duplicate.value.status_code == 409


@pytest.mark.asyncio
async def test_activity_changes_recency_score(session: AsyncSession) -> None:
    manager, _, account = await crm_base(session)
    activity = await create_activity(
        ActivityCreate(account_id=account.id, type="call", content="견적 후속 통화"),
        session,
        manager,
    )
    inquiry = Inquiry(
        account_id=account.id,
        channel="web",
        content="가격 문의",
        created_at=activity.created_at + timedelta(days=2),
    )
    session.add(inquiry)
    await session.commit()

    score = await score_inquiry(session, inquiry.id, IntentLLM())

    assert score.recency_score == 100
    assert "2일" in score.reasoning["recency"]


@pytest.mark.asyncio
async def test_task_filters_completion_and_rbac(session: AsyncSession) -> None:
    manager, rep, account = await crm_base(session)
    other = Staff(
        id=uuid.uuid4(),
        name="다른 담당자",
        email=f"other-{uuid.uuid4()}@example.test",
        hashed_password="not-used",
        role="rep",
    )
    session.add(other)
    await session.commit()
    overdue = await create_task(
        TaskCreate(
            account_id=account.id,
            assignee_id=rep.id,
            title="기한 지난 전화",
            due_at=datetime.now(timezone.utc) - timedelta(days=1),
        ),
        session,
        manager,
    )
    other_task = await create_task(
        TaskCreate(
            account_id=account.id,
            assignee_id=other.id,
            title="다른 담당자 업무",
            due_at=datetime.now(timezone.utc) + timedelta(days=1),
        ),
        session,
        manager,
    )

    mine = await list_tasks(session, rep, "mine", None, "pending", None, True, 50, 0)
    assert [task.id for task in mine] == [overdue.id]
    completed = await complete_task(overdue.id, session, rep)
    assert completed.status == "completed"
    assert completed.completed_at is not None
    await complete_task(overdue.id, session, rep)
    assert (
        len(
            (
                await session.scalars(select(AuditLog).where(AuditLog.action == "task.complete"))
            ).all()
        )
        == 1
    )

    with pytest.raises(HTTPException) as same_account_complete:
        await complete_task(other_task.id, session, rep)
    assert same_account_complete.value.status_code == 403
    with pytest.raises(HTTPException) as same_account_takeover:
        await update_task(
            other_task.id,
            TaskUpdate(assignee_id=rep.id, title="가로채기"),
            session,
            rep,
        )
    assert same_account_takeover.value.status_code == 403

    foreign_account = Account(name="다른 고객", phone=str(uuid.uuid4().int)[:11], attributes={})
    session.add(foreign_account)
    await session.flush()
    foreign_task = Task(
        account_id=foreign_account.id,
        assignee_id=other.id,
        title="다른 고객 업무",
        due_at=datetime.now(timezone.utc),
    )
    session.add(foreign_task)
    await session.commit()
    assert foreign_task is not None
    with pytest.raises(HTTPException) as forbidden:
        await complete_task(foreign_task.id, session, rep)
    assert forbidden.value.status_code == 403


@pytest.mark.asyncio
async def test_customer_overview_and_opportunity_filters(session: AsyncSession) -> None:
    manager, rep, account = await crm_base(session)
    opportunity = Opportunity(
        account_id=account.id,
        assignee_id=rep.id,
        title="가전 교체",
        stage="qualify",
        probability=10,
    )
    session.add(opportunity)
    await session.commit()

    rows = await list_opportunities(session, manager, "교체", "qualify", rep.id, account.id, 1, 0)
    overview = await account_overview(account.id, session, manager)

    assert [row.id for row in rows] == [opportunity.id]
    assert overview["account"]["id"] == account.id
    assert [item["id"] for item in overview["opportunities"]] == [opportunity.id]
    assert overview["timeline"][0]["kind"] == "opportunity"


@pytest.mark.asyncio
async def test_lost_stage_requires_reason_and_rep_cannot_edit_foreign_deal(
    session: AsyncSession,
) -> None:
    manager, rep, account = await crm_base(session)
    other = Staff(
        id=uuid.uuid4(),
        name="다른 담당자",
        email=f"deal-other-{uuid.uuid4()}@example.test",
        hashed_password="not-used",
        role="rep",
    )
    opportunity = Opportunity(
        account_id=account.id,
        assignee_id=other.id,
        title="타인 딜",
        stage="qualify",
        probability=10,
    )
    session.add(other)
    await session.flush()
    own_opportunity = Opportunity(
        account_id=account.id,
        assignee_id=rep.id,
        title="내 딜",
        stage="qualify",
        probability=10,
    )
    session.add_all([opportunity, own_opportunity])
    await session.commit()

    with pytest.raises(HTTPException) as forbidden:
        await update_opportunity(opportunity.id, OpportunityUpdate(title="침범"), session, rep)
    assert forbidden.value.status_code == 403
    with pytest.raises(HTTPException) as takeover:
        await update_opportunity(
            opportunity.id,
            OpportunityUpdate(assignee_id=rep.id, title="가로채기"),
            session,
            rep,
        )
    assert takeover.value.status_code == 403
    with pytest.raises(HTTPException) as missing_reason:
        await update_opportunity(opportunity.id, OpportunityUpdate(stage="lost"), session, manager)
    assert missing_reason.value.status_code == 422

    lost = await update_opportunity(
        opportunity.id,
        OpportunityUpdate(stage="lost", loss_reason="예산 취소"),
        session,
        manager,
    )
    assert lost.stage == "lost"
    with pytest.raises(HTTPException) as terminal:
        await update_opportunity(
            opportunity.id, OpportunityUpdate(stage="develop"), session, manager
        )
    assert terminal.value.status_code == 409


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/crm/opportunities/1", {"title": None}),
        ("/api/crm/opportunities/1", {"stage": None}),
        ("/api/crm/opportunities/1", {"probability": None}),
        ("/api/crm/tasks/1", {"title": None}),
        ("/api/crm/tasks/1", {"due_at": None}),
        ("/api/crm/tasks/1", {"status": None}),
    ],
)
def test_patch_rejects_explicit_null(path: str, body: dict[str, object]) -> None:
    app = FastAPI()
    app.include_router(crm_router)
    staff = Staff(
        id=uuid.uuid4(),
        name="관리자",
        email="null-check@example.test",
        hashed_password="not-used",
        role="manager",
    )

    async def override_staff() -> Staff:
        return staff

    async def override_session() -> AsyncIterator[None]:
        yield None

    app.dependency_overrides[get_current_staff] = override_staff
    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as client:
        response = client.patch(path, json=body)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_rep_cannot_pollute_unowned_account(session: AsyncSession) -> None:
    manager, assigned_rep, account = await crm_base(session)
    other_rep = Staff(
        id=uuid.uuid4(),
        name="다른 담당자",
        email=f"scope-{uuid.uuid4()}@example.test",
        hashed_password="not-used",
        role="rep",
    )
    inquiry = Inquiry(account_id=account.id, channel="web", content="비공개 문의")
    session.add_all([other_rep, inquiry])
    await session.flush()
    session.add(Assignment(inquiry_id=inquiry.id, assignee_id=assigned_rep.id, method="manual"))
    await session.commit()

    with pytest.raises(HTTPException) as activity_error:
        await create_activity(
            ActivityCreate(account_id=account.id, type="note", content="침범"),
            session,
            other_rep,
        )
    assert activity_error.value.status_code == 403
    with pytest.raises(HTTPException) as overview_error:
        await account_overview(account.id, session, other_rep)
    assert overview_error.value.status_code == 403

    overview = await account_overview(account.id, session, manager)
    assert overview["inquiries"][0]["content"] == "비공개 문의"
    assert "raw_conversation" not in overview["inquiries"][0]


@pytest.mark.asyncio
async def test_assignees_must_be_active_reps_and_latest_assignment_wins(
    session: AsyncSession,
) -> None:
    manager, rep, account = await crm_base(session)
    with pytest.raises(HTTPException) as invalid_task_assignee:
        await create_task(
            TaskCreate(
                account_id=account.id,
                assignee_id=manager.id,
                title="관리자에게 배정",
                due_at=datetime.now(timezone.utc),
            ),
            session,
            manager,
        )
    assert invalid_task_assignee.value.status_code == 404

    inactive = Staff(
        id=uuid.uuid4(),
        name="비활성 담당자",
        email=f"inactive-{uuid.uuid4()}@example.test",
        hashed_password="not-used",
        role="rep",
        is_active=False,
    )
    inquiry = Inquiry(account_id=account.id, channel="web", content="문의")
    session.add_all([inactive, inquiry])
    await session.flush()
    session.add_all(
        [
            Assignment(
                inquiry_id=inquiry.id,
                assignee_id=rep.id,
                assigned_at=datetime.now(timezone.utc) - timedelta(minutes=1),
                method="manual",
            ),
            Assignment(
                inquiry_id=inquiry.id,
                assignee_id=inactive.id,
                assigned_at=datetime.now(timezone.utc),
                method="manual",
            ),
        ]
    )
    await session.commit()

    with pytest.raises(ValueError, match="Sales representative not found"):
        await manually_assign(session, inquiry, inactive.id)

    with pytest.raises(HTTPException) as inactive_latest:
        await convert_to_opportunity(
            inquiry.id,
            InquiryConversionRequest(title="전환"),
            session,
            manager,
        )
    assert inactive_latest.value.status_code == 422


def test_opportunity_amount_rejects_numeric_overflow() -> None:
    excessive = 1_000_000_000_000
    with pytest.raises(ValueError):
        OpportunityCreate(
            account_id=1,
            assignee_id=uuid.uuid4(),
            title="한도 초과",
            amount=excessive,
        )
    with pytest.raises(ValueError):
        OpportunityUpdate(amount=excessive)
    with pytest.raises(ValueError):
        InquiryConversionRequest(title="한도 초과", amount=excessive)
    with pytest.raises(ValueError):
        LeadConversionRequest(
            phone="0212345678",
            assignee_id=uuid.uuid4(),
            opportunity_title="한도 초과",
            amount=excessive,
        )
    with pytest.raises(ValueError):
        OpportunityUpdate(amount="1.001")


@pytest.mark.asyncio
async def test_safe_send_is_idempotent_and_does_not_fake_contact(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, _, _ = await crm_base(session)
    lead = Lead(
        name="발굴 고객",
        source="localdata",
        raw_data={},
        lead_score=50,
        lead_score_reasoning={},
        pipeline_stage="approved",
    )
    session.add(lead)
    await session.flush()
    draft = OutboundDraft(
        lead_id=lead.id,
        sequence_step=1,
        subject="테스트",
        body="본문",
        reviewed_by=manager.id,
        send_mode="dry_run",
    )
    session.add(draft)
    await session.commit()
    monkeypatch.setattr(
        "app.routes.outbound.get_settings",
        lambda: SimpleNamespace(outbound_email_mode="dry_run"),
    )

    await safe_send(draft.id, session, manager)
    assert lead.pipeline_stage == "approved"
    with pytest.raises(HTTPException) as direct_approval:
        await change_stage(
            lead.id,
            LeadStageRequest(pipeline_stage="approved"),
            session,
            manager,
        )
    assert direct_approval.value.status_code == 422
    with pytest.raises(HTTPException) as regression:
        await change_stage(
            lead.id,
            LeadStageRequest(pipeline_stage="discovered"),
            session,
            manager,
        )
    assert regression.value.status_code == 409
    with pytest.raises(HTTPException) as duplicate:
        await safe_send(draft.id, session, manager)
    assert duplicate.value.status_code == 409

    captured_headers: dict[str, str] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

    class FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(
            self, _url: str, *, headers: dict[str, str], json: dict[str, object]
        ) -> FakeResponse:
            del json
            captured_headers.update(headers)
            return FakeResponse()

    test_draft = OutboundDraft(
        lead_id=lead.id,
        sequence_step=2,
        previous_draft_id=draft.id,
        subject="테스트 발송",
        body="본문",
        reviewed_by=manager.id,
        send_mode="test_override",
    )
    session.add(test_draft)
    await session.commit()
    monkeypatch.setattr("app.routes.outbound.httpx.AsyncClient", FakeClient)
    monkeypatch.setattr(
        "app.routes.outbound.get_settings",
        lambda: SimpleNamespace(
            outbound_email_mode="test_override",
            test_email_address="safe@example.test",
            email_provider_api_key="not-a-real-secret",
        ),
    )
    await safe_send(test_draft.id, session, manager)
    assert captured_headers["Idempotency-Key"] == f"directdesk-draft-{test_draft.id}"
    assert lead.pipeline_stage == "approved"

    lead.pipeline_stage = "converted"
    draft.sent_at = None
    await session.commit()
    with pytest.raises(HTTPException) as terminal_review:
        await review_draft(draft.id, session, manager)
    assert terminal_review.value.status_code == 409
    with pytest.raises(HTTPException) as terminal_stage:
        await change_stage(
            lead.id,
            LeadStageRequest(pipeline_stage="discovered"),
            session,
            manager,
        )
    assert terminal_stage.value.status_code == 409


@pytest.mark.asyncio
async def test_sqlite_foreign_keys_are_enforced(session: AsyncSession) -> None:
    task = Task(
        account_id=999999,
        assignee_id=uuid.uuid4(),
        title="잘못된 참조",
        due_at=datetime.now(timezone.utc),
    )
    session.add(task)
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


@pytest.mark.asyncio
async def test_account_unique_race_returns_409() -> None:
    staff = Staff(
        id=uuid.uuid4(),
        name="관리자",
        email="race@example.test",
        hashed_password="not-used",
        role="manager",
    )
    payload = AccountCreate(name="동시 고객", phone="01012345678")

    class ConflictSession:
        def __init__(self, scalar_results: list[object]) -> None:
            self.scalar_results = scalar_results
            self.rolled_back = False

        async def scalar(self, _statement: object) -> object:
            return self.scalar_results.pop(0)

        def add(self, _record: object) -> None:
            return None

        async def commit(self) -> None:
            raise IntegrityError("statement", {}, Exception("duplicate"))

        async def rollback(self) -> None:
            self.rolled_back = True

    create_session = ConflictSession([None])
    with pytest.raises(HTTPException) as create_error:
        await create_account(payload, create_session, staff)  # type: ignore[arg-type]
    assert create_error.value.status_code == 409
    assert create_session.rolled_back

    account = Account(id=1, name="기존 고객", phone="01000000000", attributes={})
    update_session = ConflictSession([account, None])
    with pytest.raises(HTTPException) as update_error:
        await update_account(1, payload, update_session, staff)  # type: ignore[arg-type]
    assert update_error.value.status_code == 409
    assert update_session.rolled_back


@pytest.mark.parametrize("query", ["limit=201", "limit=0", "offset=-1"])
def test_account_pagination_bounds(query: str) -> None:
    app = FastAPI()
    app.include_router(accounts_router)
    staff = Staff(
        id=uuid.uuid4(),
        name="담당자",
        email="pagination@example.test",
        hashed_password="not-used",
        role="rep",
    )

    async def override_staff() -> Staff:
        return staff

    async def override_session() -> AsyncIterator[None]:
        yield None

    app.dependency_overrides[get_current_staff] = override_staff
    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as client:
        response = client.get(f"/api/accounts?{query}")
    assert response.status_code == 422
