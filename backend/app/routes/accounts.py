from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models import Account, Contact
from app.schemas import AccountCreate, AccountResponse, ContactCreate, ContactResponse
from app.security import CurrentStaff

router = APIRouter(prefix="/api/accounts", tags=["accounts"])
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("", response_model=list[AccountResponse])
async def list_accounts(session: Session, _staff: CurrentStaff) -> list[Account]:
    return list((await session.scalars(select(Account).order_by(Account.created_at.desc()))).all())


@router.post("", response_model=AccountResponse, status_code=status.HTTP_201_CREATED)
async def create_account(payload: AccountCreate, session: Session, _staff: CurrentStaff) -> Account:
    if await session.scalar(select(Account.id).where(Account.phone == payload.phone)):
        raise HTTPException(status_code=409, detail="이미 등록된 연락처입니다.")
    account = Account(**payload.model_dump())
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


@router.get("/{account_id}", response_model=AccountResponse)
async def get_account(account_id: int, session: Session, _staff: CurrentStaff) -> Account:
    account = await session.get(Account, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="계정을 찾을 수 없습니다.")
    return account


@router.put("/{account_id}", response_model=AccountResponse)
async def update_account(
    account_id: int, payload: AccountCreate, session: Session, _staff: CurrentStaff
) -> Account:
    account = await session.get(Account, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="계정을 찾을 수 없습니다.")
    duplicate = await session.scalar(
        select(Account.id).where(Account.phone == payload.phone, Account.id != account_id)
    )
    if duplicate:
        raise HTTPException(status_code=409, detail="이미 등록된 연락처입니다.")
    for key, value in payload.model_dump().items():
        setattr(account, key, value)
    await session.commit()
    await session.refresh(account)
    return account


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    account_id: int, session: Session, _staff: CurrentStaff
) -> Response:
    account = await session.get(Account, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="계정을 찾을 수 없습니다.")
    await session.delete(account)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{account_id}/contacts", response_model=list[ContactResponse])
async def list_contacts(account_id: int, session: Session, _staff: CurrentStaff) -> list[Contact]:
    return list(
        (await session.scalars(select(Contact).where(Contact.account_id == account_id))).all()
    )


@router.post(
    "/{account_id}/contacts", response_model=ContactResponse, status_code=status.HTTP_201_CREATED
)
async def create_contact(
    account_id: int, payload: ContactCreate, session: Session, _staff: CurrentStaff
) -> Contact:
    if not await session.get(Account, account_id):
        raise HTTPException(status_code=404, detail="계정을 찾을 수 없습니다.")
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
    _staff: CurrentStaff,
) -> Contact:
    contact = await session.scalar(
        select(Contact).where(Contact.id == contact_id, Contact.account_id == account_id)
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
    account_id: int, contact_id: int, session: Session, _staff: CurrentStaff
) -> Response:
    contact = await session.scalar(
        select(Contact).where(Contact.id == contact_id, Contact.account_id == account_id)
    )
    if not contact:
        raise HTTPException(status_code=404, detail="담당자를 찾을 수 없습니다.")
    await session.delete(contact)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

