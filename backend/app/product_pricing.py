from datetime import date, timedelta
from math import isfinite
from urllib.parse import urlsplit

from app.models import Product
from app.schemas import seoul_business_date

PRICE_FRESHNESS_DAYS = 30


def trusted_business_price(
    product: Product, today: date | None = None
) -> tuple[float | None, str, str | None, date | None]:
    verified_at = product.price_verified_at
    current = today or seoul_business_date()
    try:
        price = float(product.price)
        source = urlsplit(product.price_source_url or "")
        valid_source = source.scheme == "https" and bool(source.hostname)
    except (TypeError, ValueError, OverflowError):
        return None, "사업자 가격 상담 필요", None, None
    if (
        product.is_verified
        and product.price_type == "wholesale"
        and isfinite(price)
        and price > 0
        and valid_source
        and verified_at
        and current - timedelta(days=PRICE_FRESHNESS_DAYS) <= verified_at <= current
    ):
        return price, f"사업자 가격 {price:,.0f}원", product.price_source_url, verified_at
    return None, "사업자 가격 상담 필요", None, None
