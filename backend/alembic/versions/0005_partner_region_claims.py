"""Add partner, regional routing, and claimed assignments.

Revision ID: 0005
Revises: 0004
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("inquiries") as batch_op:
        batch_op.add_column(sa.Column("routing_manager_id", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            "fk_inquiries_routing_manager_id_staff",
            "staff",
            ["routing_manager_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(
            "ix_inquiries_routing_manager_id", ["routing_manager_id"]
        )
    with op.batch_alter_table("assignments") as batch_op:
        batch_op.drop_constraint("ck_assignment_method", type_="check")
        batch_op.create_check_constraint(
            "ck_assignment_method",
            "method IN ('round_robin', 'manual', 'claimed')",
        )
    op.create_table(
        "sales_regions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("region_name", sa.String(100), nullable=False),
        sa.Column("match_keyword", sa.String(100), nullable=False),
        sa.Column("manager_id", sa.Uuid(), sa.ForeignKey("staff.id"), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_sales_regions_match_keyword", "sales_regions", ["match_keyword"], unique=True)
    op.create_index("ix_sales_regions_manager_id", "sales_regions", ["manager_id"])
    op.create_index("ix_sales_regions_is_active", "sales_regions", ["is_active"])
    op.create_table(
        "partners",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("address", sa.String(500), nullable=False),
        sa.Column("phone", sa.String(30)),
        sa.Column("region", sa.String(100), nullable=False),
        sa.Column("partner_type", sa.String(50), nullable=False),
        sa.Column("verification_source", sa.String(200), nullable=False),
        sa.Column("verified_at", sa.Date(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_partners_region", "partners", ["region"])
    op.create_index("ix_partners_is_active", "partners", ["is_active"])


def downgrade() -> None:
    op.drop_table("partners")
    op.drop_table("sales_regions")
    op.execute("UPDATE assignments SET method = 'manual' WHERE method = 'claimed'")
    with op.batch_alter_table("assignments") as batch_op:
        batch_op.drop_constraint("ck_assignment_method", type_="check")
        batch_op.create_check_constraint(
            "ck_assignment_method", "method IN ('round_robin', 'manual')"
        )
    with op.batch_alter_table("inquiries") as batch_op:
        batch_op.drop_index("ix_inquiries_routing_manager_id")
        batch_op.drop_constraint(
            "fk_inquiries_routing_manager_id_staff", type_="foreignkey"
        )
        batch_op.drop_column("routing_manager_id")
