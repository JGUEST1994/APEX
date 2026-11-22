"""Where a practice account's own trading starts.

``paper_accounts.starts_after`` is the newest stored bar when an account was created with
``start_now``: it trades only bars after it, and its paper-vs-backtest report compares only
those. NULL (every account made before this migration, and the default) means the account
replays all stored history.

Revision ID: 0008
Revises: 0007
"""
import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("paper_accounts") as batch:
        batch.add_column(sa.Column("starts_after", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("paper_accounts") as batch:
        batch.drop_column("starts_after")
