import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Lead, Staff
from app.routes.outbound import SbizSyncRequest, sync_sbiz_leads


class FakeResponse:
    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict[str, object]:
        return {
            "header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE"},
            "body": {
                "totalCount": 1,
                "items": [
                    {
                        "bizesId": "MA-1",
                        "bizesNm": "가상호텔",
                        "indsSclsNm": "호텔",
                        "rdnmAdr": "서울특별시 중구",
                    }
                ],
            },
        }


class FakeClient:
    async def __aenter__(self) -> "FakeClient":  # noqa: PYI034
        return self

    async def __aexit__(self, *_args: object) -> None:
        pass

    async def get(self, _url: str, **_kwargs: object) -> FakeResponse:
        return FakeResponse()


@pytest.mark.asyncio
async def test_sbiz_sync_is_idempotent(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = Staff(
        id=uuid.uuid4(),
        name="관리자",
        email="sbiz-manager@example.test",
        hashed_password="not-used",
        role="manager",
    )
    session.add(manager)
    await session.commit()
    monkeypatch.setattr(
        "app.routes.outbound.get_settings",
        lambda: SimpleNamespace(data_go_kr_service_key="test-key"),
    )
    monkeypatch.setattr(
        "app.routes.outbound.httpx.AsyncClient", lambda **_kwargs: FakeClient()
    )

    request = SbizSyncRequest(region_code="11", page=1, rows=100)
    first = await sync_sbiz_leads(request, session, manager)
    second = await sync_sbiz_leads(request, session, manager)

    assert first == {
        "fetched_count": 1,
        "created_count": 1,
        "updated_count": 0,
        "total_count": 1,
    }
    assert second["created_count"] == 0
    assert await session.scalar(select(func.count(Lead.id))) == 1
    lead = await session.scalar(select(Lead))
    assert lead and lead.external_id == "MA-1" and lead.years_in_business is None
