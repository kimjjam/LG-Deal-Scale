import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.localdata import SERVICE_ID, parse_localdata_rows
from app.models import (
    Account,
    AuditLog,
    Inquiry,
    Interaction,
    Lead,
    Opportunity,
    OpportunityStageHistory,
    OutboundDraft,
    Product,
    Score,
    Staff,
    Task,
)
from app.routes import public
from app.routes.accounts import export_accounts, import_accounts
from app.routes.crm import dashboard
from app.routes.outbound import (
    edit_draft,
    export_leads,
    generate_draft,
    import_leads,
    record_actual_contact,
    stop_sequence,
)
from app.routes.public import _relevant_products
from app.schemas import (
    ChatMessage,
    CsvTextRequest,
    DraftEditRequest,
    IntakeFields,
    ManualContactRequest,
    PublicSubmissionRequest,
)


def staff(role: str = "manager") -> Staff:
    return Staff(
        id=uuid.uuid4(),
        name=role,
        email=f"{uuid.uuid4()}@example.test",
        hashed_password="not-used",
        role=role,
    )


@pytest.mark.asyncio
async def test_outbound_edit_context_contact_and_stop_are_safe(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = staff()
    lead = Lead(
        name="가상호텔",
        raw_data={},
        lead_score=70,
        lead_score_reasoning={},
        pipeline_stage="approved",
    )
    product = Product(
        name="객실 냉장고",
        brand="LG",
        category="냉장고",
        price=Decimal(500000),
        product_url="https://example.test/fridge",
    )
    session.add_all([manager, lead, product])
    await session.flush()
    first = OutboundDraft(
        lead_id=lead.id,
        sequence_step=1,
        subject="이전 제목",
        body="이전 본문",
        reviewed_by=manager.id,
        send_mode="dry_run",
        sent_at=datetime.now(timezone.utc),
    )
    session.add(first)
    await session.commit()
    prompts: list[str] = []

    class LLM:
        async def structured(self, prompt: str, result_type: type) -> object:
            prompts.append(prompt)
            return result_type(subject="후속 제목", body="후속 본문")

    monkeypatch.setattr("app.routes.outbound.get_llm_client", lambda: LLM())
    monkeypatch.setattr(
        "app.routes.outbound.get_settings",
        lambda: SimpleNamespace(outbound_email_mode="dry_run"),
    )
    created = await generate_draft(lead.id, session, manager)
    assert "이전 제목" in prompts[0] and "이전 본문" in prompts[0]

    second = await session.get(OutboundDraft, created["id"])
    assert second is not None
    second.reviewed_by = manager.id
    lead.pipeline_stage = "approved"
    await session.commit()
    edited = await edit_draft(
        second.id,
        DraftEditRequest(subject="수정 제목", body="수정 본문"),
        session,
        manager,
    )
    assert edited["reviewed"] is False
    assert lead.pipeline_stage == "draft_generated"

    payload = ManualContactRequest(channel="phone", note="대표와 통화")
    await record_actual_contact(lead.id, payload, session, manager)
    await record_actual_contact(lead.id, payload, session, manager)
    assert lead.pipeline_stage == "contacted"
    assert (
        await session.scalar(
            select(func.count(AuditLog.id)).where(AuditLog.action == "lead.actual_contact")
        )
        == 2
    )
    await stop_sequence(lead.id, session, manager)
    await stop_sequence(lead.id, session, manager)
    assert lead.pipeline_stage == "dropped"
    assert (
        await session.scalar(
            select(func.count(AuditLog.id)).where(AuditLog.action == "lead.sequence_stop")
        )
        == 1
    )
    with pytest.raises(HTTPException):
        await record_actual_contact(lead.id, payload, session, manager)


@pytest.mark.asyncio
async def test_csv_imports_are_atomic_and_exports_exclude_deleted(
    session: AsyncSession,
) -> None:
    manager = staff()
    session.add(manager)
    await session.commit()
    invalid_accounts = CsvTextRequest(
        csv_text='name,phone,attributes\n정상,010-1111-2222,"{}"\n오류,12,"{}"'
    )
    result = await import_accounts(invalid_accounts, session, manager)
    assert result["imported_count"] == 0 and result["errors"]
    assert await session.scalar(select(func.count(Account.id))) == 0

    valid_accounts = CsvTextRequest(
        csv_text='name,phone,attributes\n정상,010-1111-2222,"{""room_count"": 8}"'
    )
    result = await import_accounts(valid_accounts, session, manager)
    assert result == {"imported_count": 1, "errors": []}
    account = await session.scalar(select(Account))
    assert account is not None
    account.deleted_at = datetime.now(timezone.utc)
    await session.commit()
    exported = await export_accounts(session, manager)
    assert "정상" not in exported.body.decode("utf-8")

    invalid_leads = CsvTextRequest(csv_text="name,lead_score\n정상,80\n오류,101")
    result = await import_leads(invalid_leads, session, manager)
    assert result["imported_count"] == 0
    assert await session.scalar(select(func.count(Lead.id))) == 0
    result = await import_leads(
        CsvTextRequest(csv_text="name,lead_score,business_type\n가상숙소,80,호텔"),
        session,
        manager,
    )
    assert result == {"imported_count": 1, "errors": []}
    assert "가상숙소" in (await export_leads(session, manager)).body.decode("utf-8")

    rejected = await import_leads(
        CsvTextRequest(csv_text="name,lead_score,pipeline_stage\n완료위장,80,converted"),
        session,
        manager,
    )
    assert rejected["imported_count"] == 0 and rejected["errors"]


@pytest.mark.asyncio
async def test_csv_exports_neutralize_formula_cells(session: AsyncSession) -> None:
    manager = staff()
    account = Account(name="=HYPERLINK(\"bad\")", phone="01011112222", attributes={})
    lead = Lead(
        name="+CMD",
        address="@formula",
        raw_data={},
        lead_score=50,
        lead_score_reasoning={},
    )
    session.add_all([manager, account, lead])
    await session.commit()
    assert "'=HYPERLINK" in (await export_accounts(session, manager)).body.decode("utf-8")
    exported_leads = (await export_leads(session, manager)).body.decode("utf-8")
    assert "'+CMD" in exported_leads and "'@formula" in exported_leads


@pytest.mark.asyncio
async def test_csv_row_cap_and_rep_export_visibility(session: AsyncSession) -> None:
    manager, rep = staff(), staff("rep")
    visible = Account(name="담당 고객", phone="01055556666", attributes={})
    hidden = Account(name="비담당 고객", phone="01077778888", attributes={})
    session.add_all([manager, rep, visible, hidden])
    await session.flush()
    session.add(
        Opportunity(
            account_id=visible.id,
            assignee_id=rep.id,
            title="담당 영업기회",
            probability=10,
            stage="qualify",
        )
    )
    await session.commit()
    exported = (await export_accounts(session, rep)).body.decode("utf-8")
    assert "담당 고객" in exported and "비담당 고객" not in exported

    oversized = "name,phone\n" + "\n".join(
        f"고객{index},010{index:08d}" for index in range(501)
    )
    result = await import_accounts(CsvTextRequest(csv_text=oversized), session, manager)
    assert result["imported_count"] == 0
    assert "500행" in result["errors"][0]["error"]


@pytest.mark.asyncio
async def test_public_product_filter_and_returning_attributes(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    account = Account(
        name="재방문 호텔",
        phone="01022223333",
        attributes={"room_count": 12, "business_type": "모텔"},
    )
    fridge = Product(
        name="객실 냉장고",
        brand="LG",
        category="냉장고",
        price=Decimal(500000),
        product_url="https://example.test/fridge",
    )
    washer = Product(
        name="상업용 세탁기",
        brand="LG",
        category="세탁기",
        price=Decimal(900000),
        product_url="https://example.test/washer",
    )
    session.add_all([account, fridge, washer])
    await session.commit()
    prompts: list[str] = []
    captured_raw: list[object] = []

    class LLM:
        async def text(self, prompt: str) -> str:
            prompts.append(prompt)
            return "추천 분석"

    async def fake_create_inquiry(
        session: AsyncSession, account_id: int, channel: str, content: str, raw: object, llm: object
    ) -> tuple[Inquiry, bool]:
        del channel, llm
        captured_raw.append(raw)
        inquiry = Inquiry(account_id=account_id, channel="public_web", content=content)
        session.add(inquiry)
        await session.commit()
        return inquiry, False

    monkeypatch.setattr(public, "get_llm_client", lambda: LLM())
    monkeypatch.setattr(public, "create_inquiry", fake_create_inquiry)
    monkeypatch.setattr(public, "_nearby_stores", lambda _location: _async_value([]))
    request = Request({"type": "http", "client": ("127.0.0.1", 1)})
    response = await public.submit.__wrapped__(
        request,
        PublicSubmissionRequest(
            messages=[ChatMessage(role="user", content="냉장고 문의")],
            fields=IntakeFields(
                business_name="재방문 호텔",
                phone=account.phone,
                inquiry="객실 냉장고가 필요합니다",
                product="냉장고 6대",
                business_type="호텔",
            ),
        ),
        session,
    )
    assert [item.name for item in response.products] == ["객실 냉장고"]
    assert "상업용 세탁기" not in prompts[0]
    assert account.attributes == {"room_count": 12, "business_type": "모텔"}
    assert response.inquiry_id
    assert captured_raw[0][-1]["type"] == "intake_fields"  # type: ignore[index]


async def _async_value(value: object) -> object:
    return value


def test_product_filter_handles_compound_terms_without_catalog_fallback() -> None:
    fridge = Product(name="객실 냉장고", brand="LG", category="냉장고", price=1, product_url="x")
    washer = Product(name="상업용 세탁기", brand="LG", category="세탁기", price=1, product_url="x")
    products = [fridge, washer]
    assert _relevant_products(
        products, IntakeFields(inquiry="냉장고와 세탁기가 필요합니다")
    ) == products
    assert _relevant_products(products, IntakeFields(inquiry="에어컨 문의")) == []


@pytest.mark.asyncio
async def test_follow_up_requires_sent_reviewed_draft_and_invalid_llm_is_502(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = staff()
    lead = Lead(name="가상호텔", raw_data={}, lead_score=70, lead_score_reasoning={})
    session.add_all([manager, lead])
    await session.flush()
    previous = OutboundDraft(
        lead_id=lead.id,
        sequence_step=1,
        subject="이전",
        body="본문",
        reviewed_by=manager.id,
        send_mode="dry_run",
    )
    session.add(previous)
    await session.commit()
    with pytest.raises(HTTPException) as unsent:
        await generate_draft(lead.id, session, manager)
    assert unsent.value.status_code == 409

    previous.sent_at = datetime.now(timezone.utc)
    await session.commit()

    class InvalidLLM:
        async def structured(self, _prompt: str, result_type: type) -> object:
            return result_type(subject="x" * 301, body="본문")

    monkeypatch.setattr("app.routes.outbound.get_llm_client", lambda: InvalidLLM())
    with pytest.raises(HTTPException) as invalid:
        await generate_draft(lead.id, session, manager)
    assert invalid.value.status_code == 502


@pytest.mark.asyncio
async def test_dashboard_uses_explicit_deterministic_denominators(
    session: AsyncSession,
) -> None:
    manager, rep = staff(), staff("rep")
    account = Account(name="고객", phone="01033334444", attributes={})
    session.add_all([manager, rep, account])
    await session.flush()
    inquiries = [
        Inquiry(account_id=account.id, channel="web", content=f"문의 {index}") for index in range(2)
    ]
    session.add_all(inquiries)
    await session.flush()
    won = Opportunity(
        account_id=account.id,
        inquiry_id=inquiries[0].id,
        assignee_id=rep.id,
        title="수주",
        amount=Decimal(1000),
        probability=100,
        stage="won",
    )
    lost = Opportunity(
        account_id=account.id,
        inquiry_id=inquiries[1].id,
        assignee_id=rep.id,
        title="실주",
        amount=Decimal(500),
        probability=0,
        stage="lost",
        loss_reason="예산",
    )
    open_opportunity = Opportunity(
        account_id=account.id,
        assignee_id=rep.id,
        title="제안",
        amount=Decimal(2000),
        probability=5,
        stage="propose",
    )
    session.add_all([won, lost, open_opportunity])
    await session.flush()
    start = datetime.now(timezone.utc) - timedelta(hours=10)
    session.add_all(
        [
            OpportunityStageHistory(
                opportunity_id=won.id, stage="qualify", changed_by=manager.id, changed_at=start
            ),
            OpportunityStageHistory(
                opportunity_id=won.id,
                stage="won",
                changed_by=manager.id,
                changed_at=start + timedelta(hours=4),
            ),
            Task(
                account_id=account.id,
                assignee_id=rep.id,
                title="기한 초과",
                due_at=datetime.now(timezone.utc) - timedelta(days=1),
            ),
            Interaction(account_id=account.id, staff_id=rep.id, type="call"),
            Score(
                inquiry_id=inquiries[0].id,
                fit_score=80,
                intent_score=80,
                intent_category="구매임박",
                intent_confidence=0.9,
                recency_score=60,
                total_score=80,
                reasoning={},
                llm_provider="test",
                model_name="test",
            ),
            Score(
                inquiry_id=inquiries[1].id,
                fit_score=50,
                intent_score=50,
                intent_category="정보탐색",
                intent_confidence=0.8,
                recency_score=60,
                total_score=50,
                reasoning={},
                llm_provider="test",
                model_name="test",
            ),
        ]
    )
    await session.commit()
    result = await dashboard(session, manager)
    assert result["weighted_amount"] == 1100.0
    assert result["closed_conversion"] == {
        "won": 1,
        "lost": 1,
        "denominator": 2,
        "rate": 0.5,
        "definition": "won / (won + lost)",
    }
    assert result["tasks"] == {"open": 1, "overdue": 1}
    assert result["average_stage_hours"]["qualify"] == 4.0
    assert result["average_stage_hours"]["propose"] is not None
    assert result["rep_stats"][0]["activity_count"] == 1
    assert (
        next(item for item in result["ai_score_buckets"] if item["range"] == "80-100")[
            "won_conversion"
        ]
        == 1.0
    )


def test_localdata_fixture_parser_uses_confirmed_mapping_without_filtering() -> None:
    assert SERVICE_ID == "03_11_03_P"
    row = {
        "bplcNm": "가상호텔",
        "rdnWhlAddr": "서울 도로명",
        "siteWhlAddr": "서울 지번",
        "apvPermYmd": "20240102",
        "uptaeNm": "관광호텔",
        "trdStateNm": "영업/정상",
        "trdStateGbn": "01",
        "dtlStateNm": "정상",
        "dtlStateGbn": "01",
        "mgtNo": "M-1",
    }
    parsed = parse_localdata_rows({"result": {"body": {"rows": [row]}}})
    assert parsed[0]["address"] == "서울 도로명"
    assert parsed[0]["license_date"].isoformat() == "2024-01-02"
    assert parsed[0]["status_name"] == "영업/정상"
    assert parsed[0]["detailed_status_name"] == "정상"
    assert parsed[0]["management_number"] == "M-1"
    assert parsed[0]["raw_data"] == row
    assert parse_localdata_rows({"result": {"body": {"rows": {"@class": "list"}}}}) == []


def test_localdata_parser_rejects_undocumented_shapes() -> None:
    with pytest.raises(TypeError):
        parse_localdata_rows({"result": {"body": {"rows": {"unexpected": True}}}})


def test_localdata_parser_rejects_process_errors_and_bad_dates() -> None:
    with pytest.raises(ValueError, match="인증 실패"):
        parse_localdata_rows(
            {
                "result": {
                    "header": {"process": {"code": "ERROR", "message": "인증 실패"}},
                    "body": {"rows": []},
                }
            }
        )
    with pytest.raises(ValueError, match="잘못된 인허가일자"):
        parse_localdata_rows(
            {"result": {"body": {"rows": [{"bplcNm": "가상호텔", "apvPermYmd": "bad"}]}}}
        )


def test_localdata_address_uses_trimmed_lot_address_fallback() -> None:
    parsed = parse_localdata_rows(
        {
            "result": {
                "body": {
                    "rows": [
                        {
                            "bplcNm": "가상호텔",
                            "rdnWhlAddr": "   ",
                            "siteWhlAddr": "  서울 지번  ",
                        }
                    ]
                }
            }
        }
    )
    assert parsed[0]["address"] == "서울 지번"
