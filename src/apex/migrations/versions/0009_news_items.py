"""News and official releases, stored point in time.

``news_items`` keeps every version of every item with the time the source claims
(``published_at``), the time APEX first saw it (``first_seen_at``) and the later of the two
(``known_at``), which is the earliest moment a backtest may use it. ``news_symbols`` links
an item to the markets it is about.

Revision ID: 0009
Revises: 0008
"""
import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "news_items",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_id", sa.String(255), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("summary", sa.Text),
        sa.Column("url", sa.Text),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("known_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("licence", sa.String(255), nullable=False),
        sa.Column("extra", sa.JSON, nullable=False),
        sa.UniqueConstraint("source", "source_id", "version", name="uq_news_version"),
    )
    op.create_index("ix_news_items_source", "news_items", ["source"])
    op.create_index("ix_news_known", "news_items", ["known_at"])
    op.create_table(
        "news_symbols",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("item_id", sa.Integer,
                  sa.ForeignKey("news_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("linked_by", sa.String(16), nullable=False),
        sa.UniqueConstraint("item_id", "symbol", name="uq_news_symbol"),
    )
    op.create_index("ix_news_symbols_item_id", "news_symbols", ["item_id"])
    op.create_index("ix_news_symbols_symbol", "news_symbols", ["symbol"])


def downgrade() -> None:
    op.drop_table("news_symbols")
    op.drop_table("news_items")
