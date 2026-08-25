from datetime import datetime, timezone
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_session
from app.localdata import BUILDING_PERMIT_URL, BUILDING_TITLE_URL
from app.security import OwnerStaff

router = APIRouter(prefix="/api/admin", tags=["admin"])
Session = Annotated[AsyncSession, Depends(get_session)]
SBIZ_HEALTH_URL = "https://apis.data.go.kr/B553077/api/open/sdsc2/largeUpjongList"


@router.get("/api-status")
async def api_status(session: Session, _owner: OwnerStaff) -> dict[str, object]:
    settings = get_settings()
    services: list[dict[str, str]] = [
        {"name": "LG ELECTRONICS PARTNER PORTAL API", "status": "available", "detail": "백엔드 응답 정상"}
    ]
    try:
        await session.execute(text("SELECT 1"))
        services.append({"name": "데이터베이스", "status": "available", "detail": "연결 정상"})
    except SQLAlchemyError:
        await session.rollback()
        services.append({"name": "데이터베이스", "status": "degraded", "detail": "연결 실패"})

    if settings.data_go_kr_service_key:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(
                    SBIZ_HEALTH_URL,
                    params={"serviceKey": settings.data_go_kr_service_key, "type": "json"},
                )
                response.raise_for_status()
                if response.json().get("header", {}).get("resultCode") != "00":
                    raise ValueError("공공데이터 인증 실패")
            services.append(
                {"name": "상가(상권)정보 API", "status": "available", "detail": "연결 정상"}
            )
        except (httpx.HTTPError, TypeError, ValueError):
            services.append(
                {"name": "상가(상권)정보 API", "status": "degraded", "detail": "연결 실패"}
            )
    else:
        services.append(
            {
                "name": "상가(상권)정보 API",
                "status": "not_configured",
                "detail": "DATA_GO_KR_SERVICE_KEY 미설정",
            }
        )

    building_key = settings.effective_building_hub_api_key
    if building_key:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(
                    BUILDING_PERMIT_URL,
                    params={
                        "serviceKey": building_key,
                        "sigunguCd": "11680",
                        "bjdongCd": "10100",
                        "platGbCd": "0",
                        "bun": "0825",
                        "ji": "0000",
                        "numOfRows": 1,
                        "pageNo": 1,
                        "_type": "json",
                    },
                )
                response.raise_for_status()
                result_code = str(
                    response.json().get("response", {}).get("header", {}).get("resultCode") or ""
                )
                if result_code != "00":
                    raise ValueError("건축인허가 인증 실패")
            services.append(
                {"name": "건축인허가정보 API", "status": "available", "detail": "연결 정상"}
            )
        except (httpx.HTTPError, TypeError, ValueError):
            services.append(
                {"name": "건축인허가정보 API", "status": "degraded", "detail": "연결 실패"}
            )
    else:
        services.append(
            {
                "name": "건축인허가정보 API",
                "status": "not_configured",
                "detail": "DATA_GO_KR_SERVICE_KEY 미설정",
            }
        )

    if building_key:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(
                    BUILDING_TITLE_URL,
                    params={
                        "serviceKey": building_key,
                        "sigunguCd": "11680",
                        "bjdongCd": "10100",
                        "platGbCd": "0",
                        "bun": "0825",
                        "ji": "0000",
                        "numOfRows": 1,
                        "pageNo": 1,
                        "_type": "json",
                    },
                )
                response.raise_for_status()
                result_code = str(
                    response.json().get("response", {}).get("header", {}).get("resultCode") or ""
                )
                if result_code != "00":
                    raise ValueError("건축물대장 인증 실패")
            services.append(
                {"name": "건축물대장정보 API", "status": "available", "detail": "연결 정상"}
            )
        except (httpx.HTTPError, TypeError, ValueError):
            services.append(
                {"name": "건축물대장정보 API", "status": "degraded", "detail": "연결 실패"}
            )
    else:
        services.append(
            {
                "name": "건축물대장정보 API",
                "status": "not_configured",
                "detail": "BUILDING_HUB_API_KEY 또는 DATA_GO_KR_SERVICE_KEY 미설정",
            }
        )

    services.append(
        {
            "name": "Gemini API",
            "status": "configured" if settings.gemini_api_key else "not_configured",
            "detail": "GEMINI_API_KEY 설정됨" if settings.gemini_api_key else "GEMINI_API_KEY 미설정",
        }
    )
    naver_ready = bool(settings.naver_client_id and settings.naver_client_secret)
    naver_partial = bool(settings.naver_client_id or settings.naver_client_secret)
    if naver_ready:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(
                    "https://openapi.naver.com/v1/search/blog.json",
                    params={"query": "리모델링", "display": 1},
                    headers={
                        "X-Naver-Client-Id": settings.naver_client_id,
                        "X-Naver-Client-Secret": settings.naver_client_secret,
                    },
                )
                response.raise_for_status()
                if not isinstance(response.json().get("items"), list):
                    raise TypeError("네이버 검색 응답 오류")
            services.append(
                {"name": "네이버 검색 API", "status": "available", "detail": "검색 연결 정상"}
            )
        except (httpx.HTTPError, TypeError, ValueError):
            services.append(
                {"name": "네이버 검색 API", "status": "degraded", "detail": "검색 연결 실패"}
            )
    else:
        services.append(
            {
                "name": "네이버 검색 API",
                "status": "incomplete" if naver_partial else "not_configured",
                "detail": "인증정보 일부 누락" if naver_partial else "인증정보 미설정",
            }
        )
    email_ready = bool(settings.test_email_address and settings.email_provider_api_key)
    services.append(
        {
            "name": "이메일 API",
            "status": "configured" if settings.outbound_email_mode == "dry_run" or email_ready else "incomplete",
            "detail": "드라이런 모드" if settings.outbound_email_mode == "dry_run" else "테스트 발송 설정됨" if email_ready else "테스트 주소 또는 API 키 누락",
        }
    )
    return {"checked_at": datetime.now(timezone.utc), "services": services}
