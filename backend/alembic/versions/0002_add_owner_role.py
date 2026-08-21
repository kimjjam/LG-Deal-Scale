"""Allow the owner staff role.

Revision ID: 0002
Revises: 0001
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_staff_role", "staff", type_="check")
    op.create_check_constraint(
        "ck_staff_role", "staff", "role IN ('owner', 'manager', 'rep')"
    )


def downgrade() -> None:
    op.execute("UPDATE staff SET role = 'manager' WHERE role = 'owner'")
    op.drop_constraint("ck_staff_role", "staff", type_="check")
    op.create_check_constraint("ck_staff_role", "staff", "role IN ('manager', 'rep')")
