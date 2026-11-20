"""Optional aggressive-buyer volume on bars.

``bars.buy_volume`` holds the part of a bar's volume that came from buyers lifting the ask
(Binance reports it as taker-buy volume). It is NULL where the source does not report it,
including every bar stored before this migration.

Revision ID: 0007
Revises: 0006
"""
import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("bars") as batch:
        batch.add_column(sa.Column("buy_volume", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("bars") as batch:
        batch.drop_column("buy_volume")
