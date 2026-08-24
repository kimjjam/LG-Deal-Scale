import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

JsonType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    attributes: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    role: Mapped[str | None] = mapped_column(String(100))
    phone: Mapped[str | None] = mapped_column(String(30))
    email: Mapped[str | None] = mapped_column(String(320))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class Inquiry(Base):
    __tablename__ = "inquiries"
    __table_args__ = (
        CheckConstraint("status IN ('open', 'routed', 'resolved')", name="ck_inquiry_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    channel: Mapped[str] = mapped_column(String(30), default="web")
    content: Mapped[str] = mapped_column(Text)
    raw_conversation: Mapped[list[dict[str, Any]] | None] = mapped_column(JsonType)
    routing_manager_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("staff.id", ondelete="SET NULL"), index=True
    )
    partner_id: Mapped[int | None] = mapped_column(
        ForeignKey("partners.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class Interaction(Base):
    __tablename__ = "interactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    staff_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("staff.id"), index=True)
    contact_id: Mapped[int | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL"), index=True
    )
    inquiry_id: Mapped[int | None] = mapped_column(
        ForeignKey("inquiries.id", ondelete="SET NULL"), index=True
    )
    opportunity_id: Mapped[int | None] = mapped_column(
        ForeignKey("opportunities.id", ondelete="SET NULL"), index=True
    )
    type: Mapped[str] = mapped_column(String(30))
    content: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str | None] = mapped_column(String(200))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class Score(Base):
    __tablename__ = "scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    inquiry_id: Mapped[int] = mapped_column(
        ForeignKey("inquiries.id", ondelete="CASCADE"), unique=True, index=True
    )
    fit_score: Mapped[int] = mapped_column(Integer)
    intent_score: Mapped[int] = mapped_column(Integer)
    intent_category: Mapped[str] = mapped_column(String(30))
    intent_confidence: Mapped[float] = mapped_column(Float)
    recency_score: Mapped[int] = mapped_column(Integer)
    total_score: Mapped[float] = mapped_column(Float, index=True)
    reasoning: Mapped[dict[str, str]] = mapped_column(JsonType)
    scoring_version: Mapped[str] = mapped_column(String(20), default="v1")
    llm_provider: Mapped[str] = mapped_column(String(30))
    model_name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Staff(Base):
    __tablename__ = "staff"
    __table_args__ = (CheckConstraint("role IN ('owner', 'manager', 'rep')", name="ck_staff_role"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(default=True, index=True)


class Assignment(Base):
    __tablename__ = "assignments"
    __table_args__ = (
        CheckConstraint(
            "method IN ('round_robin', 'manual', 'claimed')", name="ck_assignment_method"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    inquiry_id: Mapped[int] = mapped_column(
        ForeignKey("inquiries.id", ondelete="CASCADE"), index=True
    )
    assignee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("staff.id"), index=True)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    method: Mapped[str] = mapped_column(String(20))


class SalesRegion(Base):
    __tablename__ = "sales_regions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    region_name: Mapped[str] = mapped_column(String(100))
    match_keyword: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    manager_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("staff.id"), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Partner(Base):
    __tablename__ = "partners"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str] = mapped_column(String(500))
    phone: Mapped[str | None] = mapped_column(String(30))
    region: Mapped[str] = mapped_column(String(100), index=True)
    partner_type: Mapped[str] = mapped_column(String(50))
    verification_source: Mapped[str] = mapped_column(String(200))
    verified_at: Mapped[date] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Opportunity(Base):
    __tablename__ = "opportunities"
    __table_args__ = (
        CheckConstraint(
            "stage IN ('qualify', 'develop', 'propose', 'won', 'lost')",
            name="ck_opportunity_stage",
        ),
        CheckConstraint("probability BETWEEN 0 AND 100", name="ck_opportunity_probability"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    inquiry_id: Mapped[int | None] = mapped_column(
        ForeignKey("inquiries.id", ondelete="SET NULL"), unique=True
    )
    lead_id: Mapped[int | None] = mapped_column(
        ForeignKey("leads.id", ondelete="SET NULL"), unique=True
    )
    assignee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("staff.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    probability: Mapped[int] = mapped_column(Integer, default=10)
    expected_close_date: Mapped[date | None] = mapped_column(Date)
    stage: Mapped[str] = mapped_column(String(20), default="qualify", index=True)
    loss_reason: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class OpportunityStageHistory(Base):
    __tablename__ = "opportunity_stage_history"
    __table_args__ = (
        CheckConstraint(
            "stage IN ('qualify', 'develop', 'propose', 'won', 'lost')",
            name="ck_opportunity_history_stage",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(
        ForeignKey("opportunities.id", ondelete="CASCADE"), index=True
    )
    stage: Mapped[str] = mapped_column(String(20))
    changed_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("staff.id"))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (CheckConstraint("status IN ('pending', 'completed')", name="ck_task_status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    opportunity_id: Mapped[int | None] = mapped_column(
        ForeignKey("opportunities.id", ondelete="CASCADE"), index=True
    )
    inquiry_id: Mapped[int | None] = mapped_column(
        ForeignKey("inquiries.id", ondelete="CASCADE"), index=True
    )
    assignee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("staff.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("staff.id", ondelete="SET NULL"), index=True
    )
    action: Mapped[str] = mapped_column(String(50), index=True)
    resource_type: Mapped[str] = mapped_column(String(50))
    resource_id: Mapped[str] = mapped_column(String(100))
    details: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class QueryLog(Base):
    __tablename__ = "query_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    staff_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("staff.id"), index=True)
    question: Mapped[str] = mapped_column(Text)
    generated_sql: Mapped[str] = mapped_column(Text)
    row_count: Mapped[int] = mapped_column(Integer)
    success: Mapped[bool] = mapped_column(default=True, index=True)
    error_category: Mapped[str | None] = mapped_column(String(50))
    error_message: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (
        CheckConstraint(
            "pipeline_stage IN ('discovered', 'draft_generated', 'approved', 'contacted', "
            "'follow_up_due', 'converted', 'dropped')",
            name="ck_lead_pipeline_stage",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    external_id: Mapped[str | None] = mapped_column(String(50), unique=True)
    address: Mapped[str | None] = mapped_column(String(500))
    license_date: Mapped[date | None] = mapped_column(Date)
    years_in_business: Mapped[int | None] = mapped_column(Integer)
    business_type: Mapped[str | None] = mapped_column(String(100))
    source: Mapped[str] = mapped_column(String(30), default="localdata")
    raw_data: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    lead_score: Mapped[int] = mapped_column(Integer)
    lead_score_reasoning: Mapped[dict[str, str]] = mapped_column(JsonType)
    lead_scoring_version: Mapped[str] = mapped_column(String(20), default="v1")
    pipeline_stage: Mapped[str] = mapped_column(String(30), default="discovered", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OutboundDraft(Base):
    __tablename__ = "outbound_drafts"
    __table_args__ = (
        CheckConstraint("sequence_step BETWEEN 1 AND 3", name="ck_draft_sequence_step"),
        CheckConstraint("send_mode IN ('dry_run', 'test_override')", name="ck_draft_send_mode"),
        UniqueConstraint("lead_id", "sequence_step", name="uq_draft_lead_sequence"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    sequence_step: Mapped[int] = mapped_column(Integer, default=1)
    previous_draft_id: Mapped[int | None] = mapped_column(ForeignKey("outbound_drafts.id"))
    subject: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("staff.id"))
    send_mode: Mapped[str] = mapped_column(String(20), default="dry_run")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (CheckConstraint("price > 0", name="ck_product_price_positive"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    brand: Mapped[str] = mapped_column(String(100), index=True)
    category: Mapped[str] = mapped_column(String(100), index=True)
    price: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    price_type: Mapped[str] = mapped_column(String(30), default="retail_reference")
    price_source_url: Mapped[str | None] = mapped_column(String(1000))
    price_verified_at: Mapped[date | None] = mapped_column(Date)
    usage_context: Mapped[str | None] = mapped_column(String(50))
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    product_url: Mapped[str] = mapped_column(String(1000))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
