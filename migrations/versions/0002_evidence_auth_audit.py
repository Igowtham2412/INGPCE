"""Add evidence review, camera zones, user access, and audit data.

Revision ID: 0002_access
Revises: 0001_core
"""
from alembic import op

revision = "0002_access"
down_revision = "0001_core"
branch_labels = None
depends_on = None


def upgrade():
    statements = [
        "ALTER TABLE violations ADD COLUMN IF NOT EXISTS clean_snapshot_path TEXT",
        "ALTER TABLE violations ADD COLUMN IF NOT EXISTS video_path TEXT",
        "ALTER TABLE violations ADD COLUMN IF NOT EXISTS review_status TEXT NOT NULL DEFAULT 'unreviewed'",
        "ALTER TABLE violations ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ",
        "ALTER TABLE violations ADD COLUMN IF NOT EXISTS reviewed_by TEXT",
        "ALTER TABLE violations ADD COLUMN IF NOT EXISTS review_comment TEXT",
        "ALTER TABLE violations ADD COLUMN IF NOT EXISTS detection_metadata JSONB NOT NULL DEFAULT '[]'::jsonb",
        """CREATE TABLE IF NOT EXISTS violation_review_log (
            id BIGSERIAL PRIMARY KEY,
            violation_id BIGINT NOT NULL REFERENCES violations(id) ON DELETE CASCADE,
            review_status TEXT NOT NULL CHECK (review_status IN ('confirmed', 'false_positive')),
            reviewed_by TEXT,
            reviewed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            dataset_image_path TEXT NOT NULL,
            dataset_label_path TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS camera_zones (
            id BIGSERIAL PRIMARY KEY,
            camera_id TEXT NOT NULL REFERENCES cameras(camera_id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            polygon JSONB NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (camera_id, name)
        )""",
        """CREATE TABLE IF NOT EXISTS roles (
            name TEXT PRIMARY KEY,
            description TEXT NOT NULL
        )""",
        """INSERT INTO roles (name, description) VALUES
            ('admin', 'Full system administration'),
            ('manager', 'Manage cameras, alert rules, and operations'),
            ('operator', 'Review incidents and operate cameras'),
            ('viewer', 'Read-only access')
            ON CONFLICT (name) DO NOTHING""",
        """CREATE TABLE IF NOT EXISTS users (
            id BIGSERIAL PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL REFERENCES roles(name),
            is_active BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""",
        """CREATE TABLE IF NOT EXISTS auth_sessions (
            session_id TEXT PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            csrf_token TEXT NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""",
        "CREATE INDEX IF NOT EXISTS idx_auth_sessions_expiry ON auth_sessions (expires_at)",
        """CREATE TABLE IF NOT EXISTS user_invites (
            token_hash TEXT PRIMARY KEY,
            email TEXT NOT NULL,
            role TEXT NOT NULL REFERENCES roles(name),
            expires_at TIMESTAMPTZ NOT NULL,
            accepted_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""",
        """CREATE TABLE IF NOT EXISTS auth_login_attempts (
            ip_address TEXT PRIMARY KEY,
            window_started_at TIMESTAMPTZ NOT NULL,
            failures INTEGER NOT NULL DEFAULT 0,
            locked_until TIMESTAMPTZ
        )""",
        """CREATE TABLE IF NOT EXISTS audit_log (
            id BIGSERIAL PRIMARY KEY,
            actor_user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
            actor_email TEXT,
            action TEXT NOT NULL,
            target_type TEXT,
            target_id TEXT,
            details JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""",
        "CREATE INDEX IF NOT EXISTS idx_audit_log_created ON audit_log (created_at DESC)",
    ]
    for statement in statements:
        op.execute(statement)


def downgrade():
    raise RuntimeError("Access and review data is retained; this migration is intentionally irreversible")
