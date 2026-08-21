from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, Inquiry, Score
from app.schemas import IntentResult
from app.scoring import calculate_fit
from app.services import score_inquiry


@pytest.mark.parametrize(
    ("attributes", "expected_score", "reason_part"),
    [
        ({"business_type": "호텔", "room_count": 50}, 90, "객실 수 50개"),
        ({"business_type": "lodging", "room_count": 20}, 60, "객실 수 20개"),
        ({"business_type": "카페", "seat_count": 29}, 30, "좌석 수 29석"),
        ({"business_type": "restaurant", "seat_count": 80}, 90, "좌석 수 80석"),
        ({"business_type": "office", "employee_count": 20}, 60, "직원 수 20명"),
        ({"business_type": "소매점", "store_count": 10}, 90, "매장 수 10개"),
    ],
)
def test_industry_fit_profiles(
    attributes: dict[str, object], expected_score: int, reason_part: str
) -> None:
    score, reason = calculate_fit(attributes)
    assert score == expected_score
    assert reason_part in reason


@pytest.mark.parametrize(
    "attributes",
    [
        {},
        {"business_type": "제조업", "room_count": 1000},
        {"business_type": "호텔"},
        {"business_type": "호텔", "room_count": True},
        {"business_type": "카페", "seat_count": "많음"},
        {"business_type": "office", "employee_count": -1},
        {"business_type": "호텔", "room_count": "9" * 100_000},
        {"business_type": "호텔", "room_count": "100001"},
        {"business_type": "호텔", "room_count": float("inf")},
    ],
)
def test_industry_fit_uses_neutral_score_for_insufficient_data(
    attributes: dict[str, object],
) -> None:
    score, reason = calculate_fit(attributes)
    assert score == 50
    assert "중립 점수" in reason


@pytest.mark.parametrize("business_type", ["리조트", "관광 호텔업", "guest-house"])
def test_lodging_aliases_share_the_same_profile(business_type: str) -> None:
    score, reason = calculate_fit({"business_type": business_type, "room_count": 20})
    assert score == 60
    assert "숙박업" in reason


class IntentLLM:
    provider = "test"
    model = "test-model"

    def __init__(self) -> None:
        self.prompt = ""

    async def structured(self, prompt: str, result_type: type[IntentResult]) -> IntentResult:
        self.prompt = prompt
        return result_type(category="정보탐색", confidence=0.8, reasoning="정보를 요청함")


@pytest.mark.asyncio
async def test_score_inquiry_keeps_fit_and_intent_inputs_isolated(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    attributes = {
        "business_type": "office",
        "employee_count": 25,
        "purchase_stage": "FIT에만 전달되는 표식",
    }
    account = Account(name="가상사무실", phone="01077778888", attributes=attributes)
    session.add(account)
    await session.flush()
    inquiry = Inquiry(
        account_id=account.id,
        channel="web",
        content="INTENT에만 전달되는 문의",
        created_at=datetime.now(timezone.utc),
    )
    session.add(inquiry)
    await session.commit()

    received: list[dict[str, object]] = []

    def recording_fit(value: dict[str, object]) -> tuple[int, str]:
        received.append(value)
        return 60, "사업 규모 근거"

    monkeypatch.setattr("app.services.calculate_fit", recording_fit)
    llm = IntentLLM()
    score = await score_inquiry(session, inquiry.id, llm)

    assert received == [attributes]
    assert "INTENT에만 전달되는 문의" in llm.prompt
    assert "FIT에만 전달되는 표식" not in llm.prompt
    assert score.scoring_version == "v2"

    score_id = score.id
    score.scoring_version = "v1"
    await session.commit()
    assert await session.scalar(select(Score.scoring_version).where(Score.id == score_id)) == "v1"

    rescored = await score_inquiry(session, inquiry.id, llm)
    assert rescored.id == score_id
    assert rescored.scoring_version == "v2"
