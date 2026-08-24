from datetime import datetime, timezone
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_session
from app.security import OwnerStaff

router = APIRouter(prefix="/api/admin", tags=["admin"])
Session = Annotated[AsyncSession, Depends(get_session)]
SBIZ_HEALTH_URL = "https://apis.data.go.kr/B553077/api/open/sdsc2/largeUpjongList"


@router.get("/api-status")
async def api_status(session: Session, _owner: OwnerStaff) -> dict[str, object]:
    settings = get_settings()
    services: list[dict[str, str]] = [
        {"name": "DirectDesk API", "status": "available", "detail": "백엔드 응답 정상"}
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

    services.append(
        {
            "name": "Gemini API",
            "status": "configured" if settings.gemini_api_key else "not_configured",
            "detail": "GEMINI_API_KEY 설정됨" if settings.gemini_api_key else "GEMINI_API_KEY 미설정",
        }
    )
    naver_ready = bool(settings.naver_client_id and settings.naver_client_secret)
    naver_partial = bool(settings.naver_client_id or settings.naver_client_secret)
    services.append(
        {
            "name": "네이버 지역검색 API",
            "status": "configured" if naver_ready else "incomplete" if naver_partial else "not_configured",
            "detail": "인증정보 설정됨" if naver_ready else "인증정보 일부 누락" if naver_partial else "인증정보 미설정",
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
