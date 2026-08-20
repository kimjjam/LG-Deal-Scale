from datetime import datetime, timezone
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_session
from app.llm import get_llm_client
from app.models import Lead, OutboundDraft, Product
from app.prompts import outbound_prompt
from app.schemas import LeadStageRequest
from app.security import CurrentStaff

router = APIRouter(prefix="/api/outbound", tags=["outbound"])
Session = Annotated[AsyncSession, Depends(get_session)]


class DraftResult(BaseModel):
    subject: str
    body: str


def draft_payload(draft: OutboundDraft) -> dict[str, object]:
    return {
        "id": draft.id,
        "lead_id": draft.lead_id,
        "sequence_step": draft.sequence_step,
        "subject": draft.subject,
        "body": draft.body,
        "generated_at": draft.generated_at,
        "reviewed": draft.reviewed_by is not None,
        "send_mode": draft.send_mode,
        "sent_at": draft.sent_at,
    }


@router.get("/leads")
async def list_leads(session: Session, _staff: CurrentStaff) -> list[dict[str, object]]:
    leads = list((await session.scalars(select(Lead).order_by(Lead.lead_score.desc()))).all())
    return [
        {
            "id": lead.id,
            "name": lead.name,
            "address": lead.address,
            "business_type": lead.business_type,
            "lead_score": lead.lead_score,
            "reasoning": lead.lead_score_reasoning,
            "pipeline_stage": lead.pipeline_stage,
        }
        for lead in leads
    ]


@router.get("/leads/{lead_id}/drafts")
async def list_drafts(
    lead_id: int, session: Session, _staff: CurrentStaff
) -> list[dict[str, object]]:
    if not await session.get(Lead, lead_id):
        raise HTTPException(status_code=404, detail="리드를 찾을 수 없습니다.")
    drafts = (
        await session.scalars(
            select(OutboundDraft)
            .where(OutboundDraft.lead_id == lead_id)
            .order_by(OutboundDraft.sequence_step.desc())
        )
    ).all()
    return [draft_payload(draft) for draft in drafts]


@router.put("/leads/{lead_id}/stage")
async def change_stage(
    lead_id: int, payload: LeadStageRequest, session: Session, _staff: CurrentStaff
) -> dict[str, object]:
    lead = await session.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="리드를 찾을 수 없습니다.")
    lead.pipeline_stage = payload.pipeline_stage
    await session.commit()
    return {"id": lead.id, "pipeline_stage": lead.pipeline_stage}


@router.post("/leads/{lead_id}/drafts")
async def generate_draft(
    lead_id: int, session: Session, _staff: CurrentStaff
) -> dict[str, object]:
    lead = await session.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="리드를 찾을 수 없습니다.")
    previous = await session.scalar(
        select(OutboundDraft)
        .where(OutboundDraft.lead_id == lead_id)
        .order_by(OutboundDraft.sequence_step.desc())
        .limit(1)
    )
    sequence_step = 1 if not previous else previous.sequence_step + 1
    if sequence_step > 3:
        raise HTTPException(status_code=409, detail="시퀀스는 최대 3단계입니다.")
    products = list((await session.scalars(select(Product).where(Product.brand == "LG").limit(5))).all())
    result = await get_llm_client().structured(
        outbound_prompt(
            {
                "name": lead.name,
                "business_type": lead.business_type,
                "years_in_business": lead.years_in_business,
                "lead_score_reasoning": lead.lead_score_reasoning,
            },
            [
                {"name": product.name, "category": product.category, "price": str(product.price)}
                for product in products
            ],
            sequence_step,
        ),
        DraftResult,
    )
    draft = OutboundDraft(
        lead_id=lead.id,
        sequence_step=sequence_step,
        previous_draft_id=previous.id if previous else None,
        subject=result.subject,
        body=result.body,
        send_mode=get_settings().outbound_email_mode,
    )
    lead.pipeline_stage = "draft_generated"
    session.add(draft)
    await session.commit()
    await session.refresh(draft)
    return draft_payload(draft)


@router.post("/drafts/{draft_id}/review")
async def review_draft(
    draft_id: int, session: Session, staff: CurrentStaff
) -> dict[str, object]:
    draft = await session.get(OutboundDraft, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="초안을 찾을 수 없습니다.")
    draft.reviewed_by = staff.id
    lead = await session.get(Lead, draft.lead_id)
    if lead:
        lead.pipeline_stage = "approved"
    await session.commit()
    return {"id": draft.id, "reviewed": True}


@router.post("/drafts/{draft_id}/send")
async def safe_send(draft_id: int, session: Session, _staff: CurrentStaff) -> dict[str, object]:
    draft = await session.get(OutboundDraft, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="초안을 찾을 수 없습니다.")
    if not draft.reviewed_by:
        raise HTTPException(status_code=409, detail="담당자 검토 후에만 발송할 수 있습니다.")
    settings = get_settings()
    if settings.outbound_email_mode == "test_override":
        if not all((settings.test_email_address, settings.email_provider_api_key)):
            raise HTTPException(status_code=503, detail="테스트 발송 설정이 필요합니다.")
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {settings.email_provider_api_key}"},
                json={
                    "from": "DirectDesk Test <onboarding@resend.dev>",
                    "to": [settings.test_email_address],
                    "subject": draft.subject,
                    "text": draft.body,
                },
            )
            response.raise_for_status()
    draft.sent_at = datetime.now(timezone.utc)
    draft.send_mode = settings.outbound_email_mode
    lead = await session.get(Lead, draft.lead_id)
    if lead:
        lead.pipeline_stage = "contacted"
    await session.commit()
    return {"id": draft.id, "mode": draft.send_mode, "sent_at": draft.sent_at}


@router.get("/dashboard")
async def dashboard(session: Session, _staff: CurrentStaff) -> dict[str, object]:
    stages = dict(
        (await session.execute(select(Lead.pipeline_stage, func.count()).group_by(Lead.pipeline_stage))).all()
    )
    draft_counts = (
        await session.execute(
            select(
                func.count(OutboundDraft.id),
                func.sum(case((OutboundDraft.reviewed_by.is_not(None), 1), else_=0)),
            )
        )
    ).one()
    sequence = dict(
        (
            await session.execute(
                select(OutboundDraft.sequence_step, func.count()).group_by(OutboundDraft.sequence_step)
            )
        ).all()
    )
    total, reviewed = int(draft_counts[0] or 0), int(draft_counts[1] or 0)
    return {
        "pipeline": stages,
        "draft_approval_rate": round(reviewed / total, 3) if total else 0,
        "sequence_distribution": sequence,
        "outbound_email_mode": get_settings().outbound_email_mode,
    }
