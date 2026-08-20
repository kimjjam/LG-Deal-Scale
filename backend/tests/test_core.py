import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import Account, Assignment, Inquiry, Staff
from app.nl2sql import UnsafeQueryError, validate_sql
from app.schemas import IntentResult
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
    assert settings.database_url == (
        "postgresql+asyncpg://user:pass@example.test/db?ssl=require"
    )


def test_readonly_url_is_derived_from_password() -> None:
    settings = Settings(
        database_url="postgresql://owner:writer@example.test/db?sslmode=require",
        database_readonly_password="readonly password",
        _env_file=None,
    )
    assert settings.effective_database_readonly_url == (
        "postgresql+asyncpg://directdesk_readonly:readonly%20password@"
        "example.test/db?ssl=require"
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


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE accounts",
        "DELETE FROM accounts",
        "UPDATE accounts SET name = 'x'",
        "INSERT INTO accounts (name) VALUES ('x')",
        "SELECT * FROM accounts; DROP TABLE accounts",
        "SELECT secret_column FROM accounts",
    ],
)
def test_nl2sql_rejects_dangerous_queries(sql: str) -> None:
    with pytest.raises(UnsafeQueryError):
        validate_sql(sql)


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
