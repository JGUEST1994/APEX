"""Optional vector search table (PostgreSQL + pgvector only).

On SQLite, or on PostgreSQL without the pgvector extension installed, this migration is a
deliberate no-op: the research library falls back to keyword search. Embedding dimension is
fixed at 384 to match small local sentence-transformer models (e.g. all-MiniLM-L6-v2), so
embeddings can be produced without any external AI provider.

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

EMBEDDING_DIM = 384


def _pgvector_available() -> bool:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return False
    row = bind.execute(
        sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")
    ).first()
    return row is not None


def upgrade() -> None:
    if not _pgvector_available():
        return
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        f"""
        CREATE TABLE research_embeddings (
            chunk_id INTEGER PRIMARY KEY REFERENCES research_chunks(id) ON DELETE CASCADE,
            model TEXT NOT NULL,
            embedding vector({EMBEDDING_DIM}) NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_research_embeddings_hnsw ON research_embeddings "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS research_embeddings")
