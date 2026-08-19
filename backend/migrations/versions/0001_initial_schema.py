"""initial schema

Captures the schema as it existed at the end of the capstone, when tables were
created by Base.metadata.create_all at startup. Databases created that way are
stamped with this revision rather than re-running it (see app/migrate.py).

Revision ID: 0001
Revises:
Create Date: 2026-08-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "virtual_keys",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("virtual_key", sa.String(), nullable=False),
        sa.Column("team", sa.String(), nullable=False),
        sa.Column("monthly_budget_usd", sa.Numeric(14, 6), nullable=False),
        sa.Column("requests_per_minute", sa.Integer(), nullable=False),
        sa.Column("tokens_per_minute", sa.Integer(), nullable=True),
        sa.Column("model_allowlist", sa.JSON(), nullable=False),
        sa.Column("cache_enabled", sa.Boolean(), nullable=False),
        sa.Column("cache_similarity_threshold", sa.Float(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_virtual_keys_virtual_key", "virtual_keys", ["virtual_key"], unique=True)

    op.create_table(
        "request_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(), nullable=False),
        sa.Column("virtual_key", sa.String(), nullable=False),
        sa.Column("requested_model", sa.String(), nullable=False),
        sa.Column("resolved_provider", sa.String(), nullable=True),
        sa.Column("resolved_model", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(14, 6), nullable=False),
        sa.Column("cache", sa.String(), nullable=False),
        sa.Column("fallback", sa.Boolean(), nullable=False),
        sa.Column("route_reason", sa.String(), nullable=True),
        sa.Column("retries", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_request_logs_request_id", "request_logs", ["request_id"], unique=True)
    op.create_index("ix_request_logs_virtual_key", "request_logs", ["virtual_key"], unique=False)

    op.create_table(
        "rate_limit_windows",
        sa.Column("virtual_key", sa.String(), nullable=False),
        sa.Column("window_start", sa.Integer(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("virtual_key", "window_start"),
    )

    op.create_table(
        "usage_records",
        sa.Column("virtual_key", sa.String(), nullable=False),
        sa.Column("year_month", sa.String(), nullable=False),
        sa.Column("spend_usd", sa.Numeric(14, 6), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("requests", sa.Integer(), nullable=False),
        sa.Column("cache_hits", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("virtual_key", "year_month"),
    )

    op.create_table(
        "cache_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("virtual_key", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("prompt_text", sa.String(), nullable=False),
        sa.Column("token_counts", sa.JSON(), nullable=False),
        sa.Column("response_body", sa.JSON(), nullable=False),
        sa.Column("resolved_provider", sa.String(), nullable=False),
        sa.Column("resolved_model", sa.String(), nullable=False),
        sa.Column("hit_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cache_entries_virtual_key", "cache_entries", ["virtual_key"], unique=False)

    op.create_table(
        "admin_users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_admin_users_username", "admin_users", ["username"], unique=True)

    op.create_table(
        "refresh_tokens",
        sa.Column("jti", sa.String(), nullable=False),
        sa.Column("admin_user_id", sa.Integer(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("jti"),
    )
    op.create_index(
        "ix_refresh_tokens_admin_user_id", "refresh_tokens", ["admin_user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_table("refresh_tokens")
    op.drop_table("admin_users")
    op.drop_table("cache_entries")
    op.drop_table("usage_records")
    op.drop_table("rate_limit_windows")
    op.drop_table("request_logs")
    op.drop_table("virtual_keys")
