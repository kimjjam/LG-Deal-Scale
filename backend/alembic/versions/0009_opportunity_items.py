"""Add opportunity product items.

Revision ID: 0009
Revises: 0008
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "opportunity_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "opportunity_id",
            sa.Integer(),
            sa.ForeignKey("opportunities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "product_id",
            sa.Integer(),
            sa.ForeignKey("products.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("product_name", sa.String(length=200), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.Numeric(14, 2), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_opportunity_item_quantity_positive"),
        sa.CheckConstraint("unit_price >= 0", name="ck_opportunity_item_unit_price_nonnegative"),
    )
    op.create_index("ix_opportunity_items_opportunity_id", "opportunity_items", ["opportunity_id"])
    op.create_index("ix_opportunity_items_product_id", "opportunity_items", ["product_id"])


def downgrade() -> None:
    op.drop_index("ix_opportunity_items_product_id", table_name="opportunity_items")
    op.drop_index("ix_opportunity_items_opportunity_id", table_name="opportunity_items")
    op.drop_table("opportunity_items")
