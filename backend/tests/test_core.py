import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import Account, Assignment, Inquiry, Opportunity, Staff, Task
from app.nl2sql import UnsafeQueryError, validate_sql
from app.routes.accounts import create_account
from app.schemas import AccountCreate, IntakeFields, IntentResult, normalize_phone
from app.services import auto_assign, classify_intent, create_inquiry


class RecordingLLM:
    provider = "test"
    model = "test-model"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.prompt = ""

    async def structured(self, prompt: str, result_type: type[IntentResult]) -> IntentResult:
        self.prompt = prompt
        if self.fail:
            raise RuntimeError("simulated failure")
        return result_type(category="구매임박", confidence=0.9, reasoning="구매 수량을 명시함")

    async def text(self, prompt: str) -> str:
        return prompt


def test_neon_url_uses_asyncpg_driver() -> None:
    settings = Settings(
        database_url=(
            "postgresql://user:pass@example.test/db?sslmode=require&channel_binding=require"
        ),
        _env_file=None,
    )
    assert settings.database_url == ("postgresql+asyncpg://user:pass@example.test/db?ssl=require")


def test_readonly_url_is_derived_from_password() -> None:
    settings = Settings(
        database_url="postgresql://owner:writer@example.test/db?sslmode=require",
        database_readonly_password="readonly password",
        _env_file=None,
    )
    assert settings.effective_database_readonly_url == (
        "postgresql+asyncpg://directdesk_readonly:readonly%20password@example.test/db?ssl=require"
    )


@pytest.mark.asyncio
async def test_existing_open_account_inquiry_keeps_assignee(session: AsyncSession) -> None:
    rep = Staff(
        id=uuid.uuid4(),
        name="담당자",
        email="rep@example.test",
        hashed_password="not-used",
        role="rep",
    )
    account = Account(name="가상호텔", phone="01000000000", attributes={})
    session.add_all([rep, account])
    await session.flush()
    prior = Inquiry(account_id=account.id, channel="web", content="이전 문의", status="routed")
    current = Inquiry(account_id=account.id, channel="web", content="새 문의")
    session.add_all([prior, current])
    await session.flush()
    session.add(Assignment(inquiry_id=prior.id, assignee_id=rep.id, method="round_robin"))
    await session.flush()

    assignment = await auto_assign(session, current)

    assert assignment.assignee_id == rep.id


@pytest.mark.asyncio
async def test_auto_assign_falls_back_when_latest_prior_assignee_is_inactive(
    session: AsyncSession,
) -> None:
    active = Staff(
        id=uuid.uuid4(),
        name="활성 담당자",
        email="active-fallback@example.test",
        hashed_password="not-used",
        role="rep",
    )
    inactive = Staff(
        id=uuid.uuid4(),
        name="비활성 담당자",
        email="inactive-fallback@example.test",
        hashed_password="not-used",
        role="rep",
        is_active=False,
    )
    account = Account(name="배정 테스트", phone="01000000001", attributes={})
    session.add_all([active, inactive, account])
    await session.flush()
    prior = Inquiry(account_id=account.id, channel="web", content="이전", status="routed")
    current = Inquiry(account_id=account.id, channel="web", content="현재")
    session.add_all([prior, current])
    await session.flush()
    session.add_all(
        [
            Assignment(inquiry_id=prior.id, assignee_id=active.id, method="manual"),
            Assignment(inquiry_id=prior.id, assignee_id=inactive.id, method="manual"),
        ]
    )
    await session.flush()

    assignment = await auto_assign(session, current)

    assert assignment.assignee_id == active.id


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE accounts",
        "DELETE FROM accounts",
        "UPDATE accounts SET name = 'x'",
        "INSERT INTO accounts (name) VALUES ('x')",
        "SELECT * FROM accounts; DROP TABLE accounts",
        "SELECT accounts.* FROM accounts",
        "SELECT pg_sleep(10) FROM accounts",
        "SELECT accounts.name FROM arbitrary.accounts",
        "SELECT secret_column FROM accounts",
    ],
)
def test_nl2sql_rejects_dangerous_queries(sql: str) -> None:
    with pytest.raises(UnsafeQueryError):
        validate_sql(sql)


def test_phone_normalization() -> None:
    assert normalize_phone("010-1234-5678") == "01012345678"
    assert normalize_phone("010-1234-５６７８") == "0101234"
    assert IntakeFields(phone="010 9876 5432").phone == "01098765432"
    with pytest.raises(ValueError):
        normalize_phone("12-34")


def test_migration_checks_phone_length_before_normalizing() -> None:
    source = (Path(__file__).parents[1] / "alembic" / "versions" / "0003_crm_core.py").read_text(
        encoding="utf-8"
    )
    account_check = "normalized values must contain 7 to 15 digits"
    contact_check = "non-null normalized values must contain 7 to 15 digits"
    assert "NOT BETWEEN 7 AND 15" in source
    assert source.index(account_check) < source.index("UPDATE accounts SET phone")
    assert source.index(contact_check) < source.index("UPDATE contacts SET phone")


def test_nl2sql_allows_public_date_trunc() -> None:
    sql = validate_sql("SELECT DATE_TRUNC('month', accounts.created_at) FROM public.accounts")
    assert "DATE_TRUNC" in sql


@pytest.mark.asyncio
async def test_account_create_normalizes_phone(session: AsyncSession) -> None:
    staff = Staff(
        id=uuid.uuid4(),
        name="담당자",
        email="normalize@example.test",
        hashed_password="not-used",
        role="rep",
    )
    session.add(staff)
    account = await create_account(
        AccountCreate(name="가상호텔", phone="010-9876-5432"), session, staff
    )
    assert account.phone == "01098765432"


@pytest.mark.asyncio
async def test_opportunity_probability_constraint(session: AsyncSession) -> None:
    staff = Staff(
        id=uuid.uuid4(),
        name="담당자",
        email="opportunity@example.test",
        hashed_password="not-used",
        role="rep",
    )
    account = Account(name="가상모텔", phone="01033334444", attributes={})
    session.add_all([staff, account])
    await session.flush()
    opportunity = Opportunity(
        account_id=account.id,
        assignee_id=staff.id,
        title="객실 가전 교체",
        probability=101,
    )
    session.add(opportunity)
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


@pytest.mark.asyncio
async def test_task_model_defaults(session: AsyncSession) -> None:
    staff = Staff(
        id=uuid.uuid4(),
        name="담당자",
        email="task@example.test",
        hashed_password="not-used",
        role="rep",
    )
    account = Account(name="가상펜션", phone="01055556666", attributes={})
    session.add_all([staff, account])
    await session.flush()
    task = Task(
        account_id=account.id,
        assignee_id=staff.id,
        title="견적 전화",
        due_at=datetime.now(timezone.utc),
    )
    session.add(task)
    await session.commit()
    assert task.status == "pending"


@pytest.mark.asyncio
async def test_intent_only_receives_inquiry_content() -> None:
    llm = RecordingLLM()
    await classify_intent("냉장고 20대를 이번 달에 구매합니다", llm)
    assert "냉장고 20대" in llm.prompt
    assert "room_count" not in llm.prompt


@pytest.mark.asyncio
async def test_llm_failure_does_not_rollback_inquiry(session: AsyncSession) -> None:
    account = Account(name="가상펜션", phone="01011112222", attributes={"room_count": 8})
    session.add(account)
    await session.commit()
    inquiry, scoring_failed = await create_inquiry(
        session,
        account.id,
        "public_web",
        "에어컨 견적 문의",
        None,
        RecordingLLM(fail=True),
    )
    stored = await session.scalar(select(Inquiry).where(Inquiry.id == inquiry.id))
    assert stored is not None
    assert scoring_failed is True
