"""Paper accounts can trade several instruments: optional ``symbols`` list.

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("paper_accounts") as b:
        b.add_column(sa.Column("symbols", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("paper_accounts") as b:
        b.drop_column("symbols")
