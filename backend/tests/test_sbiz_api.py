import uuid
from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.localdata import building_age_score, building_query_from_sbiz, parse_building_title
from app.models import Lead, Staff
from app.routes.outbound import SbizSyncRequest, enrich_lead_building, sync_sbiz_leads


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
                        "ldongCd": "1114012300",
                        "lnoMnno": 100,
                        "lnoSlno": 2,
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
    assert lead.lead_score == 50


class BuildingResponse(FakeResponse):
    def json(self) -> dict[str, object]:
        return {
            "response": {
                "header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE"},
                "body": {
                    "items": {
                        "item": [
                            {
                                "bldNm": "가상빌딩",
                                "mainAtchGbCdNm": "주건축물",
                                "mainPurpsCdNm": "근린생활시설",
                                "totArea": "1200.5",
                                "useAprDay": "20000115",
                            }
                        ]
                    }
                },
            }
        }


class BuildingClient(FakeClient):
    async def get(self, _url: str, **_kwargs: object) -> BuildingResponse:
        return BuildingResponse()


@pytest.mark.asyncio
async def test_building_enrichment_updates_score_and_reasoning(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = Staff(
        id=uuid.uuid4(),
        name="관리자",
        email="building-manager@example.test",
        hashed_password="not-used",
        role="manager",
    )
    lead = Lead(
        name="가상상가",
        external_id="STORE-1",
        source="sbiz",
        raw_data={"ldongCd": "1168010100", "lnoMnno": 825, "lnoSlno": 0},
        lead_score=50,
        lead_score_reasoning={"source_data": "상가정보 확인"},
    )
    session.add_all([manager, lead])
    await session.commit()
    monkeypatch.setattr(
        "app.routes.outbound.get_settings",
        lambda: SimpleNamespace(effective_building_hub_api_key="test-key"),
    )
    monkeypatch.setattr(
        "app.routes.outbound.httpx.AsyncClient", lambda **_kwargs: BuildingClient()
    )

    result = await enrich_lead_building(lead.id, session, manager)

    assert result["lead_score"] in {35, 55, 70, 85}
    assert "사용승인일 2000-01-15" in result["reasoning"]["building_age"]
    assert lead.raw_data["building_register"]["building_name"] == "가상빌딩"


def test_building_helpers_use_sbiz_location_and_explain_age() -> None:
    query = building_query_from_sbiz(
        {"ldongCd": "1168010100", "lnoMnno": 825, "lnoSlno": 0}
    )
    building = parse_building_title(BuildingResponse().json())
    score, reasoning = building_age_score(date(2000, 1, 15), date(2026, 8, 24))

    assert query["sigunguCd"] == "11680" and query["bjdongCd"] == "10100"
    assert query["bun"] == "0825" and query["ji"] == "0000"
    assert building["approval_date"] == "2000-01-15"
    assert score == 70 and "26년" in reasoning
