from datetime import datetime, timezone
from typing import Any

INTENT_POINTS = {"구매임박": 100, "정보탐색": 55, "AS·불만": 20}


def calculate_fit(attributes: dict[str, Any]) -> tuple[int, str]:
    room_count = int(attributes.get("room_count") or 0)
    renovation = str(attributes.get("renovation_status") or "").lower()
    room_points = 70 if room_count >= 50 else 50 if room_count >= 20 else 30 if room_count > 0 else 15
    renovation_points = 30 if renovation in {"planned", "진행예정", "진행중"} else 10
    score = min(100, room_points + renovation_points)
    return score, f"객실 규모 {room_count}개와 리노베이션 상태만으로 적합도를 계산했습니다."


def calculate_recency(last_interaction_at: datetime | None, inquiry_created_at: datetime) -> tuple[int, str]:
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
