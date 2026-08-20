"""Create DirectDesk schema.

Revision ID: 0001
Revises:
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("phone", sa.String(30), nullable=False),
        sa.Column("attributes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("phone"),
    )
    op.create_index("ix_accounts_phone", "accounts", ["phone"], unique=True)
    op.create_table(
        "staff",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.CheckConstraint("role IN ('manager', 'rep')", name="ck_staff_role"),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_staff_email", "staff", ["email"], unique=True)
    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("brand", sa.String(100), nullable=False),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("price", sa.Numeric(14, 2), nullable=False),
        sa.Column("product_url", sa.String(1000), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_products_brand", "products", ["brand"])
    op.create_index("ix_products_category", "products", ["category"])
    op.create_table(
        "leads",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("address", sa.String(500)),
        sa.Column("license_date", sa.Date()),
        sa.Column("years_in_business", sa.Integer()),
        sa.Column("business_type", sa.String(100)),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("raw_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("lead_score", sa.Integer(), nullable=False),
        sa.Column("lead_score_reasoning", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("lead_scoring_version", sa.String(20), nullable=False),
        sa.Column("pipeline_stage", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "pipeline_stage IN ('discovered', 'draft_generated', 'approved', 'contacted', "
            "'follow_up_due', 'converted', 'dropped')",
            name="ck_lead_pipeline_stage",
        ),
    )
    op.create_index("ix_leads_pipeline_stage", "leads", ["pipeline_stage"])
    op.create_table(
        "contacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("role", sa.String(100)),
        sa.Column("phone", sa.String(30)),
        sa.Column("email", sa.String(320)),
    )
    op.create_table(
        "inquiries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel", sa.String(30), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("raw_conversation", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('open', 'routed', 'resolved')", name="ck_inquiry_status"),
    )
    op.create_index("ix_inquiries_account_id", "inquiries", ["account_id"])
    op.create_index("ix_inquiries_created_at", "inquiries", ["created_at"])
    op.create_index("ix_inquiries_status", "inquiries", ["status"])
    op.create_table(
        "interactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", sa.String(30), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_interactions_account_id", "interactions", ["account_id"])
    op.create_index("ix_interactions_created_at", "interactions", ["created_at"])
    op.create_table(
        "query_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("staff_id", sa.Uuid(), sa.ForeignKey("staff.id"), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("generated_sql", sa.Text(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_query_logs_staff_id", "query_logs", ["staff_id"])
    op.create_table(
        "outbound_drafts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("lead_id", sa.Integer(), sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence_step", sa.Integer(), nullable=False),
        sa.Column("previous_draft_id", sa.Integer(), sa.ForeignKey("outbound_drafts.id")),
        sa.Column("subject", sa.String(300), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_by", sa.Uuid(), sa.ForeignKey("staff.id")),
        sa.Column("send_mode", sa.String(20), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("sequence_step BETWEEN 1 AND 3", name="ck_draft_sequence_step"),
        sa.CheckConstraint("send_mode IN ('dry_run', 'test_override')", name="ck_draft_send_mode"),
        sa.UniqueConstraint("lead_id", "sequence_step", name="uq_draft_lead_sequence"),
    )
    op.create_index("ix_outbound_drafts_lead_id", "outbound_drafts", ["lead_id"])
    op.create_table(
        "scores",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("inquiry_id", sa.Integer(), sa.ForeignKey("inquiries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("fit_score", sa.Integer(), nullable=False),
        sa.Column("intent_score", sa.Integer(), nullable=False),
        sa.Column("intent_category", sa.String(30), nullable=False),
        sa.Column("intent_confidence", sa.Float(), nullable=False),
        sa.Column("recency_score", sa.Integer(), nullable=False),
        sa.Column("total_score", sa.Float(), nullable=False),
        sa.Column("reasoning", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("scoring_version", sa.String(20), nullable=False),
        sa.Column("llm_provider", sa.String(30), nullable=False),
        sa.Column("model_name", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("inquiry_id"),
    )
    op.create_index("ix_scores_inquiry_id", "scores", ["inquiry_id"], unique=True)
    op.create_index("ix_scores_total_score", "scores", ["total_score"])
    op.create_table(
        "assignments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("inquiry_id", sa.Integer(), sa.ForeignKey("inquiries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("assignee_id", sa.Uuid(), sa.ForeignKey("staff.id"), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("method", sa.String(20), nullable=False),
        sa.CheckConstraint("method IN ('round_robin', 'manual')", name="ck_assignment_method"),
    )
    op.create_index("ix_assignments_assigned_at", "assignments", ["assigned_at"])
    op.create_index("ix_assignments_assignee_id", "assignments", ["assignee_id"])
    op.create_index("ix_assignments_inquiry_id", "assignments", ["inquiry_id"])


def downgrade() -> None:
    for table in (
        "assignments",
        "scores",
        "outbound_drafts",
        "query_logs",
        "interactions",
        "inquiries",
        "contacts",
        "leads",
        "products",
        "staff",
        "accounts",
    ):
        op.drop_table(table)

