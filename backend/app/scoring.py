import re
from datetime import datetime, timezone
from typing import Any

INTENT_POINTS = {"구매임박": 100, "정보탐색": 55, "AS·불만": 20}

FIT_PROFILES = {
    "숙박업": ("room_count", "객실", "개", 20, 50),
    "음식점·카페": ("seat_count", "좌석", "석", 30, 80),
    "사무실": ("employee_count", "직원", "명", 20, 100),
    "소매업": ("store_count", "매장", "개", 2, 10),
}

INDUSTRY_ALIASES = {
    **{
        alias: "숙박업"
        for alias in (
            "숙박",
            "숙박업",
            "호텔",
            "모텔",
            "펜션",
            "게스트하우스",
            "리조트",
            "호텔업",
            "관광호텔",
            "관광호텔업",
            "lodging",
            "hotel",
            "motel",
            "pension",
            "guesthouse",
        )
    },
    **{
        alias: "음식점·카페"
        for alias in (
            "음식점",
            "음식점카페",
            "식당",
            "레스토랑",
            "카페",
            "커피숍",
            "restaurant",
            "cafe",
            "café",
        )
    },
    **{alias: "사무실" for alias in ("사무실", "오피스", "office")},
    **{alias: "소매업" for alias in ("소매업", "소매점", "매장", "상점", "retail", "store")},
}


def normalize_industry(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = re.sub(r"[\s·./_-]+", "", value.strip().lower())
    return INDUSTRY_ALIASES.get(normalized)


def _positive_count(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 < value <= 100_000 else None
    if isinstance(value, float):
        return int(value) if value.is_integer() and 0 < value <= 100_000 else None
    if isinstance(value, str):
        stripped = value.strip()
        if len(stripped) <= 6 and stripped.isdecimal():
            count = int(stripped)
            return count if 0 < count <= 100_000 else None
    return None


def calculate_fit(attributes: dict[str, Any]) -> tuple[int, str]:
    if not isinstance(attributes, dict):
        return 50, "업종별 사업 규모 데이터가 부족해 중립 점수를 적용했습니다."
    industry = normalize_industry(attributes.get("business_type"))
    if not industry:
        return 50, "지원하는 업종 정보가 없어 사업 규모 적합도에 중립 점수를 적용했습니다."
    metric, label, unit, medium, high = FIT_PROFILES[industry]
    count = _positive_count(attributes.get(metric))
    if count is None:
        return (
            50,
            f"{industry} 적합도 판단에 필요한 {label} 수 데이터가 부족해 중립 점수를 적용했습니다.",
        )
    score = 90 if count >= high else 60 if count >= medium else 30
    reason = (
        f"{industry}의 {label} 수 {count}{unit}를 기준으로 사업 규모 적합도를 계산했습니다. "
        f"({medium}{unit} 미만 30점, {medium}~{high - 1}{unit} 60점, "
        f"{high}{unit} 이상 90점)"
    )
    return score, reason


def calculate_recency(
    last_interaction_at: datetime | None, inquiry_created_at: datetime
) -> tuple[int, str]:
    if last_interaction_at is None:
        return 20, "이번 문의 이전의 활동 기록이 없는 신규 고객입니다."
    current = inquiry_created_at
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    previous = last_interaction_at
    if previous.tzinfo is None:
        previous = previous.replace(tzinfo=timezone.utc)
    days = max(0, (current - previous).days)
    score = 100 if days <= 7 else 60 if days <= 30 else 30
    return score, f"이번 문의 이전 마지막 활동으로부터 {days}일이 지났습니다."


def calculate_total(fit: int, intent: int, recency: int) -> float:
    return round(fit * 0.35 + intent * 0.50 + recency * 0.15, 2)
