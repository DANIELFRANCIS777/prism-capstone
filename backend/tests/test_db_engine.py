"""Connection settings for pooled vs direct Postgres.

The failure this guards against only appears in production: against a
transaction-mode pooler (Neon's `-pooler` endpoint, Supabase, RDS Proxy),
asyncpg's prepared statements break with "prepared statement
__asyncpg_stmt_N__ does not exist", because consecutive statements land on
different server connections. Local dev talks straight to Postgres and never
sees it.
"""

from sqlalchemy.pool import NullPool

from app.db import _engine_kwargs, _is_transaction_pooled

NEON_POOLED = (
    "postgresql+asyncpg://u:p@ep-late-credit-ay18aefz-pooler.c-5.us-east-2.aws.neon.tech/neondb"
)
NEON_DIRECT = (
    "postgresql+asyncpg://u:p@ep-late-credit-ay18aefz.c-5.us-east-2.aws.neon.tech/neondb"
)
LOCAL = "postgresql+asyncpg://prism:prism@localhost:5432/prism"


def test_detects_neon_pooled_endpoint():
    assert _is_transaction_pooled(NEON_POOLED)


def test_does_not_flag_neon_direct_endpoint():
    assert not _is_transaction_pooled(NEON_DIRECT)


def test_does_not_flag_a_plain_local_database():
    assert not _is_transaction_pooled(LOCAL)


def test_a_password_containing_pooler_does_not_trigger_detection():
    """Only the host is inspected - a credential that happens to contain the
    word must not silently switch the engine into pooled mode."""
    assert not _is_transaction_pooled("postgresql+asyncpg://u:pooler-pw@localhost:5432/prism")


def test_pooled_url_disables_prepared_statement_caching():
    kwargs = _engine_kwargs(NEON_POOLED)
    assert kwargs["prepared_statement_cache_size"] == 0
    assert kwargs["connect_args"]["statement_cache_size"] == 0
    # Unique names, since asyncpg's numeric ones collide across clients
    # sharing a proxy.
    name_func = kwargs["connect_args"]["prepared_statement_name_func"]
    assert name_func() != name_func()


def test_pooled_url_uses_nullpool_and_drops_client_side_pooling():
    """SQLAlchemy's own warning: pooling on both sides of a proxy piles up
    prepared statements. The pooler is already doing the pooling."""
    kwargs = _engine_kwargs(NEON_POOLED)
    assert kwargs["poolclass"] is NullPool
    assert "pool_pre_ping" not in kwargs
    assert "pool_recycle" not in kwargs


def test_direct_url_keeps_pre_ping_for_scale_to_zero():
    """Neon suspends when idle; without pre-ping the first request back finds
    a dead socket."""
    kwargs = _engine_kwargs(NEON_DIRECT)
    assert kwargs["pool_pre_ping"] is True
    assert kwargs["pool_recycle"] == 300
    assert "connect_args" not in kwargs


def test_local_url_is_untouched_by_any_of_this():
    kwargs = _engine_kwargs(LOCAL)
    assert kwargs["pool_pre_ping"] is True
    assert "poolclass" not in kwargs
    assert "connect_args" not in kwargs
