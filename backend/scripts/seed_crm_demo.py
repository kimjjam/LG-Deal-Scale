import asyncio
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select

from app.database import SessionLocal, engine
from app.models import (
    Account,
    Assignment,
    Inquiry,
    Interaction,
    Opportunity,
    OpportunityStageHistory,
    Score,
    Staff,
    Task,
)
from app.scoring import INTENT_POINTS, calculate_fit, calculate_total

MANAGER_EMAIL = "kimsungsu@test.com"
SEOUL = timezone(timedelta(hours=9))
STAGES = ("qualify", "develop", "propose")

DEALS = (
    {
        "account": "가상 한강호텔",
        "phone": "01088001001",
        "location": "서울특별시 용산구",
        "rooms": 80,
        "inquiry": "객실 TV 80대를 이번 달 안에 교체하려고 합니다. 계약 조건을 확인해주세요.",
        "intent": "구매임박",
        "title": "객실 TV 80대 교체",
        "stage": "won",
        "amount": 64_000_000,
        "assignee": "rep1@example.com",
        "stage_ages": (45, 34, 18, 5),
    },
    {
        "account": "가상 서울비즈니스호텔",
        "phone": "01088001002",
        "location": "서울특별시 강서구",
        "rooms": 55,
        "inquiry": "리뉴얼 예산이 승인돼 시스템 에어컨 32대를 발주하려고 합니다.",
        "intent": "구매임박",
        "title": "시스템 에어컨 32대 도입",
        "stage": "won",
        "amount": 36_000_000,
        "assignee": "rep2@example.com",
        "stage_ages": (38, 29, 15, 3),
    },
    {
        "account": "가상 성수스테이",
        "phone": "01088001003",
        "location": "서울특별시 성동구",
        "rooms": 32,
        "inquiry": "객실 냉장고 32대 견적과 납기를 이번 주에 받고 싶습니다.",
        "intent": "구매임박",
        "title": "객실 냉장고 32대 교체",
        "stage": "propose",
        "amount": 58_000_000,
        "assignee": "rep3@example.com",
        "stage_ages": (24, 16, 7),
        "close_days": 14,
        "task_days": 0,
    },
    {
        "account": "가상 북촌게스트하우스",
        "phone": "01088001004",
        "location": "서울특별시 종로구",
        "rooms": 14,
        "inquiry": "공용 세탁실에 세탁기와 건조기를 각 3대씩 바로 주문하고 싶어요.",
        "intent": "구매임박",
        "title": "공용 세탁실 세탁기·건조기 구축",
        "stage": "propose",
        "amount": 45_000_000,
        "assignee": "rep1@example.com",
        "stage_ages": (18, 11, 4),
        "close_days": 30,
        "task_days": -2,
    },
    {
        "account": "가상 마포시티호텔",
        "phone": "01088001005",
        "location": "서울특별시 마포구",
        "rooms": 70,
        "inquiry": "로비와 객실 TV 리뉴얼 비용을 알아보는 중입니다. 모델별 차이가 궁금합니다.",
        "intent": "정보탐색",
        "title": "로비·객실 TV 리뉴얼",
        "stage": "develop",
        "amount": 32_000_000,
        "assignee": "rep2@example.com",
        "stage_ages": (14, 8),
        "close_days": 45,
        "task_days": 2,
    },
    {
        "account": "가상 을지로모텔",
        "phone": "01088001006",
        "location": "서울특별시 중구",
        "rooms": 28,
        "inquiry": "객실용 냉장고 28대를 교체할 경우의 대략적인 비용을 비교하고 싶습니다.",
        "intent": "정보탐색",
        "title": "객실 냉장고 28대 교체",
        "stage": "develop",
        "amount": 24_000_000,
        "assignee": "rep3@example.com",
        "stage_ages": (10, 5),
        "close_days": 60,
        "task_days": 5,
    },
    {
        "account": "가상 연남펜션",
        "phone": "01088001007",
        "location": "서울특별시 마포구",
        "rooms": 12,
        "inquiry": "객실 12개의 냉난방 패키지는 어떤 구성이 좋은지 먼저 알아보고 있어요.",
        "intent": "정보탐색",
        "title": "펜션 냉난방 패키지 검토",
        "stage": "qualify",
        "amount": 18_000_000,
        "assignee": "rep1@example.com",
        "stage_ages": (6,),
        "close_days": 75,
    },
    {
        "account": "가상 강남모텔",
        "phone": "01088001008",
        "location": "서울특별시 강남구",
        "rooms": 18,
        "inquiry": "기존 세탁기 수리 비용이 너무 많이 나와 교체도 가능한지 문의합니다.",
        "intent": "AS·불만",
        "title": "객실 세탁기 18대 교체",
        "stage": "lost",
        "amount": 22_000_000,
        "assignee": "rep2@example.com",
        "stage_ages": (30, 21, 10, 2),
        "loss_reason": "교체 예산 보류",
    },
    {
        "account": "가상 동대문호텔",
        "phone": "01088001009",
        "location": "서울특별시 동대문구",
        "rooms": 45,
        "inquiry": "객실 TV 45대 교체를 위한 견적을 최대한 빨리 받고 싶습니다.",
        "intent": "구매임박",
        "title": "객실 TV 45대 긴급 견적",
        "stage": "qualify",
        "amount": 12_000_000,
        "assignee": "rep3@example.com",
        "stage_ages": (3,),
        "close_days": 20,
        "task_days": 0,
    },
)


async def main() -> None:
    now = datetime.now(timezone.utc)
    today = now.astimezone(SEOUL).date()
    titles = {deal["title"] for deal in DEALS}
    async with SessionLocal() as session, session.begin():
        manager = await session.scalar(
            select(Staff).where(
                Staff.email == MANAGER_EMAIL,
                Staff.role == "manager",
                Staff.is_active.is_(True),
            )
        )
        if not manager:
            raise RuntimeError(f"active presentation manager not found: {MANAGER_EMAIL}")

        rep_emails = {deal["assignee"] for deal in DEALS}
        reps = {
            rep.email: rep
            for rep in (
                await session.scalars(
                    select(Staff).where(
                        Staff.email.in_(rep_emails),
                        Staff.role == "rep",
                        Staff.is_active.is_(True),
                    )
                )
            ).all()
        }
        missing_reps = rep_emails - reps.keys()
        if missing_reps:
            raise RuntimeError(f"active presentation reps not found: {sorted(missing_reps)}")

        for deal in DEALS:
            account = await session.scalar(select(Account).where(Account.phone == deal["phone"]))
            if account and account.name != deal["account"]:
                raise RuntimeError(f"demo phone already belongs to another account: {deal['phone']}")
            if not account:
                created_at = now - timedelta(days=deal["stage_ages"][0])
                account = Account(
                    name=deal["account"],
                    phone=deal["phone"],
                    attributes={
                        "business_type": "숙박업",
                        "room_count": deal["rooms"],
                        "renovation_status": "진행중",
                        "location": deal["location"],
                    },
                    created_at=created_at,
                )
                session.add(account)
                await session.flush()

            inquiry = await session.scalar(
                select(Inquiry).where(
                    Inquiry.account_id == account.id,
                    Inquiry.channel == "crm-demo",
                )
            )
            if not inquiry:
                inquiry = Inquiry(
                    account_id=account.id,
                    channel="crm-demo",
                    content=deal["inquiry"],
                    raw_conversation=None,
                    routing_manager_id=manager.id,
                    status="routed",
                    created_at=account.created_at,
                )
                session.add(inquiry)
                await session.flush()

            if not await session.scalar(select(Score.id).where(Score.inquiry_id == inquiry.id)):
                fit, fit_reason = calculate_fit(account.attributes)
                intent = INTENT_POINTS[deal["intent"]]
                session.add(
                    Score(
                        inquiry_id=inquiry.id,
                        fit_score=fit,
                        intent_score=intent,
                        intent_category=deal["intent"],
                        intent_confidence=0.92,
                        recency_score=20,
                        total_score=calculate_total(fit, intent, 20),
                        reasoning={
                            "fit": fit_reason,
                            "intent": "발표용 고정 시드의 문의 내용과 구매 의도가 일치합니다.",
                            "recency": "이번 문의 이전의 활동 기록이 없는 신규 고객입니다.",
                        },
                        scoring_version="v1-demo",
                        llm_provider="seed",
                        model_name="fixed-json",
                        created_at=inquiry.created_at,
                        updated_at=inquiry.created_at,
                    )
                )

            rep = reps[deal["assignee"]]
            if not await session.scalar(
                select(Assignment.id).where(Assignment.inquiry_id == inquiry.id)
            ):
                session.add(
                    Assignment(
                        inquiry_id=inquiry.id,
                        assignee_id=rep.id,
                        assigned_at=inquiry.created_at,
                        method="round_robin",
                    )
                )

            if await session.scalar(
                select(Opportunity.id).where(Opportunity.inquiry_id == inquiry.id)
            ):
                continue

            probability = {"qualify": 10, "develop": 30, "propose": 60, "won": 100, "lost": 0}[
                deal["stage"]
            ]
            opportunity = Opportunity(
                account_id=account.id,
                inquiry_id=inquiry.id,
                assignee_id=rep.id,
                title=deal["title"],
                amount=Decimal(deal["amount"]),
                probability=probability,
                expected_close_date=(
                    today + timedelta(days=deal["close_days"])
                    if "close_days" in deal
                    else None
                ),
                stage=deal["stage"],
                loss_reason=deal.get("loss_reason"),
                created_at=account.created_at,
                updated_at=now,
            )
            session.add(opportunity)
            await session.flush()

            history_stages = (
                (*STAGES, deal["stage"])
                if deal["stage"] in {"won", "lost"}
                else STAGES[: STAGES.index(deal["stage"]) + 1]
            )
            if len(history_stages) != len(deal["stage_ages"]):
                raise RuntimeError(f"invalid stage history: {deal['title']}")
            session.add_all(
                [
                    OpportunityStageHistory(
                        opportunity_id=opportunity.id,
                        stage=stage,
                        changed_by=manager.id,
                        changed_at=now - timedelta(days=age),
                    )
                    for stage, age in zip(history_stages, deal["stage_ages"], strict=True)
                ]
            )

            activity_ages = (
                max(1, deal["stage_ages"][0] - 2),
                max(0, deal["stage_ages"][-1] - 1),
            )
            session.add_all(
                [
                    Interaction(
                        account_id=account.id,
                        staff_id=rep.id,
                        inquiry_id=inquiry.id,
                        opportunity_id=opportunity.id,
                        type=activity_type,
                        content=content,
                        outcome=outcome,
                        created_at=now - timedelta(days=age),
                    )
                    for activity_type, content, outcome, age in (
                        (
                            "call",
                            "요구사항과 예산 범위를 확인했습니다.",
                            "후속 제안 진행",
                            activity_ages[0],
                        ),
                        (
                            "email",
                            "제품 구성과 다음 일정을 안내했습니다.",
                            "고객 검토 중",
                            activity_ages[1],
                        ),
                    )
                ]
            )
            if deal["stage"] == "won":
                session.add(
                    Interaction(
                        account_id=account.id,
                        staff_id=rep.id,
                        inquiry_id=inquiry.id,
                        opportunity_id=opportunity.id,
                        type="purchase",
                        content="계약 확정 내용을 기록했습니다.",
                        outcome="수주",
                        amount=Decimal(deal["amount"]),
                        created_at=now - timedelta(days=deal["stage_ages"][-1]),
                    )
                )
            if "task_days" in deal:
                due_at = datetime.combine(
                    today + timedelta(days=deal["task_days"]), time(17), SEOUL
                ).astimezone(timezone.utc)
                session.add(
                    Task(
                        account_id=account.id,
                        opportunity_id=opportunity.id,
                        inquiry_id=inquiry.id,
                        assignee_id=rep.id,
                        title=f"{deal['title']} 후속 연락",
                        due_at=due_at,
                        status="pending",
                        created_at=inquiry.created_at,
                    )
                )

        created = await session.scalar(
            select(func.count()).select_from(Opportunity).where(Opportunity.title.in_(titles))
        )
        if created != len(DEALS):
            raise RuntimeError(f"expected {len(DEALS)} demo opportunities, found {created}")

    print(f"crm_demo_opportunities={len(DEALS)}")


async def run() -> None:
    try:
        await main()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())
