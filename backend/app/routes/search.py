from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.config import get_settings
from app.database import get_session
from app.llm import get_llm_client
from app.models import QueryLog
from app.nl2sql import SCHEMA_WHITELIST, UnsafeQueryError, validate_sql
from app.prompts import nl2sql_prompt
from app.schemas import SearchRequest, SearchResponse
from app.security import OwnerStaff

router = APIRouter(prefix="/api/search", tags=["search"])
Session = Annotated[AsyncSession, Depends(get_session)]


async def _record_query(
    session: AsyncSession,
    staff_id: UUID,
    question: str,
    generated_sql: str = "",
    *,
    success: bool,
    row_count: int = 0,
    error_category: str | None = None,
    error_message: str | None = None,
) -> None:
    session.add(
        QueryLog(
            staff_id=staff_id,
            question=question,
            generated_sql=generated_sql,
            row_count=row_count,
            success=success,
            error_category=error_category,
            error_message=error_message,
        )
    )
    await session.commit()


@router.post("", response_model=SearchResponse)
async def natural_language_search(
    payload: SearchRequest, session: Session, staff: OwnerStaff
) -> SearchResponse:
    readonly_url = get_settings().effective_database_readonly_url
    if not readonly_url:
        await _record_query(
            session,
            staff.id,
            payload.question,
            success=False,
            error_category="configuration_error",
            error_message="읽기 전용 DB 연결이 설정되지 않았습니다.",
        )
        raise HTTPException(status_code=503, detail="읽기 전용 DB 연결이 설정되지 않았습니다.")
    try:
        generated = await get_llm_client().text(nl2sql_prompt(payload.question, SCHEMA_WHITELIST))
    except Exception as error:
        message = "자연어 SQL 생성에 실패했습니다."
        await _record_query(
            session,
            staff.id,
            payload.question,
            success=False,
            error_category="generation_error",
            error_message=message,
        )
        raise HTTPException(status_code=422, detail=message) from error
    try:
        sql = validate_sql(generated)
    except UnsafeQueryError as error:
        message = "안전하지 않은 SQL이 거부되었습니다."
        await _record_query(
            session,
            staff.id,
            payload.question,
            generated,
            success=False,
            error_category="validation_error",
            error_message=str(error)[:200],
        )
        raise HTTPException(status_code=422, detail=message) from error
    readonly_engine = None
    try:
        readonly_engine = create_async_engine(readonly_url, pool_pre_ping=True)
        async with readonly_engine.connect() as connection, connection.begin():
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            await connection.execute(text("SET LOCAL statement_timeout = '3s'"))
            result = await connection.execute(text(sql))
            rows = [dict(row) for row in result.mappings().all()]
    except Exception as error:
        message = "읽기 전용 DB 쿼리 실행에 실패했습니다."
        await _record_query(
            session,
            staff.id,
            payload.question,
            sql,
            success=False,
            error_category="execution_error",
            error_message=message,
        )
        raise HTTPException(status_code=502, detail=message) from error
    finally:
        if readonly_engine:
            await readonly_engine.dispose()
    await _record_query(
        session,
        staff.id,
        payload.question,
        sql,
        success=True,
        row_count=len(rows),
    )
    return SearchResponse(sql=sql, rows=rows)
