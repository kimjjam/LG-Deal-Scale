from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import record_audit
from app.database import get_session
from app.models import Partner, SalesRegion, Staff
from app.schemas import (
    PartnerCreate,
    PartnerResponse,
    PartnerUpdate,
    SalesRegionCreate,
    SalesRegionUpdate,
)
from app.security import CurrentStaff, ManagerStaff

router = APIRouter(prefix="/api/partners-regions", tags=["partners-regions"])
Session = Annotated[AsyncSession, Depends(get_session)]


def _region_response(region: SalesRegion, manager_name: str) -> dict[str, object]:
    return {
        "id": region.id,
        "region_name": region.region_name,
        "match_keyword": region.match_keyword,
        "manager_id": region.manager_id,
        "manager_name": manager_name,
        "is_active": region.is_active,
        "created_at": region.created_at,
    }


async def _active_manager(session: AsyncSession, manager_id: object) -> Staff:
    manager = await session.get(Staff, manager_id)
    if not manager or manager.role != "manager" or not manager.is_active:
        raise HTTPException(status_code=422, detail="활성 지역 매니저를 선택해주세요.")
    return manager


@router.get("/partners", response_model=list[PartnerResponse])
async def list_partners(session: Session, _staff: CurrentStaff) -> list[Partner]:
    return list(await session.scalars(select(Partner).order_by(Partner.region, Partner.name)))


@router.post(
    "/partners", response_model=PartnerResponse, status_code=status.HTTP_201_CREATED
)
async def create_partner(
    payload: PartnerCreate, session: Session, manager: ManagerStaff
) -> Partner:
    partner = Partner(**payload.model_dump())
    session.add(partner)
    await session.flush()
    record_audit(session, manager, "partner.create", "partner", partner.id)
    await session.commit()
    await session.refresh(partner)
    return partner


@router.patch("/partners/{partner_id}", response_model=PartnerResponse)
async def update_partner(
    partner_id: int, payload: PartnerUpdate, session: Session, manager: ManagerStaff
) -> Partner:
    partner = await session.get(Partner, partner_id)
    if not partner:
        raise HTTPException(status_code=404, detail="파트너를 찾을 수 없습니다.")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(partner, field, value)
    record_audit(session, manager, "partner.update", "partner", partner.id)
    await session.commit()
    await session.refresh(partner)
    return partner


@router.get("/regions")
async def list_regions(session: Session, _staff: CurrentStaff) -> list[dict[str, object]]:
    rows = (
        await session.execute(
            select(SalesRegion, Staff.name)
            .join(Staff, Staff.id == SalesRegion.manager_id)
            .order_by(SalesRegion.region_name, SalesRegion.match_keyword)
        )
    ).all()
    return [_region_response(region, manager_name) for region, manager_name in rows]


@router.post("/regions", status_code=status.HTTP_201_CREATED)
async def create_region(
    payload: SalesRegionCreate, session: Session, manager: ManagerStaff
) -> dict[str, object]:
    assigned_manager = await _active_manager(session, payload.manager_id)
    region = SalesRegion(**payload.model_dump())
    session.add(region)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="이미 등록된 지역 매칭 키워드입니다.") from None
    record_audit(session, manager, "sales_region.create", "sales_region", region.id)
    await session.commit()
    await session.refresh(region)
    return _region_response(region, assigned_manager.name)


@router.patch("/regions/{region_id}")
async def update_region(
    region_id: int, payload: SalesRegionUpdate, session: Session, manager: ManagerStaff
) -> dict[str, object]:
    region = await session.get(SalesRegion, region_id)
    if not region:
        raise HTTPException(status_code=404, detail="지역 매핑을 찾을 수 없습니다.")
    changes = payload.model_dump(exclude_unset=True)
    manager_id = changes.get("manager_id", region.manager_id)
    if "manager_id" in changes or changes.get("is_active", region.is_active):
        assigned_manager = await _active_manager(session, manager_id)
    else:
        assigned_manager = await session.get(Staff, manager_id)
        if not assigned_manager:
            raise HTTPException(status_code=422, detail="지역 매니저를 찾을 수 없습니다.")
    for field, value in changes.items():
        setattr(region, field, value)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="이미 등록된 지역 매칭 키워드입니다.") from None
    record_audit(session, manager, "sales_region.update", "sales_region", region.id)
    await session.commit()
    await session.refresh(region)
    return _region_response(region, assigned_manager.name)
