"""Link inquiries to curated partners.

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("inquiries") as batch_op:
        batch_op.add_column(sa.Column("partner_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_inquiries_partner_id_partners",
            "partners",
            ["partner_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index("ix_inquiries_partner_id", ["partner_id"])


def downgrade() -> None:
    with op.batch_alter_table("inquiries") as batch_op:
        batch_op.drop_index("ix_inquiries_partner_id")
        batch_op.drop_constraint("fk_inquiries_partner_id_partners", type_="foreignkey")
        batch_op.drop_column("partner_id")
