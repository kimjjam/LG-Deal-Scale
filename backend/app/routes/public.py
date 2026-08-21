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
from app.prompts import analysis_prompt, intake_prompt
from app.schemas import (
    ChatTurnRequest,
    ChatTurnResponse,
    IntakeFields,
    ProductRecommendation,
    PublicSubmissionRequest,
    PublicSubmissionResponse,
)
from app.services import create_inquiry

router = APIRouter(prefix="/api/public", tags=["public"])
limiter = Limiter(key_func=get_remote_address)
Session = Annotated[AsyncSession, Depends(get_session)]


class ChatAIResult(BaseModel):
    message: str
    fields: IntakeFields


def _relevant_products(products: list[Product], fields: IntakeFields) -> list[Product]:
    haystack = " ".join(filter(None, (fields.product, fields.inquiry))).lower()
    terms = [term for term in re.findall(r"[0-9a-z가-힣]+", haystack) if len(term) > 1]
    matched = []
    for product in products:
        name = product.name.lower()
        category = product.category.lower()
        if category in haystack or name in haystack or any(
            term in name or term in category for term in terms
        ):
            matched.append(product)
    return matched[:10]


def _fallback_turn(fields: IntakeFields) -> ChatAIResult:
    questions = {
        "business_name": "업체명을 알려주세요.",
        "phone": "연락받으실 전화번호를 알려주세요.",
        "inquiry": "어떤 제품이 얼마나 필요하신지 말씀해주세요.",
        "business_type": "호텔, 모텔, 펜션, 게스트하우스 중 어떤 업종인가요?",
        "room_count": "객실은 몇 개인가요?",
        "location": "어느 지역에 계신가요?",
    }
    missing = next((name for name in questions if getattr(fields, name) in (None, "")), None)
    return ChatAIResult(
        message=questions.get(missing, "정보가 모두 모였어요. AI 맞춤 분석을 받아보세요."),
        fields=fields,
    )


async def _nearby_stores(location: str | None) -> list[dict[str, str]]:
    settings = get_settings()
    if not location or not settings.naver_client_id or not settings.naver_client_secret:
        return []
    headers = {
        "X-Naver-Client-Id": settings.naver_client_id,
        "X-Naver-Client-Secret": settings.naver_client_secret,
    }
    async with httpx.AsyncClient(timeout=8) as client:
        response = await client.get(
            "https://openapi.naver.com/v1/search/local.json",
            params={"query": f"{location} LG전자 인증 전문점", "display": 5},
            headers=headers,
        )
        response.raise_for_status()
    return [
        {
            "name": html.unescape(re.sub(r"<[^>]+>", "", item.get("title", ""))),
            "address": item.get("roadAddress") or item.get("address", ""),
            "phone": item.get("telephone", ""),
        }
        for item in response.json().get("items", [])
    ]


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
    ready = all((result.fields.business_name, result.fields.phone, result.fields.inquiry))
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
    if not fields.business_name or not fields.phone or not fields.inquiry:
        raise HTTPException(status_code=422, detail="업체명, 연락처, 문의내용이 필요합니다.")
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
            attributes={
                "business_type": fields.business_type,
                "room_count": fields.room_count,
            },
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
    try:
        llm = get_llm_client()
    except RuntimeError:
        llm = None
    raw = [message.model_dump() for message in payload.messages] + [
        {"type": "intake_fields", "fields": fields.model_dump()}
    ]
    inquiry, _ = await create_inquiry(session, account.id, "public_web", fields.inquiry, raw, llm)
    products = _relevant_products(
        list(
            (
                await session.scalars(
                    select(Product).where(Product.brand == "LG").order_by(Product.id)
                )
            ).all()
        ),
        fields,
    )
    product_data: list[dict[str, Any]] = [
        {
            "name": product.name,
            "brand": product.brand,
            "category": product.category,
            "price": float(product.price),
            "product_url": product.product_url,
        }
        for product in products
    ]
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
        stores = await _nearby_stores(fields.location)
    except httpx.HTTPError:
        stores = []
    inquiry.raw_conversation = raw + [
        {"role": "assistant", "content": analysis or "분석 생성 실패, 상담 접수 완료"},
        {"role": "assistant", "stores": stores},
    ]
    await session.commit()
    return PublicSubmissionResponse(
        inquiry_id=inquiry.id,
        confirmation="문의가 접수되었습니다. 담당자가 확인 후 연락드리겠습니다.",
        analysis=analysis,
        analysis_error=analysis_error,
        products=[ProductRecommendation(**product) for product in product_data],
        stores=stores,
    )
