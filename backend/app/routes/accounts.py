import csv
import io
import json
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import ValidationError
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import record_audit
from app.database import get_session
from app.models import Account, Assignment, Contact, Inquiry, Interaction, Opportunity, Task
from app.schemas import (
    AccountCreate,
    AccountResponse,
    ActivityResponse,
    ContactCreate,
    ContactResponse,
    CsvTextRequest,
    InquiryResponse,
    OpportunityResponse,
    TaskResponse,
)
from app.security import (
    CurrentStaff,
    ManagerStaff,
    OwnerStaff,
    account_scope_ids,
    require_account_access,
    require_account_location_access,
)

router = APIRouter(prefix="/api/accounts", tags=["accounts"])
Session = Annotated[AsyncSession, Depends(get_session)]
CSV_ROW_LIMIT = 500


def csv_safe(value: object) -> object:
    dangerous = ("=", "+", "-", "@", "\t", "\r")
    return f"'{value}" if isinstance(value, str) and value.startswith(dangerous) else value


def normalized_name(value: str) -> str:
    return "".join(value.split()).casefold()


async def active_account(session: AsyncSession, account_id: int) -> Account:
    account = await session.scalar(
        select(Account).where(Account.id == account_id, Account.deleted_at.is_(None))
    )
    if not account:
        raise HTTPException(status_code=404, detail="계정을 찾을 수 없습니다.")
    return account


@router.get("", response_model=list[AccountResponse])
async def list_accounts(
    session: Session,
    staff: CurrentStaff,
    q: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Account]:
    statement = (
        select(Account).where(Account.deleted_at.is_(None)).order_by(Account.created_at.desc())
    )
    scoped_ids = await account_scope_ids(session, staff)
    if scoped_ids is not None:
        statement = statement.where(Account.id.in_(scoped_ids))
    if q:
        statement = statement.where(
            or_(Account.name.ilike(f"%{q}%"), Account.phone.ilike(f"%{q}%"))
        )
    return list((await session.scalars(statement.limit(limit).offset(offset))).all())


@router.post("", response_model=AccountResponse, status_code=status.HTTP_201_CREATED)
async def create_account(payload: AccountCreate, session: Session, _staff: CurrentStaff) -> Account:
    await require_account_location_access(session, payload.attributes, _staff)
    if await session.scalar(select(Account.id).where(Account.phone == payload.phone)):
        raise HTTPException(status_code=409, detail="이미 등록된 연락처입니다.")
    account = Account(**payload.model_dump())
    session.add(account)
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(status_code=409, detail="이미 등록된 연락처입니다.") from error
    await session.refresh(account)
    return account


@router.get("/data-quality/name-candidates")
async def account_name_candidates(
    name: Annotated[str, Query(min_length=1, max_length=200)],
    session: Session,
    staff: CurrentStaff,
) -> list[dict[str, object]]:
    statement = select(Account).where(Account.deleted_at.is_(None))
    scoped_ids = await account_scope_ids(session, staff)
    if scoped_ids is not None:
        statement = statement.where(Account.id.in_(scoped_ids))
    target = normalized_name(name)
    return [
        {"id": account.id, "name": account.name, "phone": account.phone}
        for account in (await session.scalars(statement)).all()
        if normalized_name(account.name) == target
    ]


@router.get("/export.csv")
async def export_accounts(session: Session, staff: CurrentStaff) -> Response:
    statement = select(Account).where(Account.deleted_at.is_(None)).order_by(Account.id)
    scoped_ids = await account_scope_ids(session, staff)
    if scoped_ids is not None:
        statement = statement.where(Account.id.in_(scoped_ids))
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["name", "phone", "attributes"])
    writer.writeheader()
    for account in (await session.scalars(statement)).all():
        row = {
            "name": account.name,
            "phone": account.phone,
            "attributes": json.dumps(account.attributes, ensure_ascii=False),
        }
        writer.writerow({key: csv_safe(value) for key, value in row.items()})
    return Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="accounts.csv"'},
    )


@router.post("/import")
async def import_accounts(
    payload: CsvTextRequest, session: Session, staff: ManagerStaff
) -> dict[str, object]:
    try:
        reader = csv.DictReader(io.StringIO(payload.csv_text.lstrip("\ufeff")))
        rows = list(reader)
    except csv.Error as error:
        return {"imported_count": 0, "errors": [{"row": 0, "error": str(error)}]}
    required = {"name", "phone"}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        return {
            "imported_count": 0,
            "errors": [{"row": 1, "error": "name, phone 헤더가 필요합니다."}],
        }
    if len(rows) > CSV_ROW_LIMIT:
        return {
            "imported_count": 0,
            "errors": [{"row": 0, "error": f"최대 {CSV_ROW_LIMIT}행까지 가져올 수 있습니다."}],
        }
    errors: list[dict[str, object]] = []
    payloads: list[tuple[int, AccountCreate]] = []
    seen_phones: set[str] = set()
    for row_number, row in enumerate(rows, start=2):
        try:
            attributes = json.loads(row.get("attributes") or "{}")
            if not isinstance(attributes, dict):
                raise TypeError("attributes는 JSON 객체여야 합니다.")
            account = AccountCreate(
                name=row.get("name") or "",
                phone=row.get("phone") or "",
                attributes=attributes,
            )
            await require_account_location_access(session, account.attributes, staff)
            if account.phone in seen_phones:
                raise ValueError("CSV 안에 중복 연락처가 있습니다.")
            seen_phones.add(account.phone)
            payloads.append((row_number, account))
        except (ValidationError, ValueError, TypeError, json.JSONDecodeError) as error:
            errors.append({"row": row_number, "error": str(error)})
    existing = set(
        (await session.scalars(select(Account.phone).where(Account.phone.in_(seen_phones)))).all()
    )
    for row_number, account in payloads:
        if account.phone in existing:
            errors.append({"row": row_number, "error": "이미 등록된 연락처입니다."})
    if errors:
        return {"imported_count": 0, "errors": errors}
    accounts = [Account(**account.model_dump()) for _, account in payloads]
    session.add_all(accounts)
    record_audit(
        session,
        staff,
        "account.csv_import",
        "account",
        "bulk",
        {"count": len(accounts)},
    )
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return {
            "imported_count": 0,
            "errors": [{"row": 0, "error": "가져오기 중 중복 연락처가 발생했습니다."}],
        }
    return {"imported_count": len(accounts), "errors": []}


@router.get("/{account_id}", response_model=AccountResponse)
async def get_account(account_id: int, session: Session, staff: CurrentStaff) -> Account:
    account = await active_account(session, account_id)
    await require_account_access(session, account_id, staff)
    return account


@router.get("/{account_id}/overview")
async def account_overview(
    account_id: int, session: Session, staff: CurrentStaff
) -> dict[str, object]:
    account = await active_account(session, account_id)
    await require_account_access(session, account_id, staff)
    contacts = list(
        (
            await session.scalars(
                select(Contact).where(
                    Contact.account_id == account_id, Contact.deleted_at.is_(None)
                )
            )
        ).all()
    )
    inquiry_query = select(Inquiry).where(Inquiry.account_id == account_id)
    activity_query = select(Interaction).where(Interaction.account_id == account_id)
    opportunity_query = select(Opportunity).where(Opportunity.account_id == account_id)
    task_query = select(Task).where(Task.account_id == account_id)
    if staff.role == "rep":
        current_assignee = (
            select(Assignment.assignee_id)
            .where(Assignment.inquiry_id == Inquiry.id)
            .order_by(Assignment.assigned_at.desc(), Assignment.id.desc())
            .limit(1)
            .correlate(Inquiry)
            .scalar_subquery()
        )
        inquiry_query = inquiry_query.where(current_assignee == staff.id)
        activity_query = activity_query.where(Interaction.staff_id == staff.id)
        opportunity_query = opportunity_query.where(Opportunity.assignee_id == staff.id)
        task_query = task_query.where(Task.assignee_id == staff.id)
    inquiries = list(
        (await session.scalars(inquiry_query.order_by(Inquiry.created_at.desc()))).all()
    )
    activities = list(
        (await session.scalars(activity_query.order_by(Interaction.created_at.desc()))).all()
    )
    opportunities = list(
        (await session.scalars(opportunity_query.order_by(Opportunity.updated_at.desc()))).all()
    )
    tasks = list((await session.scalars(task_query.order_by(Task.due_at))).all())
    timeline = sorted(
        [
            *(
                {"kind": "inquiry", "id": item.id, "at": item.created_at, "text": item.content}
                for item in inquiries
            ),
            *(
                {
                    "kind": "activity",
                    "id": item.id,
                    "at": item.created_at,
                    "text": item.content or item.type,
                }
                for item in activities
            ),
            *(
                {"kind": "opportunity", "id": item.id, "at": item.created_at, "text": item.title}
                for item in opportunities
            ),
            *(
                {"kind": "task", "id": item.id, "at": item.created_at, "text": item.title}
                for item in tasks
            ),
        ],
        key=lambda item: item["at"],
        reverse=True,
    )
    return {
        "account": AccountResponse.model_validate(account).model_dump(),
        "contacts": [ContactResponse.model_validate(item).model_dump() for item in contacts],
        "inquiries": [InquiryResponse.model_validate(item).model_dump() for item in inquiries],
        "activities": [ActivityResponse.model_validate(item).model_dump() for item in activities],
        "opportunities": [
            OpportunityResponse.model_validate(item).model_dump() for item in opportunities
        ],
        "tasks": [TaskResponse.model_validate(item).model_dump() for item in tasks],
        "timeline": timeline,
    }


@router.put("/{account_id}", response_model=AccountResponse)
async def update_account(
    account_id: int, payload: AccountCreate, session: Session, staff: CurrentStaff
) -> Account:
    account = await active_account(session, account_id)
    await require_account_access(session, account_id, staff)
    await require_account_location_access(session, payload.attributes, staff)
    duplicate = await session.scalar(
        select(Account.id).where(Account.phone == payload.phone, Account.id != account_id)
    )
    if duplicate:
        raise HTTPException(status_code=409, detail="이미 등록된 연락처입니다.")
    for key, value in payload.model_dump().items():
        setattr(account, key, value)
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(status_code=409, detail="이미 등록된 연락처입니다.") from error
    await session.refresh(account)
    return account


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(account_id: int, session: Session, staff: OwnerStaff) -> Response:
    account = await active_account(session, account_id)
    deleted_at = datetime.now(timezone.utc)
    account.deleted_at = deleted_at
    await session.execute(
        update(Contact)
        .where(Contact.account_id == account_id, Contact.deleted_at.is_(None))
        .values(deleted_at=deleted_at)
    )
    record_audit(session, staff, "account.delete", "account", account_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{account_id}/restore", response_model=AccountResponse)
async def restore_account(account_id: int, session: Session, staff: OwnerStaff) -> Account:
    account = await session.scalar(
        select(Account).where(Account.id == account_id, Account.deleted_at.is_not(None))
    )
    if not account:
        raise HTTPException(status_code=404, detail="계정을 찾을 수 없습니다.")
    deleted_at = account.deleted_at
    account.deleted_at = None
    await session.execute(
        update(Contact)
        .where(Contact.account_id == account_id, Contact.deleted_at == deleted_at)
        .values(deleted_at=None)
    )
    record_audit(session, staff, "account.restore", "account", account_id)
    await session.commit()
    await session.refresh(account)
    return account


@router.get("/{account_id}/contacts", response_model=list[ContactResponse])
async def list_contacts(account_id: int, session: Session, staff: CurrentStaff) -> list[Contact]:
    await active_account(session, account_id)
    await require_account_access(session, account_id, staff)
    return list(
        (
            await session.scalars(
                select(Contact).where(
                    Contact.account_id == account_id, Contact.deleted_at.is_(None)
                )
            )
        ).all()
    )


@router.get("/{account_id}/data-quality")
async def account_data_quality(
    account_id: int, session: Session, staff: CurrentStaff
) -> dict[str, list[dict[str, object]]]:
    await active_account(session, account_id)
    await require_account_access(session, account_id, staff)
    contacts = list(
        (
            await session.scalars(
                select(Contact).where(
                    Contact.account_id == account_id, Contact.deleted_at.is_(None)
                )
            )
        ).all()
    )
    duplicates = []
    for field in ("phone", "email"):
        groups: dict[str, list[Contact]] = {}
        for contact in contacts:
            value = getattr(contact, field)
            if value:
                groups.setdefault(value.casefold(), []).append(contact)
        duplicates.extend(
            {
                "field": field,
                "value": getattr(group[0], field),
                "contact_ids": [contact.id for contact in group],
            }
            for group in groups.values()
            if len(group) > 1
        )
    return {"duplicate_contacts": duplicates}


@router.post(
    "/{account_id}/contacts", response_model=ContactResponse, status_code=status.HTTP_201_CREATED
)
async def create_contact(
    account_id: int, payload: ContactCreate, session: Session, staff: CurrentStaff
) -> Contact:
    await active_account(session, account_id)
    await require_account_access(session, account_id, staff)
    contact = Contact(account_id=account_id, **payload.model_dump(mode="json"))
    session.add(contact)
    await session.commit()
    await session.refresh(contact)
    return contact


@router.put("/{account_id}/contacts/{contact_id}", response_model=ContactResponse)
async def update_contact(
    account_id: int,
    contact_id: int,
    payload: ContactCreate,
    session: Session,
    staff: CurrentStaff,
) -> Contact:
    await active_account(session, account_id)
    await require_account_access(session, account_id, staff)
    contact = await session.scalar(
        select(Contact).where(
            Contact.id == contact_id,
            Contact.account_id == account_id,
            Contact.deleted_at.is_(None),
        )
    )
    if not contact:
        raise HTTPException(status_code=404, detail="담당자를 찾을 수 없습니다.")
    for key, value in payload.model_dump(mode="json").items():
        setattr(contact, key, value)
    await session.commit()
    await session.refresh(contact)
    return contact


@router.delete("/{account_id}/contacts/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_contact(
    account_id: int, contact_id: int, session: Session, staff: ManagerStaff
) -> Response:
    await active_account(session, account_id)
    await require_account_access(session, account_id, staff)
    contact = await session.scalar(
        select(Contact).where(
            Contact.id == contact_id,
            Contact.account_id == account_id,
            Contact.deleted_at.is_(None),
        )
    )
    if not contact:
        raise HTTPException(status_code=404, detail="담당자를 찾을 수 없습니다.")
    contact.deleted_at = datetime.now(timezone.utc)
    record_audit(session, staff, "contact.delete", "contact", contact.id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
