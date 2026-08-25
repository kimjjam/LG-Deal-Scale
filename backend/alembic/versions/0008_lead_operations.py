"""Add lead ownership and next actions.

Revision ID: 0008
Revises: 0007
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("leads") as batch_op:
        batch_op.add_column(sa.Column("assignee_id", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("contact_name", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("contact_phone", sa.String(length=30), nullable=True))
        batch_op.add_column(sa.Column("contact_email", sa.String(length=320), nullable=True))
        batch_op.add_column(sa.Column("next_action_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_foreign_key(
            "fk_leads_assignee_id_staff", "staff", ["assignee_id"], ["id"], ondelete="SET NULL"
        )
        batch_op.create_index("ix_leads_assignee_id", ["assignee_id"])
        batch_op.create_index("ix_leads_next_action_at", ["next_action_at"])
    op.execute(
        "UPDATE leads SET next_action_at = created_at "
        "WHERE pipeline_stage = 'follow_up_due' AND next_action_at IS NULL"
    )


def downgrade() -> None:
    with op.batch_alter_table("leads") as batch_op:
        batch_op.drop_index("ix_leads_next_action_at")
        batch_op.drop_index("ix_leads_assignee_id")
        batch_op.drop_constraint("fk_leads_assignee_id_staff", type_="foreignkey")
        batch_op.drop_column("next_action_at")
        batch_op.drop_column("contact_email")
        batch_op.drop_column("contact_phone")
        batch_op.drop_column("contact_name")
        batch_op.drop_column("assignee_id")
