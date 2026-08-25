import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.database import get_session
from app.models import Account, Assignment, AuditLog, Contact, Inquiry, Lead, SalesRegion, Staff
from app.routes.accounts import (
    create_contact,
    delete_account,
    delete_contact,
    list_accounts,
    list_contacts,
    restore_account,
    update_contact,
)
from app.routes.accounts import router as accounts_router
from app.routes.admin import api_status
from app.routes.admin import router as admin_router
from app.routes.inquiries import router
from app.routes.search import router as search_router
from app.routes.staff import (
    create_staff,
    reset_staff_password,
    update_staff_active,
    update_staff_role,
)
from app.routes.staff import router as staff_router
from app.schemas import (
    ContactCreate,
    StaffActiveUpdate,
    StaffCreate,
    StaffPasswordReset,
    StaffRoleUpdate,
)
from app.security import get_current_staff, require_owner, verify_password


class ApiStatusResponse:
    def __init__(self, kind: str = "sbiz") -> None:
        self.kind = kind

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict[str, object]:
        if self.kind == "naver":
            return {"items": []}
        if self.kind in {"building", "permit"}:
            return {"response": {"header": {"resultCode": "00"}}}
        return {"header": {"resultCode": "00"}}


class ApiStatusClient:
    async def __aenter__(self) -> "ApiStatusClient":  # noqa: PYI034
        return self

    async def __aexit__(self, *_args: object) -> None:
        pass

    async def get(self, url: str, **_kwargs: object) -> ApiStatusResponse:
        if "BldRgstHubService" in url:
            return ApiStatusResponse("building")
        if "ArchPmsHubService" in url:
            return ApiStatusResponse("permit")
        if "openapi.naver.com" in url:
            return ApiStatusResponse("naver")
        return ApiStatusResponse()


def test_rep_manual_assignment_returns_403() -> None:
    app = FastAPI()
    app.include_router(router)
    rep = Staff(
        id=uuid.uuid4(),
        name="일반 담당자",
        email="rep@example.test",
        hashed_password="not-used",
        role="rep",
    )

    async def override_staff() -> Staff:
        return rep

    async def override_session() -> AsyncIterator[None]:
        yield None

    app.dependency_overrides[get_current_staff] = override_staff
    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as client:
        response = client.post(
            "/api/inquiries/1/assign",
            json={"assignee_id": str(uuid.uuid4())},
        )
    assert response.status_code == 403


def test_rep_cannot_mutate_unowned_outbound() -> None:
    from app.routes.outbound import require_lead_access

    rep = Staff(
        id=uuid.uuid4(),
        name="일반 담당자",
        email="outbound-rep@example.test",
        hashed_password="not-used",
        role="rep",
    )
    lead = Lead(
        name="다른 담당자의 리드",
        source="csv",
        raw_data={},
        lead_score=50,
        lead_score_reasoning={},
        assignee_id=uuid.uuid4(),
    )
    with pytest.raises(HTTPException) as error:
        require_lead_access(lead, rep)
    assert error.value.status_code == 403


@pytest.mark.parametrize("role", ["manager", "rep"])
def test_non_owner_cannot_use_natural_language_search(role: str) -> None:
    app = FastAPI()
    app.include_router(search_router)
    rep = Staff(
        id=uuid.uuid4(),
        name="일반 담당자",
        email="search-rep@example.test",
        hashed_password="not-used",
        role=role,
    )

    async def override_staff() -> Staff:
        return rep

    async def override_session() -> AsyncIterator[None]:
        yield None

    app.dependency_overrides[get_current_staff] = override_staff
    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as client:
        response = client.post("/api/search", json={"question": "전체 리드 연락처"})
    assert response.status_code == 403


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/api/accounts/1", "delete"),
        ("/api/accounts/1/contacts/1", "delete"),
    ],
)
def test_rep_cannot_delete_crm_records(path: str, method: str) -> None:
    app = FastAPI()
    app.include_router(accounts_router)
    rep = Staff(
        id=uuid.uuid4(),
        name="일반 담당자",
        email="delete-rep@example.test",
        hashed_password="not-used",
        role="rep",
    )

    async def override_staff() -> Staff:
        return rep

    async def override_session() -> AsyncIterator[None]:
        yield None

    app.dependency_overrides[get_current_staff] = override_staff
    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as client:
        response = client.request(method, path)
    assert response.status_code == 403


def test_manager_cannot_change_staff_active_state() -> None:
    app = FastAPI()
    app.include_router(staff_router)
    manager = Staff(
        id=uuid.uuid4(),
        name="관리자",
        email="active-manager@example.test",
        hashed_password="not-used",
        role="manager",
    )

    async def override_staff() -> Staff:
        return manager

    async def override_session() -> AsyncIterator[None]:
        yield None

    app.dependency_overrides[get_current_staff] = override_staff
    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as client:
        response = client.patch(f"/api/staff/{uuid.uuid4()}/active", json={"is_active": False})
    assert response.status_code == 403


def test_manager_cannot_change_own_role() -> None:
    app = FastAPI()
    app.include_router(staff_router)
    manager = Staff(
        id=uuid.uuid4(),
        name="관리자",
        email="self-role-manager@example.test",
        hashed_password="not-used",
        role="manager",
    )

    async def override_staff() -> Staff:
        return manager

    async def override_session() -> AsyncIterator[None]:
        yield None

    app.dependency_overrides[get_current_staff] = override_staff
    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as client:
        response = client.patch(f"/api/staff/{manager.id}/role", json={"role": "manager"})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_owner_manages_staff_accounts(session: AsyncSession) -> None:
    owner = Staff(
        id=uuid.uuid4(),
        name="총관리자",
        email="owner@example.test",
        hashed_password="not-used",
        role="owner",
    )
    session.add(owner)
    await session.commit()
    created = await create_staff(
        StaffCreate(
            name="관리자",
            email="manager-new@example.com",
            role="manager",
            password="initial-pass-1234",
        ),
        session,
        owner,
    )
    assert created.role == "manager"
    assert verify_password("initial-pass-1234", created.hashed_password)

    updated = await update_staff_role(created.id, StaffRoleUpdate(role="rep"), session, owner)
    assert updated.role == "rep"

    deactivated = await update_staff_active(
        created.id, StaffActiveUpdate(is_active=False), session, owner
    )
    assert deactivated.is_active is False

    await reset_staff_password(
        created.id,
        StaffPasswordReset(password="changed-pass-5678"),
        session,
        owner,
    )
    assert verify_password("changed-pass-5678", created.hashed_password)
    logs = list((await session.scalars(select(AuditLog).order_by(AuditLog.id))).all())
    assert [log.action for log in logs] == [
        "staff.create",
        "staff.role_change",
        "staff.active_change",
        "staff.password_reset",
    ]
    assert "password" not in str([log.details for log in logs]).lower()


@pytest.mark.asyncio
async def test_current_unresolved_assignments_block_rep_role_and_active_changes(
    session: AsyncSession,
) -> None:
    owner = Staff(
        id=uuid.uuid4(),
        name="총관리자",
        email="assignment-owner@example.test",
        hashed_password="not-used",
        role="owner",
    )
    current = Staff(
        id=uuid.uuid4(),
        name="현재 담당자",
        email="assignment-current@example.test",
        hashed_password="not-used",
        role="rep",
    )
    historical = Staff(
        id=uuid.uuid4(),
        name="과거 담당자",
        email="assignment-history@example.test",
        hashed_password="not-used",
        role="rep",
    )
    replacement = Staff(
        id=uuid.uuid4(),
        name="교체 담당자",
        email="assignment-replacement@example.test",
        hashed_password="not-used",
        role="rep",
    )
    account = Account(name="배정 고객", phone="01099990000", attributes={})
    session.add_all([owner, current, historical, replacement, account])
    await session.flush()
    current_inquiry = Inquiry(
        account_id=account.id, channel="web", content="현재 문의", status="routed"
    )
    reassigned_inquiry = Inquiry(
        account_id=account.id, channel="web", content="재배정 문의", status="routed"
    )
    session.add_all([current_inquiry, reassigned_inquiry])
    await session.flush()
    session.add_all(
        [
            Assignment(
                inquiry_id=current_inquiry.id,
                assignee_id=current.id,
                method="manual",
            ),
            Assignment(
                inquiry_id=reassigned_inquiry.id,
                assignee_id=historical.id,
                method="manual",
            ),
            Assignment(
                inquiry_id=reassigned_inquiry.id,
                assignee_id=replacement.id,
                method="manual",
            ),
        ]
    )
    await session.commit()

    with pytest.raises(HTTPException) as role_error:
        await update_staff_role(current.id, StaffRoleUpdate(role="manager"), session, owner)
    assert role_error.value.status_code == 409
    with pytest.raises(HTTPException) as active_error:
        await update_staff_active(current.id, StaffActiveUpdate(is_active=False), session, owner)
    assert active_error.value.status_code == 409

    updated = await update_staff_active(
        historical.id, StaffActiveUpdate(is_active=False), session, owner
    )
    assert updated.is_active is False


@pytest.mark.asyncio
async def test_active_assigned_lead_blocks_staff_deactivation(session: AsyncSession) -> None:
    owner = Staff(
        id=uuid.uuid4(),
        name="총관리자",
        email="lead-owner@example.test",
        hashed_password="not-used",
        role="owner",
    )
    rep = Staff(
        id=uuid.uuid4(),
        name="리드 담당자",
        email="lead-rep@example.test",
        hashed_password="not-used",
        role="rep",
    )
    session.add_all([owner, rep])
    await session.flush()
    session.add(
        Lead(
            name="진행 중 리드",
            source="csv",
            raw_data={},
            lead_score=50,
            lead_score_reasoning={},
            assignee_id=rep.id,
        )
    )
    await session.commit()

    with pytest.raises(HTTPException) as error:
        await update_staff_active(rep.id, StaffActiveUpdate(is_active=False), session, owner)
    assert error.value.status_code == 409
    assert rep.is_active is True


@pytest.mark.asyncio
async def test_manager_cannot_use_owner_permissions() -> None:
    manager = Staff(
        id=uuid.uuid4(),
        name="관리자",
        email="manager@example.test",
        hashed_password="not-used",
        role="manager",
    )
    with pytest.raises(HTTPException) as error:
        await require_owner(manager)
    assert getattr(error.value, "status_code", None) == 403


@pytest.mark.asyncio
async def test_inactive_staff_token_is_rejected(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(jwt_secret_key="x" * 32, _env_file=None)
    monkeypatch.setattr("app.security.get_settings", lambda: settings)
    staff = Staff(
        id=uuid.uuid4(),
        name="비활성 담당자",
        email="inactive@example.test",
        hashed_password="not-used",
        role="rep",
        is_active=False,
    )
    session.add(staff)
    await session.commit()
    token = jwt.encode(
        {
            "sub": str(staff.id),
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )

    with pytest.raises(HTTPException) as error:
        await get_current_staff(token, session)
    assert getattr(error.value, "status_code", None) == 401


@pytest.mark.asyncio
async def test_jwt_without_exp_is_rejected(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(jwt_secret_key="x" * 32, _env_file=None)
    monkeypatch.setattr("app.security.get_settings", lambda: settings)
    token = jwt.encode(
        {"sub": str(uuid.uuid4())},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )

    with pytest.raises(HTTPException) as error:
        await get_current_staff(token, session)
    assert getattr(error.value, "status_code", None) == 401


@pytest.mark.asyncio
async def test_owner_soft_deletes_account(session: AsyncSession) -> None:
    owner = Staff(
        id=uuid.uuid4(),
        name="총관리자",
        email="soft-delete@example.test",
        hashed_password="not-used",
        role="owner",
    )
    account = Account(name="삭제 대상", phone="01077778888", attributes={})
    session.add_all([owner, account])
    await session.flush()
    contact = Contact(account_id=account.id, name="담당자", phone="01077779999")
    session.add(contact)
    await session.commit()

    await delete_account(account.id, session, owner)

    assert account.deleted_at is not None
    await session.refresh(contact)
    assert contact.deleted_at.replace(tzinfo=timezone.utc) == account.deleted_at
    assert await list_accounts(session, owner) == []

    restored = await restore_account(account.id, session, owner)

    await session.refresh(contact)
    assert restored.deleted_at is None
    assert contact.deleted_at is None
    assert [log.action for log in (await session.scalars(select(AuditLog))).all()] == [
        "account.delete",
        "account.restore",
    ]


@pytest.mark.asyncio
async def test_deleted_account_rejects_all_contact_operations(session: AsyncSession) -> None:
    owner = Staff(
        id=uuid.uuid4(),
        name="총관리자",
        email="deleted-parent@example.test",
        hashed_password="not-used",
        role="owner",
    )
    account = Account(name="삭제 고객사", phone="01088889999", attributes={})
    session.add_all([owner, account])
    await session.flush()
    contact = Contact(account_id=account.id, name="담당자")
    session.add(contact)
    await session.commit()
    await delete_account(account.id, session, owner)
    payload = ContactCreate(name="변경 담당자", phone="010-1111-2222")

    operations = (
        list_contacts(account.id, session, owner),
        create_contact(account.id, payload, session, owner),
        update_contact(account.id, contact.id, payload, session, owner),
        delete_contact(account.id, contact.id, session, owner),
    )
    for operation in operations:
        with pytest.raises(HTTPException) as error:
            await operation
        assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_contact_soft_delete_is_audited(session: AsyncSession) -> None:
    manager = Staff(
        id=uuid.uuid4(),
        name="관리자",
        email="contact-audit@example.test",
        hashed_password="not-used",
        role="manager",
    )
    account = Account(
        name="감사 고객사", phone="01099990000", attributes={"location": "서울 중구"}
    )
    session.add_all([manager, account])
    await session.flush()
    session.add(SalesRegion(region_name="서울", match_keyword="서울", manager_id=manager.id))
    contact = Contact(account_id=account.id, name="삭제 담당자")
    session.add(contact)
    await session.commit()

    await delete_contact(account.id, contact.id, session, manager)

    log = await session.scalar(select(AuditLog).where(AuditLog.action == "contact.delete"))
    assert contact.deleted_at is not None
    assert log is not None
    assert log.actor_id == manager.id
    assert log.resource_id == str(contact.id)


def test_manager_cannot_view_api_status() -> None:
    app = FastAPI()
    app.include_router(admin_router)
    manager = Staff(
        id=uuid.uuid4(),
        name="관리자",
        email="api-manager@example.test",
        hashed_password="not-used",
        role="manager",
    )

    async def override_staff() -> Staff:
        return manager

    async def override_session() -> AsyncIterator[None]:
        yield None

    app.dependency_overrides[get_current_staff] = override_staff
    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as client:
        response = client.get("/api/admin/api-status")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_owner_api_status_does_not_expose_secrets(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = Staff(
        id=uuid.uuid4(),
        name="총관리자",
        email="api-owner@example.test",
        hashed_password="not-used",
        role="owner",
    )
    monkeypatch.setattr(
        "app.routes.admin.get_settings",
        lambda: SimpleNamespace(
            data_go_kr_service_key="public-data-secret",
            effective_building_hub_api_key="building-secret",
            gemini_api_key="gemini-secret",
            naver_client_id="naver-id",
            naver_client_secret="naver-secret",
            outbound_email_mode="dry_run",
            test_email_address=None,
            email_provider_api_key=None,
        ),
    )
    monkeypatch.setattr("app.routes.admin.httpx.AsyncClient", lambda **_kwargs: ApiStatusClient())

    result = await api_status(session, owner)

    assert all(service["status"] in {"available", "configured"} for service in result["services"])
    serialized = str(result)
    assert "public-data-secret" not in serialized
    assert "building-secret" not in serialized
    assert "gemini-secret" not in serialized
    assert "naver-secret" not in serialized
