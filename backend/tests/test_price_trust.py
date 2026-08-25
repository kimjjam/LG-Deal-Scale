import uuid
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Lead, Product, Staff
from app.product_pricing import PRICE_FRESHNESS_DAYS, trusted_business_price, trusted_public_price
from app.routes.outbound import generate_draft
from app.schemas import seoul_business_date


def product(name: str, price: int, price_type: str, *, verified: bool = True) -> Product:
    return Product(
        name=name,
        brand="LG",
        category="에어컨",
        price=Decimal(price),
        price_type=price_type,
        price_source_url=f"https://example.test/{name}",
        price_verified_at=seoul_business_date(),
        is_verified=verified,
        product_url=f"https://example.test/products/{name}",
    )


def test_numeric_business_price_uses_kst_date_and_inclusive_30_day_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    today = date(2026, 8, 21)
    monkeypatch.setattr("app.product_pricing.seoul_business_date", lambda: today)
    wholesale = product("도매", 432100, "wholesale")
    wholesale.price_verified_at = today - timedelta(days=PRICE_FRESHNESS_DAYS)
    assert trusted_business_price(wholesale) == (
        432100.0,
        "사업자 가격 432,100원",
        "https://example.test/도매",
        date(2026, 7, 22),
    )
    wholesale.price_verified_at = today - timedelta(days=PRICE_FRESHNESS_DAYS + 1)
    assert trusted_business_price(wholesale) == (
        None,
        "사업자 가격 상담 필요",
        None,
        None,
    )


def test_public_price_exposes_only_fresh_verified_retail_reference() -> None:
    today = date(2026, 8, 21)
    retail = product("공식몰", 987654, "retail_reference")
    retail.price_verified_at = today
    assert trusted_public_price(retail, today) == (
        987654.0,
        "공식몰 참고가 987,654원",
        "https://example.test/공식몰",
        today,
    )
    retail.price_verified_at = today - timedelta(days=PRICE_FRESHNESS_DAYS + 1)
    assert trusted_public_price(retail, today)[0] is None
    retail.price_verified_at = today
    retail.is_verified = False
    assert trusted_public_price(retail, today)[0] is None


@pytest.mark.parametrize(
    ("price", "source"),
    [
        (Decimal(0), "https://example.test/quote"),
        (Decimal(-1), "https://example.test/quote"),
        (Decimal("NaN"), "https://example.test/quote"),
        (Decimal("Infinity"), "https://example.test/quote"),
        (Decimal(1), "http://example.test/quote"),
        (Decimal(1), "https:///quote"),
        (Decimal(1), "https://["),
    ],
)
def test_invalid_price_or_source_is_never_exposed(price: Decimal, source: str) -> None:
    wholesale = product("invalid", 1, "wholesale")
    wholesale.price = price
    wholesale.price_source_url = source
    assert trusted_business_price(wholesale) == (
        None,
        "사업자 가격 상담 필요",
        None,
        None,
    )


@pytest.mark.asyncio
async def test_outbound_prompt_excludes_unverified_identity_and_retail_number(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = Staff(
        id=uuid.uuid4(),
        name="매니저",
        email="manager-price@example.test",
        hashed_password="not-used",
        role="manager",
    )
    lead = Lead(name="가상호텔", raw_data={}, lead_score=70, lead_score_reasoning={})
    session.add_all(
        [
            manager,
            lead,
            product("검증도매", 432100, "wholesale"),
            product("검증소매", 987654, "retail_reference"),
            product("미검증", 765432, "wholesale", verified=False),
        ]
    )
    await session.commit()
    prompts: list[str] = []

    class LLM:
        async def structured(self, prompt: str, result_type: type) -> object:
            prompts.append(prompt)
            return result_type(subject="제목", body="본문")

    monkeypatch.setattr("app.routes.outbound.get_llm_client", lambda: LLM())
    monkeypatch.setattr(
        "app.routes.outbound.get_settings",
        lambda: SimpleNamespace(outbound_email_mode="dry_run"),
    )
    await generate_draft(lead.id, session, manager)

    assert "발신 담당자: 매니저" in prompts[0]
    assert "공급 계약을 제안한다는 목적" in prompts[0]
    assert "연락 요청은 쓰지 마세요" in prompts[0]
    assert "검증도매" in prompts[0] and "432,100" in prompts[0]
    assert "검증소매" in prompts[0] and "987654" not in prompts[0]
    assert "미검증" not in prompts[0] and "765432" not in prompts[0]
