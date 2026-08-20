from typing import Annotated

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
from app.security import CurrentStaff

router = APIRouter(prefix="/api/search", tags=["search"])
Session = Annotated[AsyncSession, Depends(get_session)]


@router.post("", response_model=SearchResponse)
async def natural_language_search(
    payload: SearchRequest, session: Session, staff: CurrentStaff
) -> SearchResponse:
    readonly_url = get_settings().effective_database_readonly_url
    if not readonly_url:
        raise HTTPException(status_code=503, detail="읽기 전용 DB 연결이 설정되지 않았습니다.")
    try:
        generated = await get_llm_client().text(nl2sql_prompt(payload.question, SCHEMA_WHITELIST))
        sql = validate_sql(generated)
    except (RuntimeError, UnsafeQueryError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    readonly_engine = create_async_engine(readonly_url, pool_pre_ping=True)
    try:
        async with readonly_engine.connect() as connection, connection.begin():
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            result = await connection.execute(text(sql))
            rows = [dict(row) for row in result.mappings().all()]
    finally:
        await readonly_engine.dispose()
    session.add(
        QueryLog(
            staff_id=staff.id,
            question=payload.question,
            generated_sql=sql,
            row_count=len(rows),
        )
    )
    await session.commit()
    return SearchResponse(sql=sql, rows=rows)
