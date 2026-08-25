"""Allow multiple managers per sales region.

Revision ID: 0010
Revises: 0009
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("sales_regions") as batch_op:
        batch_op.drop_index("ix_sales_regions_match_keyword")
        batch_op.create_index("ix_sales_regions_match_keyword", ["match_keyword"], unique=False)
        batch_op.create_unique_constraint(
            "uq_sales_regions_keyword_manager", ["match_keyword", "manager_id"]
        )


def downgrade() -> None:
    op.execute(
        "DELETE FROM sales_regions WHERE id NOT IN "
        "(SELECT MIN(id) FROM sales_regions GROUP BY match_keyword)"
    )
    with op.batch_alter_table("sales_regions") as batch_op:
        batch_op.drop_constraint("uq_sales_regions_keyword_manager", type_="unique")
        batch_op.drop_index("ix_sales_regions_match_keyword")
        batch_op.create_index("ix_sales_regions_match_keyword", ["match_keyword"], unique=True)
