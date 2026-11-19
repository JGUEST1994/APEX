"""Portable chunk embeddings (float32 bytes) usable on every database.

Similarity is computed in NumPy. The pgvector table from 0002 remains available for large
libraries on PostgreSQL.

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_chunk_vectors",
        sa.Column("chunk_id", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("dim", sa.Integer(), nullable=False),
        sa.Column("vector", sa.LargeBinary(), nullable=False),
        sa.ForeignKeyConstraint(["chunk_id"], ["research_chunks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("chunk_id", "model"),
    )


def downgrade() -> None:
    op.drop_table("research_chunk_vectors")
