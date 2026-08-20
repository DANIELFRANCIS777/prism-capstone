"""auth attempt throttling and lockout

Backs app/auth_throttle.py. Purely additive - one new table, nothing
existing is touched.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-20
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "auth_attempts",
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column("identifier", sa.String(), nullable=False),
        sa.Column("window_start", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("scope", "identifier", "window_start"),
    )


def downgrade() -> None:
    op.drop_table("auth_attempts")
