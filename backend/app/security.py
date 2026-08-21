import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jwt import InvalidTokenError
from sqlalchemy import select, union
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_session
from app.models import Assignment, Inquiry, Opportunity, Staff, Task

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed_password.encode())


def create_access_token(staff: Staff) -> str:
    settings = get_settings()
    if not settings.jwt_secret_key:
        raise RuntimeError("JWT_SECRET_KEY is required")
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_minutes)
    return jwt.encode(
        {"sub": str(staff.id), "role": staff.role, "exp": expires},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )


async def get_current_staff(
    token: Annotated[str, Depends(oauth2_scheme)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Staff:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="인증 정보가 유효하지 않습니다.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    settings = get_settings()
    if not settings.jwt_secret_key:
        raise credentials_error
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "exp"]},
        )
        staff_id = uuid.UUID(payload["sub"])
    except (InvalidTokenError, KeyError, ValueError):
        raise credentials_error from None
    staff = await session.scalar(select(Staff).where(Staff.id == staff_id))
    if not staff or not staff.is_active:
        raise credentials_error
    return staff


async def require_manager(staff: Annotated[Staff, Depends(get_current_staff)]) -> Staff:
    if staff.role not in {"owner", "manager"}:
        raise HTTPException(status_code=403, detail="관리자만 수행할 수 있습니다.")
    return staff


async def require_owner(staff: Annotated[Staff, Depends(get_current_staff)]) -> Staff:
    if staff.role != "owner":
        raise HTTPException(status_code=403, detail="총관리자만 수행할 수 있습니다.")
    return staff


def accessible_account_ids(staff_id: uuid.UUID):
    latest_assignee = (
        select(Assignment.assignee_id)
        .where(Assignment.inquiry_id == Inquiry.id)
        .order_by(Assignment.assigned_at.desc(), Assignment.id.desc())
        .limit(1)
        .correlate(Inquiry)
        .scalar_subquery()
    )
    return union(
        select(Opportunity.account_id).where(Opportunity.assignee_id == staff_id),
        select(Task.account_id).where(Task.assignee_id == staff_id),
        select(Inquiry.account_id).where(latest_assignee == staff_id),
    )


async def require_account_access(session: AsyncSession, account_id: int, staff: Staff) -> None:
    if staff.role in {"owner", "manager"}:
        return
    account_ids = accessible_account_ids(staff.id).subquery()
    allowed = await session.scalar(
        select(account_ids.c.account_id).where(account_ids.c.account_id == account_id).limit(1)
    )
    if allowed is None:
        raise HTTPException(status_code=403, detail="담당 고객만 조회하거나 변경할 수 있습니다.")


CurrentStaff = Annotated[Staff, Depends(get_current_staff)]
ManagerStaff = Annotated[Staff, Depends(require_manager)]
OwnerStaff = Annotated[Staff, Depends(require_owner)]
