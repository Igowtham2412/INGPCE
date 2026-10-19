"""Add optional usernames for dashboard authentication.

Revision ID: 0007_auth_username
Revises: 0006_violation_dedup_index
"""
from alembic import op

revision = "0007_auth_username"
down_revision = "0006_violation_dedup_index"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS username TEXT")
    op.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_lower
           ON users (lower(username)) WHERE username IS NOT NULL"""
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_users_username_lower")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS username")
