import uuid
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database import get_session
from app.models import Staff
from app.routes.inquiries import router
from app.security import get_current_staff


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
