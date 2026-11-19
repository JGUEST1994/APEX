"""Paper trading state: accounts (with kill switch), orders, fills, positions.

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "paper_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False, unique=True),
        sa.Column("strategy_key", sa.String(64), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("timeframe", sa.String(8), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("limits", sa.JSON(), nullable=False),
        sa.Column("initial_capital", sa.Float(), nullable=False),
        sa.Column("cash", sa.Float(), nullable=False),
        sa.Column("realised", sa.Float(), nullable=False),
        sa.Column("consecutive_losses", sa.Integer(), nullable=False),
        sa.Column("peak_equity", sa.Float(), nullable=False),
        sa.Column("day_start_equity", sa.Float(), nullable=False),
        sa.Column("day", sa.String(10), nullable=True),
        sa.Column("last_bar_ts", TS, nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("kill_engaged", sa.Boolean(), nullable=False),
        sa.Column("kill_reason", sa.String(256), nullable=False),
        sa.Column("kill_at", TS, nullable=True),
        sa.Column("kill_by", sa.String(64), nullable=False),
        sa.Column("created_at", TS, nullable=False),
    )
    op.create_table(
        "paper_orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(),
                  sa.ForeignKey("paper_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_order_id", sa.String(64), nullable=False, unique=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("qty", sa.Float(), nullable=False),
        sa.Column("stop_price", sa.Float(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("filled_qty", sa.Float(), nullable=False),
        sa.Column("avg_price", sa.Float(), nullable=False),
        sa.Column("reason", sa.String(256), nullable=False),
        sa.Column("signal_ts", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
    )
    op.create_index("ix_paper_orders_account_id", "paper_orders", ["account_id"])
    op.create_table(
        "paper_fills",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(),
                  sa.ForeignKey("paper_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_order_id", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("qty", sa.Float(), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("cost", sa.Float(), nullable=False),
        sa.Column("ts", TS, nullable=False),
    )
    op.create_index("ix_paper_fills_account_id", "paper_fills", ["account_id"])
    op.create_index("ix_paper_fills_client_order_id", "paper_fills", ["client_order_id"])
    op.create_table(
        "paper_positions",
        sa.Column("account_id", sa.Integer(),
                  sa.ForeignKey("paper_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("units", sa.Float(), nullable=False),
        sa.Column("avg_cost", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("account_id", "symbol"),
    )


def downgrade() -> None:
    op.drop_table("paper_positions")
    op.drop_index("ix_paper_fills_client_order_id", table_name="paper_fills")
    op.drop_index("ix_paper_fills_account_id", table_name="paper_fills")
    op.drop_table("paper_fills")
    op.drop_index("ix_paper_orders_account_id", table_name="paper_orders")
    op.drop_table("paper_orders")
    op.drop_table("paper_accounts")
