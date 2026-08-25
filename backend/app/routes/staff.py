import secrets
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import record_audit
from app.database import get_session
from app.models import Assignment, Inquiry, Lead, SalesRegion, Staff
from app.schemas import (
    STAFF_REGION_KEYWORDS,
    RegionalStaffPasswordResetResult,
    StaffActiveUpdate,
    StaffCreate,
    StaffIdentity,
    StaffPasswordReset,
    StaffRoleUpdate,
)
from app.security import ManagerStaff, OwnerStaff, hash_password

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


@router.post("", response_model=StaffIdentity, status_code=status.HTTP_201_CREATED)
async def create_staff(payload: StaffCreate, session: Session, owner: OwnerStaff) -> Staff:
    staff = Staff(
        name=payload.name.strip(),
        email=str(payload.email).lower(),
        role=payload.role,
        hashed_password=hash_password(payload.password),
    )
    session.add(staff)
    try:
        await session.flush()
        record_audit(
            session,
            owner,
            "staff.create",
            "staff",
            staff.id,
            {"email": staff.email, "role": staff.role},
        )
        if payload.region_name:
            region = SalesRegion(
                region_name=payload.region_name,
                match_keyword=STAFF_REGION_KEYWORDS[payload.region_name],
                manager_id=staff.id,
            )
            session.add(region)
            await session.flush()
            record_audit(
                session,
                owner,
                "sales_region.create",
                "sales_region",
                region.id,
                {"region_name": region.region_name},
            )
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="이미 등록된 이메일입니다.") from None
    await session.refresh(staff)
    return staff


async def editable_staff(staff_id: uuid.UUID, session: AsyncSession) -> Staff:
    staff = await session.get(Staff, staff_id)
    if not staff:
        raise HTTPException(status_code=404, detail="계정을 찾을 수 없습니다.")
    if staff.role == "owner":
        raise HTTPException(status_code=400, detail="총관리자 계정은 변경할 수 없습니다.")
    return staff


async def require_no_current_unresolved_assignments(
    staff_id: uuid.UUID, session: AsyncSession
) -> None:
    latest_assignee = (
        select(Assignment.assignee_id)
        .where(Assignment.inquiry_id == Inquiry.id)
        .order_by(Assignment.assigned_at.desc(), Assignment.id.desc())
        .limit(1)
        .correlate(Inquiry)
        .scalar_subquery()
    )
    inquiry_id = await session.scalar(
        select(Inquiry.id)
        .where(
            Inquiry.status.in_(("open", "routed")),
            latest_assignee == staff_id,
        )
        .limit(1)
    )
    if inquiry_id:
        raise HTTPException(
            status_code=409,
            detail="미해결 문의를 다른 담당자에게 재배정한 후 변경해주세요.",
        )
    if await session.scalar(
        select(Lead.id)
        .where(
            Lead.assignee_id == staff_id,
            Lead.pipeline_stage.not_in(("converted", "dropped")),
        )
        .limit(1)
    ):
        raise HTTPException(
            status_code=409,
            detail="진행 중인 담당 리드를 먼저 재배정해주세요.",
        )


async def require_no_active_regions(staff_id: uuid.UUID, session: AsyncSession) -> None:
    if await session.scalar(
        select(SalesRegion.id)
        .where(SalesRegion.manager_id == staff_id, SalesRegion.is_active.is_(True))
        .limit(1)
    ):
        raise HTTPException(
            status_code=409,
            detail="활성 지역 담당 매핑을 다른 매니저에게 변경하거나 비활성화한 후 변경해주세요.",
        )


async def require_no_unresolved_routed_inquiries(
    staff_id: uuid.UUID, session: AsyncSession
) -> None:
    if await session.scalar(
        select(Inquiry.id)
        .where(
            Inquiry.routing_manager_id == staff_id,
            Inquiry.status.in_(("open", "routed")),
        )
        .limit(1)
    ):
        raise HTTPException(
            status_code=409,
            detail="담당 중인 미해결 지역 문의를 처리하거나 다른 지역 매니저에게 이관한 후 변경해주세요.",
        )


@router.patch("/{staff_id}/role", response_model=StaffIdentity)
async def update_staff_role(
    staff_id: uuid.UUID,
    payload: StaffRoleUpdate,
    session: Session,
    owner: OwnerStaff,
) -> Staff:
    staff = await editable_staff(staff_id, session)
    if staff.role == "rep" and payload.role != "rep":
        await require_no_current_unresolved_assignments(staff.id, session)
    if staff.role == "manager" and payload.role != "manager":
        await require_no_active_regions(staff.id, session)
        await require_no_unresolved_routed_inquiries(staff.id, session)
    previous_role = staff.role
    staff.role = payload.role
    record_audit(
        session,
        owner,
        "staff.role_change",
        "staff",
        staff.id,
        {"from": previous_role, "to": payload.role},
    )
    await session.commit()
    await session.refresh(staff)
    return staff


@router.patch("/{staff_id}/active", response_model=StaffIdentity)
async def update_staff_active(
    staff_id: uuid.UUID,
    payload: StaffActiveUpdate,
    session: Session,
    owner: OwnerStaff,
) -> Staff:
    staff = await editable_staff(staff_id, session)
    if staff.is_active and not payload.is_active:
        await require_no_current_unresolved_assignments(staff.id, session)
        await require_no_active_regions(staff.id, session)
        await require_no_unresolved_routed_inquiries(staff.id, session)
    previous = staff.is_active
    staff.is_active = payload.is_active
    record_audit(
        session,
        owner,
        "staff.active_change",
        "staff",
        staff.id,
        {"from": previous, "to": payload.is_active},
    )
    await session.commit()
    await session.refresh(staff)
    return staff


@router.post(
    "/regional-managers/reset-passwords",
    response_model=list[RegionalStaffPasswordResetResult],
)
async def reset_regional_manager_passwords(
    response: Response,
    session: Session,
    owner: OwnerStaff,
) -> list[RegionalStaffPasswordResetResult]:
    managers = list(
        (
            await session.scalars(
                select(Staff)
                .join(SalesRegion, SalesRegion.manager_id == Staff.id)
                .where(
                    SalesRegion.is_active.is_(True),
                    Staff.is_active.is_(True),
                    Staff.role == "manager",
                )
                .distinct()
                .order_by(Staff.name, Staff.id)
            )
        ).all()
    )
    results: list[RegionalStaffPasswordResetResult] = []
    for staff in managers:
        temporary_password = secrets.token_urlsafe(12)
        staff.hashed_password = hash_password(temporary_password)
        record_audit(
            session,
            owner,
            "staff.password_reset",
            "staff",
            staff.id,
            {"method": "regional_bulk"},
        )
        results.append(
            RegionalStaffPasswordResetResult(
                id=staff.id,
                name=staff.name,
                email=staff.email,
                temporary_password=temporary_password,
            )
        )
    await session.commit()
    response.headers["Cache-Control"] = "no-store"
    return results


@router.post("/{staff_id}/reset-password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_staff_password(
    staff_id: uuid.UUID,
    payload: StaffPasswordReset,
    session: Session,
    owner: OwnerStaff,
) -> None:
    staff = await editable_staff(staff_id, session)
    staff.hashed_password = hash_password(payload.password)
    record_audit(session, owner, "staff.password_reset", "staff", staff.id)
    await session.commit()
