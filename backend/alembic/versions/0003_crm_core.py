"""Add CRM foundation and soft deletion.

Revision ID: 0003
Revises: 0002
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("Alembic migrations require PostgreSQL.")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM accounts
                WHERE coalesce(length(regexp_replace(phone, '[^0-9]', '', 'g')), 0)
                    NOT BETWEEN 7 AND 15
            ) THEN
                RAISE EXCEPTION 'Cannot normalize account phones: normalized values must contain 7 to 15 digits';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM contacts
                WHERE phone IS NOT NULL
                  AND length(regexp_replace(phone, '[^0-9]', '', 'g'))
                    NOT BETWEEN 7 AND 15
            ) THEN
                RAISE EXCEPTION 'Cannot normalize contact phones: non-null normalized values must contain 7 to 15 digits';
            END IF;
            IF EXISTS (
                SELECT regexp_replace(phone, '[^0-9]', '', 'g')
                FROM accounts
                GROUP BY regexp_replace(phone, '[^0-9]', '', 'g')
                HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION 'Cannot normalize account phones: duplicate normalized values exist';
            END IF;
        END $$
        """
    )
    op.execute("UPDATE accounts SET phone = regexp_replace(phone, '[^0-9]', '', 'g')")
    op.execute(
        "UPDATE contacts SET phone = regexp_replace(phone, '[^0-9]', '', 'g') "
        "WHERE phone IS NOT NULL"
    )
    op.add_column(
        "query_logs",
        sa.Column("success", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.alter_column("query_logs", "success", server_default=None)
    op.add_column("query_logs", sa.Column("error_category", sa.String(50)))
    op.add_column("query_logs", sa.Column("error_message", sa.String(200)))
    op.create_index("ix_query_logs_success", "query_logs", ["success"])
    op.add_column(
        "staff",
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.alter_column("staff", "is_active", server_default=None)
    op.create_index("ix_staff_is_active", "staff", ["is_active"])

    for table in ("accounts", "contacts"):
        op.add_column(table, sa.Column("deleted_at", sa.DateTime(timezone=True)))
        op.create_index(f"ix_{table}_deleted_at", table, ["deleted_at"])

    op.create_table(
        "opportunities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "inquiry_id",
            sa.Integer(),
            sa.ForeignKey("inquiries.id", ondelete="SET NULL"),
            unique=True,
        ),
        sa.Column(
            "lead_id",
            sa.Integer(),
            sa.ForeignKey("leads.id", ondelete="SET NULL"),
            unique=True,
        ),
        sa.Column("assignee_id", sa.Uuid(), sa.ForeignKey("staff.id"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2)),
        sa.Column("probability", sa.Integer(), nullable=False),
        sa.Column("expected_close_date", sa.Date()),
        sa.Column("stage", sa.String(20), nullable=False),
        sa.Column("loss_reason", sa.String(500)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "stage IN ('qualify', 'develop', 'propose', 'won', 'lost')",
            name="ck_opportunity_stage",
        ),
        sa.CheckConstraint(
            "probability BETWEEN 0 AND 100", name="ck_opportunity_probability"
        ),
    )
    op.create_index("ix_opportunities_account_id", "opportunities", ["account_id"])
    op.create_index("ix_opportunities_assignee_id", "opportunities", ["assignee_id"])
    op.create_index("ix_opportunities_stage", "opportunities", ["stage"])

    op.create_table(
        "opportunity_stage_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "opportunity_id",
            sa.Integer(),
            sa.ForeignKey("opportunities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stage", sa.String(20), nullable=False),
        sa.Column("changed_by", sa.Uuid(), sa.ForeignKey("staff.id"), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "stage IN ('qualify', 'develop', 'propose', 'won', 'lost')",
            name="ck_opportunity_history_stage",
        ),
    )
    op.create_index(
        "ix_opportunity_stage_history_opportunity_id",
        "opportunity_stage_history",
        ["opportunity_id"],
    )

    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "opportunity_id",
            sa.Integer(),
            sa.ForeignKey("opportunities.id", ondelete="CASCADE"),
        ),
        sa.Column(
            "inquiry_id",
            sa.Integer(),
            sa.ForeignKey("inquiries.id", ondelete="CASCADE"),
        ),
        sa.Column("assignee_id", sa.Uuid(), sa.ForeignKey("staff.id"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('pending', 'completed')", name="ck_task_status"),
    )
    for column in ("account_id", "opportunity_id", "inquiry_id", "assignee_id", "due_at", "status"):
        op.create_index(f"ix_tasks_{column}", "tasks", [column])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "actor_id", sa.Uuid(), sa.ForeignKey("staff.id", ondelete="SET NULL")
        ),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("resource_type", sa.String(50), nullable=False),
        sa.Column("resource_id", sa.String(100), nullable=False),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_logs_actor_id", "audit_logs", ["actor_id"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])

    interaction_columns = (
        sa.Column("staff_id", sa.Uuid()),
        sa.Column("contact_id", sa.Integer()),
        sa.Column("inquiry_id", sa.Integer()),
        sa.Column("opportunity_id", sa.Integer()),
        sa.Column("content", sa.Text()),
        sa.Column("outcome", sa.String(200)),
    )
    for column in interaction_columns:
        op.add_column("interactions", column)
    op.create_foreign_key("fk_interactions_staff", "interactions", "staff", ["staff_id"], ["id"])
    op.create_foreign_key(
        "fk_interactions_contact",
        "interactions",
        "contacts",
        ["contact_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_interactions_inquiry",
        "interactions",
        "inquiries",
        ["inquiry_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_interactions_opportunity",
        "interactions",
        "opportunities",
        ["opportunity_id"],
        ["id"],
        ondelete="SET NULL",
    )
    for column in ("staff_id", "contact_id", "inquiry_id", "opportunity_id"):
        op.create_index(f"ix_interactions_{column}", "interactions", [column])


def downgrade() -> None:
    for column in ("opportunity_id", "inquiry_id", "contact_id", "staff_id"):
        op.drop_index(f"ix_interactions_{column}", table_name="interactions")
        op.drop_constraint(f"fk_interactions_{column.removesuffix('_id')}", "interactions", type_="foreignkey")
    for column in ("outcome", "content", "opportunity_id", "inquiry_id", "contact_id", "staff_id"):
        op.drop_column("interactions", column)

    op.drop_table("audit_logs")
    op.drop_table("tasks")
    op.drop_table("opportunity_stage_history")
    op.drop_table("opportunities")
    for table in ("contacts", "accounts"):
        op.drop_index(f"ix_{table}_deleted_at", table_name=table)
        op.drop_column(table, "deleted_at")
    op.drop_index("ix_staff_is_active", table_name="staff")
    op.drop_column("staff", "is_active")
    op.drop_index("ix_query_logs_success", table_name="query_logs")
    op.drop_column("query_logs", "error_message")
    op.drop_column("query_logs", "error_category")
    op.drop_column("query_logs", "success")
