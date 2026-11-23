"""Economic calendar and macro series with their publication vintages.

``econ_events``: scheduled releases (jobs, inflation, GDP, FOMC...) by date.
``macro_observations``: one row per series, observation date and vintage (the day a value
was first published, from ALFRED), with ``known_at``, the earliest moment a backtest may
use it.

Revision ID: 0010
Revises: 0009
"""
import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "econ_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("release_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("event_date", sa.Date, nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source", "release_id", "event_date", name="uq_econ_event"),
    )
    op.create_index("ix_econ_events_category", "econ_events", ["category"])
    op.create_index("ix_econ_events_event_date", "econ_events", ["event_date"])
    op.create_table(
        "macro_observations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("series_id", sa.String(32), nullable=False),
        sa.Column("obs_date", sa.Date, nullable=False),
        sa.Column("vintage", sa.Date, nullable=False),
        sa.Column("value", sa.Float, nullable=False),
        sa.Column("known_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("series_id", "obs_date", "vintage", name="uq_macro_vintage"),
    )
    op.create_index("ix_macro_series_known", "macro_observations", ["series_id", "known_at"])


def downgrade() -> None:
    op.drop_table("macro_observations")
    op.drop_table("econ_events")
