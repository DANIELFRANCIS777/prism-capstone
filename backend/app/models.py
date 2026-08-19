import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class VirtualKey(Base):
    """A tenant. Seeded from data/seed_keys.json on startup."""

    __tablename__ = "virtual_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    # sha256 hex of the raw bearer token - the raw value is never stored past
    # issuance. key_prefix is a display-only slice for admin/self-serve UIs.
    key_hash: Mapped[str] = mapped_column(String, unique=True, index=True)
    key_prefix: Mapped[str] = mapped_column(String)
    team: Mapped[str] = mapped_column(String)
    monthly_budget_usd: Mapped[float] = mapped_column(Numeric(14, 6))
    requests_per_minute: Mapped[int] = mapped_column(Integer)
    tokens_per_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_allowlist: Mapped[list] = mapped_column(JSON)
    cache_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    cache_similarity_threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class RequestLog(Base):
    """One row per data-plane request, including rejections. See docs/DATA_MODEL.md."""

    __tablename__ = "request_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[str] = mapped_column(
        String, unique=True, index=True, default=lambda: str(uuid.uuid4())
    )
    # Nullable: a request rejected before authentication resolves (bad/missing
    # bearer token) never identifies a real key - see routers/chat.py's
    # rejected_auth path.
    virtual_key_id: Mapped[int | None] = mapped_column(
        ForeignKey("virtual_keys.id"), index=True, nullable=True
    )
    requested_model: Mapped[str] = mapped_column(String)
    resolved_provider: Mapped[str | None] = mapped_column(String, nullable=True)
    resolved_model: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Numeric(14, 6), default=0)
    cache: Mapped[str] = mapped_column(String, default="miss")
    fallback: Mapped[bool] = mapped_column(Boolean, default=False)
    route_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    retries: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class RateLimitWindow(Base):
    """Fixed 60s window counter per key. Admission is a single atomic
    upsert-and-increment, so concurrent requests can't over-admit."""

    __tablename__ = "rate_limit_windows"

    virtual_key_id: Mapped[int] = mapped_column(ForeignKey("virtual_keys.id"), primary_key=True)
    window_start: Mapped[int] = mapped_column(Integer, primary_key=True)  # unix seconds, floored to 60
    count: Mapped[int] = mapped_column(Integer, default=0)


class UsageRecord(Base):
    """Per-key, per-month spend/token counters used for the hot-path budget check.
    Incremented atomically; kept separate from RequestLog (the reporting source of
    truth) so the budget check stays a cheap single-row read, not a log scan."""

    __tablename__ = "usage_records"

    virtual_key_id: Mapped[int] = mapped_column(ForeignKey("virtual_keys.id"), primary_key=True)
    year_month: Mapped[str] = mapped_column(String, primary_key=True)  # "YYYY-MM"
    spend_usd: Mapped[float] = mapped_column(Numeric(14, 6), default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    requests: Mapped[int] = mapped_column(Integer, default=0)
    cache_hits: Mapped[int] = mapped_column(Integer, default=0)


class CacheEntry(Base):
    """Scoped per (virtual_key, requested model/alias) - never shared across
    tenants. `token_counts` is the bag-of-words vector used for cosine
    similarity at query time (see app/semantic.py)."""

    __tablename__ = "cache_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    virtual_key_id: Mapped[int] = mapped_column(ForeignKey("virtual_keys.id"), index=True)
    model: Mapped[str] = mapped_column(String)
    prompt_text: Mapped[str] = mapped_column(String)
    token_counts: Mapped[dict] = mapped_column(JSON)
    response_body: Mapped[dict] = mapped_column(JSON)
    resolved_provider: Mapped[str] = mapped_column(String)
    resolved_model: Mapped[str] = mapped_column(String)
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class AdminUser(Base):
    """A platform-admin login. Bootstrapped from Settings on first startup
    (see app/seed.py) - there's no signup route."""

    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class RefreshToken(Base):
    """Server-side record of every issued refresh token, keyed by the JWT's
    `jti` claim - this is what makes refresh tokens revocable and single-use
    (rotation), which the JWT's signature and expiry alone can't provide.
    The raw token is never stored, only its jti."""

    __tablename__ = "refresh_tokens"

    jti: Mapped[str] = mapped_column(String, primary_key=True)
    admin_user_id: Mapped[int] = mapped_column(Integer, index=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
