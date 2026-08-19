import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
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
    # NULL for operator-provisioned/seeded keys (data/seed_keys.json) - only
    # self-serve-issued keys (app/self_serve_keys.py) ever set this.
    org_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id"), index=True, nullable=True
    )


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
    The raw token is never stored, only its jti.

    Shared by both the platform-operator admin login and end-user login
    (subject_type disambiguates "admin" vs "user"; subject_id is that
    subject's id in the corresponding table) - the single-use atomic-rotation
    logic in app/jwt_tokens.py is identical in shape for both, so one table
    avoids duplicating it. No FK to admin_users/users, matching this table's
    existing FK-less convention - a subject row being deleted doesn't need to
    cascade here, it's enough that rotate/revoke re-check the subject still
    exists and is active."""

    __tablename__ = "refresh_tokens"
    __table_args__ = (Index("ix_refresh_tokens_subject", "subject_type", "subject_id"),)

    jti: Mapped[str] = mapped_column(String, primary_key=True)
    subject_type: Mapped[str] = mapped_column(String, default="admin")
    subject_id: Mapped[int] = mapped_column(Integer)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Organization(Base):
    """A self-serve tenant's org. Created at signup (app/routers/user.py);
    one org per user for now (no multi-user teams yet - see ROADMAP.md)."""

    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class User(Base):
    """A self-serve end user - distinct from AdminUser (the platform
    operator). No signup route exists for AdminUser; this is the reverse:
    the only way to create a User is /auth/signup."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    role: Mapped[str] = mapped_column(String, default="owner")
    status: Mapped[str] = mapped_column(String, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ProviderCredential(Base):
    """A tenant's own upstream provider API key (BYOK), encrypted at rest
    (app/credential_crypto.py). Consulted by dispatch instead of the
    platform's shared static config when present - see app/providers.py."""

    __tablename__ = "provider_credentials"
    __table_args__ = (
        UniqueConstraint("org_id", "provider", name="uq_provider_credentials_org_provider"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    provider: Mapped[str] = mapped_column(String)
    encrypted_key: Mapped[str] = mapped_column(String)
    key_prefix: Mapped[str] = mapped_column(String)
    label: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
