import html
import re
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
from app.models import Account, Product
from app.product_pricing import trusted_business_price
from app.prompts import analysis_prompt, intake_prompt
from app.schemas import (
    ChatTurnRequest,
    ChatTurnResponse,
    IntakeFields,
    NearbyStoreStatus,
    ProductRecommendation,
    PublicSubmissionRequest,
    PublicSubmissionResponse,
)
from app.scoring import FIT_PROFILES, normalize_industry
from app.services import create_inquiry, curated_partner_id, regional_manager_id

router = APIRouter(prefix="/api/public", tags=["public"])
limiter = Limiter(key_func=get_remote_address)
Session = Annotated[AsyncSession, Depends(get_session)]
SCALE_KEYS = {profile[0] for profile in FIT_PROFILES.values()}


class ChatAIResult(BaseModel):
    message: str
    fields: IntakeFields


def _public_price(product: Product) -> tuple[float | None, str]:
    price, label, _, _ = trusted_business_price(product)
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
            detail="업체명, 연락처, 문의내용, 업종, 업종별 규모, 구매 단계, 구매 시기가 필요합니다.",
        )
    account = await session.scalar(select(Account).where(Account.phone == fields.phone))
    if account and account.deleted_at is not None:
        raise HTTPException(
            status_code=409,
            detail="동일 연락처의 삭제된 고객사가 있습니다. 관리자에게 복구를 요청해주세요.",
        )
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
            raise HTTPException(
                status_code=409,
                detail="동일 연락처가 이미 등록되어 있습니다. 다시 시도해주세요.",
            ) from None
    else:
        preserved = {
            key: value for key, value in account.attributes.items() if key not in SCALE_KEYS
        }
        account.attributes = {**preserved, **_intake_attributes(fields)}
        await session.flush()
    try:
        llm = get_llm_client()
    except RuntimeError:
        llm = None
    raw = [message.model_dump() for message in payload.messages] + [
        {"type": "intake_fields", "fields": fields.model_dump()}
    ]
    inquiry_content = (
        f"{fields.inquiry}\n구매 단계: {fields.purchase_stage}\n구매 시기: {fields.purchase_timing}"
    )
    inquiry, _ = await create_inquiry(
        session,
        account.id,
        "public_web",
        inquiry_content,
        raw,
        llm,
        routing_manager_id=await regional_manager_id(session, fields.location),
        partner_id=await curated_partner_id(session, fields.location),
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
        price, price_label, price_source_url, price_verified_at = trusted_business_price(product)
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
    analysis = None
    analysis_error = False
    if not product_data:
        analysis = "등록된 제품 중 조건에 맞는 항목이 없어 담당자가 확인 후 안내드리겠습니다."
    elif llm:
        try:
            analysis = await llm.text(analysis_prompt(fields.model_dump(), product_data))
        except Exception:  # noqa: BLE001 - inquiry is already safely committed
            analysis_error = True
    else:
        analysis_error = True
    try:
        stores, nearby_store_status, nearby_store_message = await _nearby_stores(fields.location)
    except Exception:  # noqa: BLE001 - store lookup must never block inquiry persistence
        stores = []
        nearby_store_status = "failed"
        nearby_store_message = (
            "주변 전문점 검색 중 오류가 발생했습니다. 상담 접수는 완료되었습니다."
        )
    inquiry.raw_conversation = raw + [
        {"role": "assistant", "content": analysis or "분석 생성 실패, 상담 접수 완료"},
        {
            "type": "nearby_store_search",
            "status": nearby_store_status,
            "message": nearby_store_message,
            "stores": stores,
        },
    ]
    await session.commit()
    return PublicSubmissionResponse(
        inquiry_id=inquiry.id,
        confirmation="문의가 접수되었습니다. 담당자가 확인 후 연락드리겠습니다.",
        analysis=analysis,
        analysis_error=analysis_error,
        products=[ProductRecommendation(**product) for product in product_data],
        stores=stores,
        nearby_store_status=nearby_store_status,
        nearby_store_message=nearby_store_message,
    )
