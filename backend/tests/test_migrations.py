"""Guards the migration chain itself.

The failure this exists to catch: someone edits app/models.py, the ORM keeps
working locally because their dev database already has the column, and the
missing revision only surfaces as a 500 in production against a database that
was built from migrations. Comparing the migrated schema against the live
metadata turns that into a red test at the moment the model changes.
"""

import app.models  # noqa: F401  - registers every table on Base.metadata
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext

from app.db import Base
from app.migrate import INITIAL_REVISION


def _schema_diff(connection):
    return compare_metadata(MigrationContext.configure(connection), Base.metadata)


async def test_migrated_schema_matches_models(test_engine):
    """The migrated database and app/models.py describe the same schema."""
    async with test_engine.connect() as connection:
        diff = await connection.run_sync(_schema_diff)

    assert diff == [], (
        "app/models.py has drifted from the migrations. Generate a revision with "
        "`alembic revision --autogenerate -m '<what changed>'` and review it. Diff: "
        f"{diff}"
    )


async def test_migrations_are_recorded_as_applied(test_engine):
    """The database is stamped, so a later `alembic upgrade` is incremental
    rather than trying to recreate tables that already exist."""
    async with test_engine.connect() as connection:
        revision = await connection.run_sync(
            lambda c: MigrationContext.configure(c).get_current_revision()
        )

    assert revision is not None, "migrations ran but left no alembic_version row"
    assert revision >= INITIAL_REVISION
