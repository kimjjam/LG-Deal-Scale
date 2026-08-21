"""Add product recommendation metadata.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("products") as batch_op:
        batch_op.add_column(
            sa.Column(
                "price_type",
                sa.String(30),
                server_default="retail_reference",
                nullable=False,
            )
        )
        batch_op.add_column(sa.Column("price_source_url", sa.String(1000)))
        batch_op.add_column(sa.Column("price_verified_at", sa.Date()))
        batch_op.add_column(sa.Column("usage_context", sa.String(50)))
        batch_op.add_column(
            sa.Column("is_verified", sa.Boolean(), server_default=sa.false(), nullable=False)
        )
        batch_op.create_check_constraint("ck_product_price_positive", "price > 0")
    op.execute(
        "UPDATE products SET is_verified = true "
        "WHERE product_url LIKE 'https://www.lge.co.kr/%' "
        "OR product_url LIKE 'https://www.samsung.com/%'"
    )
    op.execute(
        "UPDATE products SET usage_context = CASE "
        "WHEN product_url LIKE '%/sq06ea1wcs-akor' THEN 'guest_room' "
        "WHEN product_url LIKE '%/32lb650bena-stand' THEN 'guest_room' "
        "WHEN product_url LIKE '%/AR06D1150HZT/' THEN 'guest_room' "
        "WHEN product_url LIKE '%/fq17gw1hn1' THEN 'common_area' "
        "WHEN product_url LIKE '%/fq17gw1hn2' THEN 'common_area' "
        "WHEN product_url LIKE '%/f17ntpr' THEN 'laundry_room' "
        "WHEN product_url LIKE '%/rd20knt' THEN 'laundry_room' "
        "WHEN product_url LIKE '%/s834mee111' THEN 'residential_large' "
        "WHEN product_url LIKE '%/RS84DB5002CW/' THEN 'residential_large' "
        "ELSE usage_context END"
    )
    with op.batch_alter_table("products") as batch_op:
        batch_op.alter_column("price_type", server_default=None)
        batch_op.alter_column("is_verified", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("products") as batch_op:
        batch_op.drop_constraint("ck_product_price_positive", type_="check")
        batch_op.drop_column("is_verified")
        batch_op.drop_column("usage_context")
        batch_op.drop_column("price_verified_at")
        batch_op.drop_column("price_source_url")
        batch_op.drop_column("price_type")
