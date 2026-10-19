"""Create the core camera, area, violation, and alert tables.

Revision ID: 0001_core
Revises:
"""
from alembic import op

revision = "0001_core"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    statements = [
        """CREATE TABLE IF NOT EXISTS area_groups (
            id BIGSERIAL PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            description TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""",
        """CREATE TABLE IF NOT EXISTS cameras (
            id BIGSERIAL PRIMARY KEY,
            camera_id TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            rtsp_url TEXT NOT NULL,
            area_group_id BIGINT REFERENCES area_groups(id) ON DELETE SET NULL,
            required_ppe TEXT[] NOT NULL DEFAULT '{helmet,vest}',
            enabled BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""",
        """CREATE TABLE IF NOT EXISTS violations (
            id BIGSERIAL PRIMARY KEY,
            camera_id TEXT NOT NULL,
            camera_name TEXT,
            zone TEXT,
            area_group_id BIGINT,
            area_group TEXT,
            track_id INTEGER,
            violation_type TEXT NOT NULL,
            confidence_note TEXT,
            snapshot_path TEXT,
            detected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            epoch_time DOUBLE PRECISION NOT NULL,
            alert_dispatched BOOLEAN DEFAULT false
        )""",
        "CREATE INDEX IF NOT EXISTS idx_violations_time ON violations (epoch_time DESC)",
        "CREATE INDEX IF NOT EXISTS idx_violations_camera ON violations (camera_id)",
        "CREATE INDEX IF NOT EXISTS idx_violations_type ON violations (violation_type)",
        "CREATE INDEX IF NOT EXISTS idx_violations_area_group ON violations (area_group_id)",
        """CREATE TABLE IF NOT EXISTS camera_status (
            camera_id TEXT PRIMARY KEY,
            camera_name TEXT,
            zone TEXT,
            connected BOOLEAN,
            last_frame_epoch DOUBLE PRECISION,
            last_updated TIMESTAMPTZ NOT NULL DEFAULT now()
        )""",
        """CREATE TABLE IF NOT EXISTS alert_rules (
            id BIGSERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            camera_id TEXT,
            zone TEXT,
            violation_type TEXT,
            min_severity INTEGER NOT NULL DEFAULT 1,
            channels TEXT[] NOT NULL DEFAULT '{teams}',
            cooldown_seconds INTEGER NOT NULL DEFAULT 300,
            email_recipients TEXT[] DEFAULT '{}',
            enabled BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""",
        """CREATE TABLE IF NOT EXISTS alert_dispatch_log (
            id BIGSERIAL PRIMARY KEY,
            violation_id BIGINT REFERENCES violations(id) ON DELETE CASCADE,
            rule_id BIGINT REFERENCES alert_rules(id) ON DELETE SET NULL,
            channel TEXT NOT NULL,
            success BOOLEAN NOT NULL,
            dispatched_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""",
    ]
    for statement in statements:
        op.execute(statement)


def downgrade():
    raise RuntimeError("Core data is retained; this migration is intentionally irreversible")
