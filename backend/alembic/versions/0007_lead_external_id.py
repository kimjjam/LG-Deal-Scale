"""Add public-data identity to leads.

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("leads") as batch_op:
        batch_op.add_column(sa.Column("external_id", sa.String(length=50), nullable=True))
        batch_op.create_unique_constraint("uq_leads_external_id", ["external_id"])


def downgrade() -> None:
    with op.batch_alter_table("leads") as batch_op:
        batch_op.drop_constraint("uq_leads_external_id", type_="unique")
        batch_op.drop_column("external_id")
