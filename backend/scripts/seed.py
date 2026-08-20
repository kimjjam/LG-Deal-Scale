import asyncio
import json
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal, engine
from app.models import Account, Inquiry, Lead, Product, Score, Staff
from app.scoring import INTENT_POINTS, calculate_fit, calculate_total
from app.security import hash_password
from app.services import auto_assign

ROOT = Path(__file__).resolve().parents[1]


def lead_score(years: int, business_type: str) -> tuple[int, dict[str, str]]:
    years_points = 60 if years >= 15 else 40 if years >= 8 else 25
    type_points = 40 if business_type in {"호텔", "모텔"} else 25
    return years_points + type_points, {
        "years_in_business": f"업력 {years}년을 기준으로 {years_points}점을 부여했습니다.",
        "business_type": f"업종 {business_type}을 기준으로 {type_points}점을 부여했습니다.",
    }


async def main() -> None:
    password = get_settings().seed_staff_password
    if not password:
        raise RuntimeError("SEED_STAFF_PASSWORD is required")
    bundle = json.loads((ROOT / "seed" / "generated_seed.json").read_text(encoding="utf-8"))
    products = json.loads((ROOT / "seed" / "products.json").read_text(encoding="utf-8"))
    async with SessionLocal() as session:
        for item in bundle["staff"]:
            if not await session.scalar(select(Staff.id).where(Staff.email == item["email"])):
                session.add(Staff(**item, hashed_password=hash_password(password)))
        await session.commit()
        for item in products:
            if not await session.scalar(
                select(Product.id).where(Product.product_url == item["product_url"])
            ):
                session.add(Product(**item))
        await session.commit()
        for item in bundle["accounts"]:
            account = await session.scalar(select(Account).where(Account.phone == item["phone"]))
            if not account:
                account = Account(
                    name=item["name"], phone=item["phone"], attributes=item["attributes"]
                )
                session.add(account)
                await session.flush()
            inquiry_data = item["inquiry"]
            inquiry = await session.scalar(
                select(Inquiry).where(
                    Inquiry.account_id == account.id, Inquiry.content == inquiry_data["content"]
                )
            )
            if inquiry:
                continue
            inquiry = Inquiry(
                account_id=account.id, channel="seed", content=inquiry_data["content"]
            )
            session.add(inquiry)
            await session.flush()
            fit, fit_reason = calculate_fit(account.attributes)
            intent = INTENT_POINTS[inquiry_data["intent_category"]]
            session.add(
                Score(
                    inquiry_id=inquiry.id,
                    fit_score=fit,
                    intent_score=intent,
                    intent_category=inquiry_data["intent_category"],
                    intent_confidence=0.9,
                    recency_score=20,
                    total_score=calculate_total(fit, intent, 20),
                    reasoning={
                        "fit": fit_reason,
                        "intent": "고정 시드의 분류 라벨과 문의 표현이 일치합니다.",
                        "recency": "이번 문의 이전 활동이 없는 신규 고객입니다.",
                    },
                    scoring_version="v1-seed",
                    llm_provider="seed",
                    model_name="fixed-json",
                )
            )
            await auto_assign(session, inquiry)
        today = datetime.now(timezone.utc).date()
        for item in bundle["leads"]:
            if await session.scalar(select(Lead.id).where(Lead.name == item["name"])):
                continue
            licensed = date.fromisoformat(item["license_date"])
            years = today.year - licensed.year - ((today.month, today.day) < (licensed.month, licensed.day))
            score, reasoning = lead_score(years, item["business_type"])
            session.add(
                Lead(
                    name=item["name"],
                    address=item["address"],
                    license_date=licensed,
                    years_in_business=years,
                    business_type=item["business_type"],
                    raw_data={"source": "fixed-fictional-seed"},
                    lead_score=score,
                    lead_score_reasoning=reasoning,
                    lead_scoring_version="v1",
                )
            )
        await session.commit()


async def run() -> None:
    try:
        await main()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())
