"""self-serve orgs, users, and BYOK provider credentials

Purely additive - no existing table's data is touched. Turns Prism from
"operator hand-provisions tenants via data/seed_keys.json" into a self-serve
model: a person signs up (organizations + users), optionally adds their own
upstream provider API key (provider_credentials, encrypted at rest), and
self-issues a rate/budget-capped virtual_keys row (org_id added below).

Seeded/operator-provisioned virtual_keys rows keep org_id NULL - there is no
synthetic org fabricated for them, since that would also require a synthetic
user nobody can log in as, for no benefit. Every self-serve-issued key always
has org_id set, enforced at the API layer (app/self_serve_keys.py), not here.

refresh_tokens is generalized to serve both the existing platform-operator
admin login and the new end-user login with one table: admin_user_id becomes
subject_id, and a new subject_type column ("admin" for every existing row,
via server_default) disambiguates which. The single-use atomic-rotation SQL
in app/admin_auth.py is identical in shape for both, so this avoids
duplicating that logic into a second table.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-19
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(), nullable=False, server_default="owner"),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_org_id", "users", ["org_id"])

    op.create_table(
        "provider_credentials",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("encrypted_key", sa.String(), nullable=False),
        sa.Column("key_prefix", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "provider", name="uq_provider_credentials_org_provider"),
    )
    op.create_index("ix_provider_credentials_org_id", "provider_credentials", ["org_id"])

    op.add_column("virtual_keys", sa.Column("org_id", sa.Integer(), nullable=True))
    op.create_index("ix_virtual_keys_org_id", "virtual_keys", ["org_id"])
    op.create_foreign_key(
        "fk_virtual_keys_org_id", "virtual_keys", "organizations", ["org_id"], ["id"]
    )

    op.add_column(
        "refresh_tokens",
        sa.Column("subject_type", sa.String(), nullable=False, server_default="admin"),
    )
    op.alter_column("refresh_tokens", "admin_user_id", new_column_name="subject_id")
    op.drop_index("ix_refresh_tokens_admin_user_id", table_name="refresh_tokens")
    op.create_index(
        "ix_refresh_tokens_subject", "refresh_tokens", ["subject_type", "subject_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_refresh_tokens_subject", table_name="refresh_tokens")
    op.create_index("ix_refresh_tokens_admin_user_id", "refresh_tokens", ["admin_user_id"])
    op.alter_column("refresh_tokens", "subject_id", new_column_name="admin_user_id")
    op.drop_column("refresh_tokens", "subject_type")

    op.drop_constraint("fk_virtual_keys_org_id", "virtual_keys", type_="foreignkey")
    op.drop_index("ix_virtual_keys_org_id", table_name="virtual_keys")
    op.drop_column("virtual_keys", "org_id")

    op.drop_table("provider_credentials")

    op.drop_index("ix_users_org_id", table_name="users")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")

    op.drop_table("organizations")
