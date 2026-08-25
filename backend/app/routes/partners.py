import csv
import io
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import record_audit
from app.database import get_session
from app.models import Assignment, Inquiry, Partner, SalesRegion, Staff
from app.schemas import (
    CsvTextRequest,
    PartnerCreate,
    PartnerResponse,
    PartnerUpdate,
    SalesRegionCreate,
    SalesRegionUpdate,
    normalize_region_text,
)
from app.security import CurrentStaff, OwnerStaff
from app.services import manager_region_keywords, partner_matches_region_keywords

router = APIRouter(prefix="/api/partners-regions", tags=["partners-regions"])
Session = Annotated[AsyncSession, Depends(get_session)]
CSV_ROW_LIMIT = 500


def _csv_rows(csv_text: str, headers: set[str]) -> tuple[list[dict[str, str]], list[dict[str, object]]]:
    try:
        reader = csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff")))
        rows = list(reader)
    except csv.Error as error:
        return [], [{"row": 0, "error": str(error)}]
    if (
        not reader.fieldnames
        or len(reader.fieldnames) != len(headers)
        or set(reader.fieldnames) != headers
    ):
        return [], [
            {
                "row": 1,
                "error": f"헤더는 {','.join(sorted(headers))} 순서와 무관하게 정확히 필요합니다.",
            }
        ]
    if len(rows) > CSV_ROW_LIMIT:
        return [], [{"row": 0, "error": f"최대 {CSV_ROW_LIMIT}행까지 가져올 수 있습니다."}]
    return rows, []


def _csv_bool(value: str | None) -> bool:
    normalized = (value or "").strip().casefold()
    if normalized in {"true", "1", "yes", "y", "활성"}:
        return True
    if normalized in {"false", "0", "no", "n", "비활성"}:
        return False
    raise ValueError("is_active은 true 또는 false여야 합니다.")


def _partner_key(partner: PartnerCreate | Partner) -> tuple[str, str, str, str]:
    def compact(value: str) -> str:
        return "".join(value.split()).casefold()

    return (
        compact(partner.name),
        compact(partner.address),
        normalize_region_text(partner.region),
        partner.partner_type,
    )


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
async def list_partners(session: Session, staff: CurrentStaff) -> list[Partner]:
    statement = select(Partner).order_by(Partner.region, Partner.name)
    if staff.role == "rep":
        latest_assignee = (
            select(Assignment.assignee_id)
            .where(Assignment.inquiry_id == Inquiry.id)
            .order_by(Assignment.assigned_at.desc(), Assignment.id.desc())
            .limit(1)
            .correlate(Inquiry)
            .scalar_subquery()
        )
        statement = statement.where(
            Partner.id.in_(
                select(Inquiry.partner_id).where(
                    Inquiry.partner_id.is_not(None), latest_assignee == staff.id
                )
            )
        )
    partners = list(await session.scalars(statement))
    if staff.role == "manager":
        keywords = await manager_region_keywords(session, staff)
        return [partner for partner in partners if partner_matches_region_keywords(partner, keywords)]
    return partners


@router.post(
    "/partners", response_model=PartnerResponse, status_code=status.HTTP_201_CREATED
)
async def create_partner(
    payload: PartnerCreate, session: Session, manager: OwnerStaff
) -> Partner:
    partner = Partner(**payload.model_dump())
    session.add(partner)
    await session.flush()
    record_audit(session, manager, "partner.create", "partner", partner.id)
    await session.commit()
    await session.refresh(partner)
    return partner


@router.post("/partners/import")
async def import_partners(
    payload: CsvTextRequest, session: Session, manager: OwnerStaff
) -> dict[str, object]:
    headers = {
        "name",
        "address",
        "phone",
        "region",
        "partner_type",
        "verification_source",
        "verified_at",
        "is_active",
    }
    rows, errors = _csv_rows(payload.csv_text, headers)
    parsed: list[tuple[int, PartnerCreate]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for row_number, row in enumerate(rows, start=2):
        try:
            if None in row:
                raise ValueError("헤더보다 많은 값이 있습니다.")
            partner = PartnerCreate(
                name=row.get("name") or "",
                address=row.get("address") or "",
                phone=row.get("phone") or None,
                region=row.get("region") or "",
                partner_type=row.get("partner_type") or "",  # type: ignore[arg-type]
                verification_source=row.get("verification_source") or "",
                verified_at=row.get("verified_at") or "",  # type: ignore[arg-type]
                is_active=_csv_bool(row.get("is_active")),
            )
            key = _partner_key(partner)
            if key in seen:
                raise ValueError("CSV 안에 동일한 파트너 자연 키가 있습니다.")
            seen.add(key)
            parsed.append((row_number, partner))
        except (ValidationError, ValueError) as error:
            errors.append({"row": row_number, "error": str(error)})
    existing_rows = list((await session.scalars(select(Partner))).all())
    existing_by_key: dict[tuple[str, str, str, str], Partner] = {}
    duplicate_existing: set[tuple[str, str, str, str]] = set()
    for partner in existing_rows:
        key = _partner_key(partner)
        if key in existing_by_key:
            duplicate_existing.add(key)
        existing_by_key[key] = partner
    for row_number, partner in parsed:
        if _partner_key(partner) in duplicate_existing:
            errors.append({"row": row_number, "error": "기존 데이터에 동일한 파트너 자연 키가 중복됩니다."})
    if errors:
        return {"imported_count": 0, "errors": errors}
    created = 0
    for _, item in parsed:
        partner = existing_by_key.get(_partner_key(item))
        if partner:
            for field, value in item.model_dump().items():
                setattr(partner, field, value)
        else:
            session.add(Partner(**item.model_dump()))
            created += 1
    record_audit(
        session,
        manager,
        "partner.csv_import",
        "partner",
        "bulk",
        {"count": len(parsed), "created": created, "updated": len(parsed) - created},
    )
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return {"imported_count": 0, "errors": [{"row": 0, "error": "가져오기 중 중복이 발생했습니다."}]}
    return {"imported_count": len(parsed), "errors": []}


@router.patch("/partners/{partner_id}", response_model=PartnerResponse)
async def update_partner(
    partner_id: int, payload: PartnerUpdate, session: Session, manager: OwnerStaff
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
async def list_regions(session: Session, staff: CurrentStaff) -> list[dict[str, object]]:
    statement = (
        select(SalesRegion, Staff.name)
        .join(Staff, Staff.id == SalesRegion.manager_id)
        .order_by(SalesRegion.region_name, SalesRegion.match_keyword)
    )
    if staff.role == "manager":
        statement = statement.where(
            SalesRegion.manager_id == staff.id, SalesRegion.is_active.is_(True)
        )
    elif staff.role == "rep":
        return []
    rows = (await session.execute(statement)).all()
    return [_region_response(region, manager_name) for region, manager_name in rows]


@router.post("/regions", status_code=status.HTTP_201_CREATED)
async def create_region(
    payload: SalesRegionCreate, session: Session, manager: OwnerStaff
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


@router.post("/regions/import")
async def import_regions(
    payload: CsvTextRequest, session: Session, manager: OwnerStaff
) -> dict[str, object]:
    headers = {"region_name", "match_keyword", "staff_email", "is_active"}
    rows, errors = _csv_rows(payload.csv_text, headers)
    emails = {(row.get("staff_email") or "").strip().casefold() for row in rows}
    managers = list(
        (
            await session.scalars(
                select(Staff).where(
                    Staff.email.in_(emails),
                    Staff.role == "manager",
                    Staff.is_active.is_(True),
                )
            )
        ).all()
    )
    manager_by_email = {item.email.casefold(): item for item in managers}
    parsed: list[tuple[int, SalesRegionCreate]] = []
    seen: set[tuple[str, object]] = set()
    for row_number, row in enumerate(rows, start=2):
        try:
            if None in row:
                raise ValueError("헤더보다 많은 값이 있습니다.")
            email = (row.get("staff_email") or "").strip().casefold()
            assigned = manager_by_email.get(email)
            if not assigned:
                raise ValueError("해당 이메일의 활성 매니저를 찾을 수 없습니다.")
            region = SalesRegionCreate(
                region_name=row.get("region_name") or "",
                match_keyword=row.get("match_keyword") or "",
                manager_id=assigned.id,
                is_active=_csv_bool(row.get("is_active")),
            )
            key = (region.match_keyword, region.manager_id)
            if key in seen:
                raise ValueError("CSV 안에 동일한 지역·담당자 조합이 있습니다.")
            seen.add(key)
            parsed.append((row_number, region))
        except (ValidationError, ValueError) as error:
            errors.append({"row": row_number, "error": str(error)})
    if errors:
        return {"imported_count": 0, "errors": errors}
    keywords = {keyword for keyword, _ in seen}
    existing = {
        (item.match_keyword, item.manager_id): item
        for item in (
            await session.scalars(
                select(SalesRegion).where(SalesRegion.match_keyword.in_(keywords))
            )
        ).all()
    }
    created = 0
    for _, item in parsed:
        region = existing.get((item.match_keyword, item.manager_id))
        if region:
            for field, value in item.model_dump().items():
                setattr(region, field, value)
        else:
            session.add(SalesRegion(**item.model_dump()))
            created += 1
    record_audit(
        session,
        manager,
        "sales_region.csv_import",
        "sales_region",
        "bulk",
        {"count": len(parsed), "created": created, "updated": len(parsed) - created},
    )
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return {"imported_count": 0, "errors": [{"row": 0, "error": "가져오기 중 중복이 발생했습니다."}]}
    return {"imported_count": len(parsed), "errors": []}


@router.patch("/regions/{region_id}")
async def update_region(
    region_id: int, payload: SalesRegionUpdate, session: Session, manager: OwnerStaff
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
