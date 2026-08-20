from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models import Staff
from app.schemas import StaffIdentity
from app.security import ManagerStaff

router = APIRouter(prefix="/api/staff", tags=["staff"])
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("", response_model=list[StaffIdentity])
async def list_staff(
    session: Session,
    _manager: ManagerStaff,
    role: Literal["manager", "rep"] | None = None,
) -> list[Staff]:
    statement = select(Staff).order_by(Staff.name)
    if role:
        statement = statement.where(Staff.role == role)
    return list((await session.scalars(statement)).all())

