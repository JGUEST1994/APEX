"""Relational schema. See docs/design/03-database-architecture.md for the rationale.

The schema is portable between PostgreSQL (servers) and SQLite (single-machine installs and
tests). PostgreSQL-only features (pgvector, partitioning, TimescaleDB) are added by optional
migrations so the core never depends on them.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from apex.db.types import UTCDateTime


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------------- market data
class Instrument(Base):
    __tablename__ = "instruments"
    __table_args__ = (UniqueConstraint("symbol", "venue", name="uq_instrument_symbol_venue"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32))
    asset_class: Mapped[str] = mapped_column(String(16), index=True)
    venue: Mapped[str] = mapped_column(String(32))
    calendar: Mapped[str] = mapped_column(String(32))
    base_currency: Mapped[str | None] = mapped_column(String(12))
    quote_currency: Mapped[str] = mapped_column(String(12))
    tick_size: Mapped[float] = mapped_column(Float)
    contract_multiplier: Mapped[float] = mapped_column(Float, default=1.0)
    lot_size: Mapped[float] = mapped_column(Float, default=1.0)
    cost_model: Mapped[dict] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class IngestionRun(Base):
    """One fetch of data from one provider. Doubles as the data-version record."""

    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(32))
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), index=True)
    timeframe: Mapped[str] = mapped_column(String(8))
    range_start: Mapped[datetime] = mapped_column(UTCDateTime)
    range_end: Mapped[datetime] = mapped_column(UTCDateTime)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running|ok|warn|failed
    rows_received: Mapped[int] = mapped_column(Integer, default=0)
    rows_stored: Mapped[int] = mapped_column(Integer, default=0)
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)

    issues: Mapped[list[DataQualityIssue]] = relationship(back_populates="run")


class PriceBar(Base):
    __tablename__ = "bars"

    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(8), primary_key=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, primary_key=True)
    source: Mapped[str] = mapped_column(String(32), primary_key=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float, default=0.0)
    # aggressive-buyer part of volume, where the source reports it (else NULL)
    buy_volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    ingestion_run_id: Mapped[int | None] = mapped_column(ForeignKey("ingestion_runs.id"))


class DataQualityIssue(Base):
    __tablename__ = "data_quality_issues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("ingestion_runs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(8))  # error|warning
    ts: Mapped[datetime | None] = mapped_column(UTCDateTime)
    detail: Mapped[str] = mapped_column(Text, default="")

    run: Mapped[IngestionRun] = relationship(back_populates="issues")


# ---------------------------------------------------------------------- strategy lab
class Strategy(Base):
    __tablename__ = "strategies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True)  # stable id, e.g. "MR-VWAP-001"
    name: Mapped[str] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(32))
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class StrategyVersion(Base):
    __tablename__ = "strategy_versions"
    __table_args__ = (UniqueConstraint("strategy_id", "version", name="uq_strategy_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id"), index=True)
    version: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="experimental")
    spec: Mapped[dict] = mapped_column(JSON, default=dict)  # rules, instruments, timeframes...
    code_sha256: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class StrategyStatusChange(Base):
    __tablename__ = "strategy_status_changes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_version_id: Mapped[int] = mapped_column(ForeignKey("strategy_versions.id"))
    from_status: Mapped[str | None] = mapped_column(String(16))
    to_status: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(64), default="system")
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Experiment(Base):
    """A reproducible backtest/validation run: exact data, code and parameters."""

    __tablename__ = "experiments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_version_id: Mapped[int] = mapped_column(ForeignKey("strategy_versions.id"))
    kind: Mapped[str] = mapped_column(String(24))  # backtest|walk_forward|monte_carlo|...
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    data_manifest: Mapped[dict] = mapped_column(JSON, default=dict)  # instruments, ranges, hashes
    code_version: Mapped[str] = mapped_column(String(64), default="")  # git commit
    random_seed: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


# --------------------------------------------------------------------- research library
class ResearchDocument(Base):
    __tablename__ = "research_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(512))
    authors: Mapped[str] = mapped_column(String(512), default="")
    published: Mapped[str | None] = mapped_column(String(32))
    source_type: Mapped[str] = mapped_column(String(24))  # pdf|paper|html|csv|transcript|doc
    source_ref: Mapped[str] = mapped_column(Text, default="")  # URL or file name
    licence_note: Mapped[str] = mapped_column(Text, default="")  # how we may use it
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    subjects: Mapped[list] = mapped_column(JSON, default=list)
    extracted: Mapped[dict] = mapped_column(JSON, default=dict)  # concepts, formulas, rules...
    imported_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    chunks: Mapped[list[ResearchChunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="ResearchChunk.ordinal"
    )


class ResearchChunk(Base):
    """Retrieval unit. Embeddings live in an optional pgvector table (later migration)."""

    __tablename__ = "research_chunks"
    __table_args__ = (Index("ix_chunk_doc_ordinal", "document_id", "ordinal", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("research_documents.id"))
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)

    document: Mapped[ResearchDocument] = relationship(back_populates="chunks")


class ChunkVector(Base):
    __tablename__ = "research_chunk_vectors"

    chunk_id: Mapped[int] = mapped_column(
        ForeignKey("research_chunks.id", ondelete="CASCADE"), primary_key=True)
    model: Mapped[str] = mapped_column(String(64), primary_key=True)
    dim: Mapped[int] = mapped_column(Integer)
    vector: Mapped[bytes] = mapped_column(LargeBinary)


class JournalEntry(Base):
    """Permanent research journal (Section 13). Append-only by convention."""

    __tablename__ = "research_journal"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    author: Mapped[str] = mapped_column(String(64), default="system")
    kind: Mapped[str] = mapped_column(String(24))  # hypothesis|finding|decision|note
    title: Mapped[str] = mapped_column(String(256))
    body: Mapped[str] = mapped_column(Text, default="")
    links: Mapped[dict] = mapped_column(JSON, default=dict)


# --------------------------------------------------------------------------- security
class AuditEvent(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str] = mapped_column(String(256), default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


# --------------------------------------------------------------------------- paper trading
class PaperAccount(Base):
    """One simulated account running one strategy on one or more instruments (Phase 9)."""

    @property
    def all_symbols(self) -> list[str]:
        return list(self.symbols) if self.symbols else [self.symbol]

    __tablename__ = "paper_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    strategy_key: Mapped[str] = mapped_column(String(64))
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    symbol: Mapped[str] = mapped_column(String(32))  # primary instrument
    symbols: Mapped[list | None] = mapped_column(JSON, nullable=True)  # all, if several
    timeframe: Mapped[str] = mapped_column(String(8))
    source: Mapped[str] = mapped_column(String(64))
    limits: Mapped[dict] = mapped_column(JSON, default=dict)
    initial_capital: Mapped[float] = mapped_column(Float)
    cash: Mapped[float] = mapped_column(Float)
    realised: Mapped[float] = mapped_column(Float, default=0.0)
    consecutive_losses: Mapped[int] = mapped_column(Integer, default=0)
    peak_equity: Mapped[float] = mapped_column(Float)
    day_start_equity: Mapped[float] = mapped_column(Float)
    day: Mapped[str | None] = mapped_column(String(10), nullable=True)
    last_bar_ts: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # set when created with start_now: the account trades (and is compared) only after it
    starts_after: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running|halted
    kill_engaged: Mapped[bool] = mapped_column(Boolean, default=False)
    kill_reason: Mapped[str] = mapped_column(String(256), default="")
    kill_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    kill_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class PaperOrder(Base):
    __tablename__ = "paper_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id", ondelete="CASCADE"),
                                            index=True)
    client_order_id: Mapped[str] = mapped_column(String(64), unique=True)
    symbol: Mapped[str] = mapped_column(String(32))
    qty: Mapped[float] = mapped_column(Float)
    stop_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20))
    filled_qty: Mapped[float] = mapped_column(Float, default=0.0)
    avg_price: Mapped[float] = mapped_column(Float, default=0.0)
    reason: Mapped[str] = mapped_column(String(256), default="")
    signal_ts: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class PaperFill(Base):
    __tablename__ = "paper_fills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id", ondelete="CASCADE"),
                                            index=True)
    client_order_id: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    qty: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    cost: Mapped[float] = mapped_column(Float)
    ts: Mapped[datetime] = mapped_column(UTCDateTime)


class PaperPosition(Base):
    __tablename__ = "paper_positions"

    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id", ondelete="CASCADE"),
                                            primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    units: Mapped[float] = mapped_column(Float)
    avg_cost: Mapped[float] = mapped_column(Float, default=0.0)


# --------------------------------------------------------------------------- news and events
class NewsItem(Base):
    """One version of one news item or official release, stored point in time.

    ``published_at`` is the time the source claims; ``first_seen_at`` is when APEX first saw
    this version. Anything judged on history uses the later of the two (``known_at``) and may
    act only on a bar that opens after it. A source that changes an item gets a new row with
    the next ``version``; stored rows are never edited.
    """

    __tablename__ = "news_items"
    __table_args__ = (UniqueConstraint("source", "source_id", "version", name="uq_news_version"),
                      Index("ix_news_known", "known_at"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    source_id: Mapped[str] = mapped_column(String(255))
    version: Mapped[int] = mapped_column(Integer, default=1)
    kind: Mapped[str] = mapped_column(String(16))  # filing, central_bank, regulator, news
    title: Mapped[str] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime] = mapped_column(UTCDateTime)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime)
    known_at: Mapped[datetime] = mapped_column(UTCDateTime)
    content_hash: Mapped[str] = mapped_column(String(64))
    licence: Mapped[str] = mapped_column(String(255))
    extra: Mapped[dict] = mapped_column(JSON, default=dict)
    symbols: Mapped[list[NewsSymbol]] = relationship(back_populates="item",
                                                     cascade="all, delete-orphan")


class NewsSymbol(Base):
    """Which market an item is about, and how APEX knows (``source`` tag or ``cik`` match)."""

    __tablename__ = "news_symbols"
    __table_args__ = (UniqueConstraint("item_id", "symbol", name="uq_news_symbol"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("news_items.id", ondelete="CASCADE"),
                                         index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    linked_by: Mapped[str] = mapped_column(String(16))
    item: Mapped[NewsItem] = relationship(back_populates="symbols")
