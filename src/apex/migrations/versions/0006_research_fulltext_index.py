"""Full-text index on research chunks (PostgreSQL only).

A GIN index on ``to_tsvector('english', text)`` lets large research libraries pick keyword
candidates inside the database instead of scanning every chunk in Python. On SQLite this
migration is a deliberate no-op (the library there is scanned in memory).

Revision ID: 0006
Revises: 0005
"""
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE INDEX IF NOT EXISTS ix_research_chunks_fts ON research_chunks "
                   "USING gin (to_tsvector('english', text))")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_research_chunks_fts")
