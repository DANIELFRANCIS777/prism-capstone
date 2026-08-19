"""Schema migration on startup.

Self-hosting is a headline goal, so `docker compose up` has to leave the operator
with a correctly-migrated database without a separate release step. That is safe
under multiple replicas because every run takes a Postgres session-level advisory
lock first - the losers block until the winner finishes, then find nothing to do.

Operators who prefer an explicit release step (Fly's `release_command`, a k8s Job)
can set RUN_MIGRATIONS_ON_STARTUP=false and run `alembic upgrade head` themselves.
"""

import logging

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect, text

from app.config import BACKEND_ROOT
from app.db import engine

logger = logging.getLogger(__name__)

# Arbitrary but fixed - any constant works as long as every replica uses the same
# one. Namespaced away from 0 so it can't collide with a default-valued lock.
_MIGRATION_LOCK_ID = 8_675_309

# Present in every database created by the pre-Alembic startup path, and created
# by revision 0001 - so "this table exists but alembic_version doesn't" uniquely
# identifies a legacy create_all database.
_LEGACY_SENTINEL_TABLE = "virtual_keys"

INITIAL_REVISION = "0001"


def _alembic_config() -> Config:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    return cfg


def _upgrade(connection) -> None:
    """Runs inside connection.run_sync() - `connection` is a sync Connection."""
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    already_versioned = MigrationContext.configure(connection).get_current_revision()

    cfg = _alembic_config()
    cfg.attributes["connection"] = connection

    if already_versioned is None and _LEGACY_SENTINEL_TABLE in tables:
        # A database built by the old Base.metadata.create_all. Its schema already
        # matches revision 0001, so running 0001 would fail on "table exists";
        # record it as applied and let later revisions run normally.
        logger.info("existing unversioned schema detected; stamping %s", INITIAL_REVISION)
        command.stamp(cfg, INITIAL_REVISION)

    command.upgrade(cfg, "head")


async def run_migrations() -> None:
    async with engine.begin() as connection:
        await connection.execute(text("SELECT pg_advisory_lock(:id)"), {"id": _MIGRATION_LOCK_ID})
        try:
            await connection.run_sync(_upgrade)
        finally:
            await connection.execute(
                text("SELECT pg_advisory_unlock(:id)"), {"id": _MIGRATION_LOCK_ID}
            )
    logger.info("database schema is up to date")
