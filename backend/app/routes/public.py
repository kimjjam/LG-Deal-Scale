import html
import re
from datetime import timedelta
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_session
from app.llm import get_llm_client
from app.models import Account, Inquiry, Partner, Product, utcnow
from app.product_pricing import trusted_public_price
from app.prompts import intake_prompt
from app.schemas import (
    ChatTurnRequest,
    ChatTurnResponse,
    IntakeFields,
    NearbyStoreStatus,
    ProductRecommendation,
    PublicSubmissionRequest,
    PublicSubmissionResponse,
    normalize_region_text,
)
from app.scoring import FIT_PROFILES, normalize_industry
from app.services import create_inquiry, curated_partner_id, regional_manager_id

router = APIRouter(prefix="/api/public", tags=["public"])
limiter = Limiter(key_func=get_remote_address)
Session = Annotated[AsyncSession, Depends(get_session)]
FINGERPRINT_FIELDS = (
    "inquiry",
    "business_type",
    "room_count",
    "seat_count",
    "employee_count",
    "store_count",
    "product",
    "quantity",
    "location",
    "purchase_stage",
    "purchase_timing",
)


class ChatAIResult(BaseModel):
    message: str
    fields: IntakeFields


def _public_price(product: Product) -> tuple[float | None, str]:
    price, label, _, _ = trusted_public_price(product)
    return price, label


def _intake_complete(fields: IntakeFields) -> bool:
    required = all(
        (
            fields.business_name,
            fields.phone,
            fields.inquiry,
            fields.business_type,
            fields.purchase_stage,
            fields.purchase_timing,
        )
    )
    if fields.purchase_stage in {"견적 요청", "모델 비교"} and not (
        fields.product
        and isinstance(fields.quantity, int)
        and fields.quantity > 0
        and fields.location
    ):
        return False
    industry = normalize_industry(fields.business_type)
    if not required or not industry:
        return required
    metric = FIT_PROFILES[industry][0]
    count = getattr(fields, metric)
    return isinstance(count, int) and count > 0


def _is_lodging(business_type: str | None) -> bool:
    return normalize_industry(business_type) == "숙박업"


def _intake_attributes(fields: IntakeFields) -> dict[str, str | int]:
    attributes: dict[str, str | int] = {}
    if fields.business_type:
        attributes["business_type"] = fields.business_type
    if fields.location:
        attributes["location"] = normalize_region_text(fields.location)
    industry = normalize_industry(fields.business_type)
    if industry:
        metric = FIT_PROFILES[industry][0]
        value = getattr(fields, metric)
        if value is not None:
            attributes[metric] = value
    return attributes


def _relevant_products(products: list[Product], fields: IntakeFields) -> list[Product]:
    haystack = " ".join(filter(None, (fields.product, fields.inquiry))).lower()
    terms = [term for term in re.findall(r"[0-9a-z가-힣]+", haystack) if len(term) > 1]
    lodging_room_fridge = _is_lodging(fields.business_type) and "냉장고" in haystack
    matched = []
    for product in products:
        name = product.name.lower()
        category = product.category.lower()
        if (
            lodging_room_fridge
            and product.category == "냉장고"
            and product.usage_context != "guest_room"
        ):
            continue
        if (
            category in haystack
            or name in haystack
            or any(term in name or term in category for term in terms)
        ):
            matched.append(product)
    return matched[:10]


def _inquiry_content(fields: IntakeFields) -> str:
    return (
        f"{fields.inquiry}\n구매 단계: {fields.purchase_stage}\n구매 시기: {fields.purchase_timing}"
    )


def _intake_fingerprint(fields: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(fields.get(name) for name in FINGERPRINT_FIELDS)


def _stored_fingerprint(inquiry: Inquiry) -> tuple[Any, ...] | None:
    intake = next(
        (
            item.get("fields")
            for item in reversed(inquiry.raw_conversation or [])
            if isinstance(item, dict) and item.get("type") == "intake_fields"
        ),
        None,
    )
    return _intake_fingerprint(intake) if isinstance(intake, dict) else None


def _deterministic_analysis(
    fields: IntakeFields, products: list[dict[str, Any]]
) -> str:
    industry = normalize_industry(fields.business_type) or "사업장"
    stage = fields.purchase_stage or "상담"
    quantity = fields.quantity or 0
    if not products:
        return (
            f"{industry} 사업장의 {stage} 요청을 접수했습니다. "
            f"요청 수량은 {quantity}대이며, 조건과 일치하는 등록 제품은 담당자가 확인해 안내드립니다."
        )
    names = ", ".join(str(product["name"]) for product in products)
    return (
        f"{industry} 사업장의 {stage} 요청으로 등록 제품 {len(products)}개({names})를 확인했습니다. "
        f"요청 수량은 {quantity}대이며, 최종 수량별 견적은 상담에서 확정됩니다."
    )


def _fallback_turn(fields: IntakeFields) -> ChatAIResult:
    questions = {
        "business_name": "업체명을 알려주세요.",
        "phone": "연락받으실 전화번호를 알려주세요.",
        "inquiry": "어떤 제품이 얼마나 필요하신지 말씀해주세요.",
        "purchase_stage": "현재 견적 요청, 모델 비교, 정보 수집 중 어느 단계인가요?",
        "purchase_timing": "구매 시기는 즉시, 1개월 이내, 3개월 이내, 미정 중 언제인가요?",
        "business_type": "숙박업, 음식점·카페, 사무실, 소매업 중 어떤 업종인가요?",
    }
    missing = next((name for name in questions if getattr(fields, name) in (None, "")), None)
    if missing is None:
        industry = normalize_industry(fields.business_type)
        if industry:
            metric, label, unit, _, _ = FIT_PROFILES[industry]
            if getattr(fields, metric) in (None, 0):
                return ChatAIResult(message=f"{label} 수는 몇 {unit}인가요?", fields=fields)
        if fields.purchase_stage in {"견적 요청", "모델 비교"}:
            if not fields.product:
                return ChatAIResult(message="비교하거나 견적받을 제품을 알려주세요.", fields=fields)
            if not fields.quantity:
                return ChatAIResult(message="필요한 제품 수량은 몇 대인가요?", fields=fields)
            if not fields.location:
                return ChatAIResult(message="지역 담당팀과 파트너를 연결할 설치 지역을 알려주세요.", fields=fields)
    return ChatAIResult(
        message=questions.get(
            missing,
            "정보가 모두 모였어요. 수정하거나 추가할 내용이 있으면 말씀해주세요. "
            "아래 버튼을 누르면 상담 요청이 접수되고 담당자에게 전달됩니다.",
        ),
        fields=fields,
    )


async def _nearby_stores(
    location: str | None,
) -> tuple[list[dict[str, str]], NearbyStoreStatus, str]:
    settings = get_settings()
    if not location:
        return [], "location_missing", "지역 정보가 없어 주변 전문점을 검색하지 않았습니다."
    if not settings.naver_client_id or not settings.naver_client_secret:
        return [], "not_configured", "현재 주변 전문점 검색 서비스를 이용할 수 없습니다."
    headers = {
        "X-Naver-Client-Id": settings.naver_client_id,
        "X-Naver-Client-Secret": settings.naver_client_secret,
    }
    async with httpx.AsyncClient(timeout=8) as client:
        response = await client.get(
            "https://openapi.naver.com/v1/search/local.json",
            params={"query": f"{location} LG전자 전문점", "display": 5},
            headers=headers,
        )
        response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        return [], "failed", "주변 전문점 검색 결과를 확인할 수 없습니다."
    stores = []
    malformed = False
    for item in payload["items"]:
        if not isinstance(item, dict):
            malformed = True
            continue
        title = item.get("title")
        address = item.get("roadAddress") or item.get("address")
        phone = item.get("telephone", "")
        if not isinstance(title, str) or not isinstance(address, str) or not isinstance(phone, str):
            malformed = True
            continue
        name = html.unescape(re.sub(r"<[^>]+>", "", title)).strip()
        if not name or not address.strip():
            malformed = True
            continue
        stores.append({"name": name, "address": address, "phone": phone})
    if not stores:
        if malformed:
            return [], "failed", "주변 전문점 검색 결과를 확인할 수 없습니다."
        return [], "no_results", "해당 지역에서 주변 전문점 검색 결과를 찾지 못했습니다."
    return stores, "success", "네이버 지역 검색에서 찾은 주변 전문점 후보입니다."


@router.post("/chat", response_model=ChatTurnResponse)
@limiter.limit("60/hour")
async def chat(request: Request, payload: ChatTurnRequest, session: Session) -> ChatTurnResponse:
    del request
    result = _fallback_turn(payload.fields)
    try:
        result = await get_llm_client().structured(
            intake_prompt(
                [message.model_dump() for message in payload.messages], payload.fields.model_dump()
            ),
            ChatAIResult,
        )
    except Exception:  # noqa: BLE001 - public chat must remain usable when the LLM is unavailable
        result = _fallback_turn(payload.fields)
    result.fields = payload.fields.model_copy(
        update=result.fields.model_dump(exclude_none=True)
    )
    if not result.fields.inquiry and result.fields.product and result.fields.quantity:
        result.fields.inquiry = next(
            (
                message.content
                for message in reversed(payload.messages)
                if message.role == "user"
                and result.fields.product.lower() in message.content.lower()
            ),
            None,
        )
    returning_customer = False
    if result.fields.phone:
        returning_customer = (
            await session.scalar(
                select(Account.id).where(
                    Account.phone == result.fields.phone, Account.deleted_at.is_(None)
                )
            )
            is not None
        )
    ready = _intake_complete(result.fields)
    if not ready:
        result.message = _fallback_turn(result.fields).message
    return ChatTurnResponse(
        message=result.message,
        fields=result.fields,
        ready_for_analysis=ready,
        returning_customer=returning_customer,
    )


@router.post("/submit", response_model=PublicSubmissionResponse)
@limiter.limit("5/hour")
async def submit(
    request: Request, payload: PublicSubmissionRequest, session: Session
) -> PublicSubmissionResponse:
    del request
    fields = payload.fields
    if not _intake_complete(fields):
        raise HTTPException(
            status_code=422,
            detail="업체명, 연락처, 문의내용, 업종, 업종별 규모, 구매 단계, 구매 시기가 필요합니다. 견적 요청과 모델 비교에는 제품, 수량, 지역도 필요합니다.",
        )
    account_query = select(Account).where(Account.phone == fields.phone).with_for_update()
    account = await session.scalar(account_query)
    if not account:
        account = Account(
            name=fields.business_name,
            phone=fields.phone,
            attributes=_intake_attributes(fields),
        )
        session.add(account)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            account = await session.scalar(account_query)
            if not account:
                raise HTTPException(
                    status_code=409,
                    detail="고객사 등록 충돌이 발생했습니다. 다시 시도해주세요.",
                ) from None
    if account.deleted_at is not None:
        raise HTTPException(
            status_code=409,
            detail="동일 연락처의 삭제된 고객사가 있습니다. 관리자에게 복구를 요청해주세요.",
        )
    location_changed = False
    if fields.location:
        normalized_location = normalize_region_text(fields.location)
        attributes = dict(account.attributes or {})
        if attributes.get("location") != normalized_location:
            attributes["location"] = normalized_location
            account.attributes = attributes
            location_changed = True
    raw = [message.model_dump() for message in payload.messages] + [
        {"type": "intake_fields", "fields": fields.model_dump()}
    ]
    inquiry_content = _inquiry_content(fields)
    fingerprint = _intake_fingerprint(fields.model_dump())
    # ponytail: a short account-locked field window avoids duplicate retries; use an
    # explicit idempotency key if legitimate identical repeat submissions become common.
    recent_inquiries = list(
        (
            await session.scalars(
                select(Inquiry)
                .where(
                    Inquiry.account_id == account.id,
                    Inquiry.channel == "public_web",
                    Inquiry.created_at >= utcnow() - timedelta(minutes=5),
                )
                .order_by(Inquiry.created_at.desc(), Inquiry.id.desc())
            )
        ).all()
    )
    inquiry = next(
        (item for item in recent_inquiries if _stored_fingerprint(item) == fingerprint),
        None,
    )
    duplicate = inquiry is not None
    if inquiry:
        manager_id = inquiry.routing_manager_id
        partner_id = inquiry.partner_id
    else:
        try:
            llm = get_llm_client()
        except RuntimeError:
            llm = None
        manager_id = await regional_manager_id(session, fields.location)
        partner_id = await curated_partner_id(session, fields.location)
        inquiry, _ = await create_inquiry(
            session,
            account.id,
            "public_web",
            inquiry_content,
            raw,
            llm,
            routing_manager_id=manager_id,
            partner_id=partner_id,
        )
    products = _relevant_products(
        list(
            (
                await session.scalars(
                    select(Product).where(Product.is_verified.is_(True)).order_by(Product.id)
                )
            ).all()
        ),
        fields,
    )
    product_data: list[dict[str, Any]] = []
    for product in products:
        price, price_label, price_source_url, price_verified_at = trusted_public_price(product)
        product_data.append(
            {
                "name": product.name,
                "brand": product.brand,
                "category": product.category,
                "price": price,
                "price_label": price_label,
                "price_source_url": price_source_url,
                "price_verified_at": price_verified_at,
                "product_url": product.product_url,
            }
        )
    analysis = _deterministic_analysis(fields, product_data)
    stored_search = next(
        (
            item
            for item in reversed(inquiry.raw_conversation or [])
            if item.get("type") == "nearby_store_search"
        ),
        None,
    )
    if duplicate and stored_search:
        stores = stored_search["stores"]
        nearby_store_status = stored_search["status"]
        nearby_store_message = stored_search["message"]
    else:
        try:
            stores, nearby_store_status, nearby_store_message = await _nearby_stores(
                fields.location
            )
        except Exception:  # noqa: BLE001 - store lookup must never block inquiry persistence
            stores = []
            nearby_store_status = "failed"
            nearby_store_message = (
                "주변 전문점 검색 중 오류가 발생했습니다. 상담 접수는 완료되었습니다."
            )
    if not duplicate:
        inquiry.raw_conversation = raw + [
            {"role": "assistant", "content": analysis},
            {
                "type": "nearby_store_search",
                "status": nearby_store_status,
                "message": nearby_store_message,
                "stores": stores,
            },
        ]
        await session.commit()
    elif location_changed:
        await session.commit()
    partner = await session.get(Partner, partner_id) if partner_id else None
    return PublicSubmissionResponse(
        inquiry_id=inquiry.id,
        confirmation="문의가 접수되었습니다. 담당자가 확인 후 연락드리겠습니다.",
        analysis=analysis,
        analysis_error=False,
        products=[ProductRecommendation(**product) for product in product_data],
        stores=stores,
        nearby_store_status=nearby_store_status,
        nearby_store_message=nearby_store_message,
        regional_team_connected=manager_id is not None,
        partner=None
        if partner is None
        else {
            "name": partner.name,
            "address": partner.address,
            "phone": partner.phone,
            "partner_type": partner.partner_type,
            "verified_at": partner.verified_at,
        },
    )
