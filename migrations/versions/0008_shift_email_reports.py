"""Persist shift-report schedules and idempotent delivery attempts."""
from alembic import op

revision = "0008_shift_email_reports"
down_revision = "0007_auth_username"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """CREATE TABLE IF NOT EXISTS shift_report_settings (
            id SMALLINT PRIMARY KEY CHECK (id = 1),
            settings JSONB NOT NULL,
            updated_by TEXT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )"""
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS shift_email_log (
            id BIGSERIAL PRIMARY KEY,
            shift_key TEXT NOT NULL UNIQUE,
            shift_name TEXT NOT NULL,
            starts_at TIMESTAMPTZ NOT NULL,
            ends_at TIMESTAMPTZ NOT NULL,
            recipients JSONB NOT NULL,
            timezone_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'sending', 'sent', 'failed', 'permanently_failed')),
            attempt_count INTEGER NOT NULL DEFAULT 0,
            next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_error TEXT,
            sent_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )"""
    )
    op.execute(
        """CREATE INDEX IF NOT EXISTS idx_shift_email_log_due
           ON shift_email_log (status, next_attempt_at, id)"""
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS shift_email_attempts (
            id BIGSERIAL PRIMARY KEY,
            report_id BIGINT NOT NULL REFERENCES shift_email_log(id) ON DELETE CASCADE,
            attempt_number INTEGER NOT NULL,
            status TEXT NOT NULL
                CHECK (status IN ('sending', 'sent', 'failed', 'interrupted')),
            error TEXT,
            started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            finished_at TIMESTAMPTZ,
            UNIQUE (report_id, attempt_number)
        )"""
    )


def downgrade():
    raise RuntimeError(
        "Shift report schedules and delivery audit history are retained; "
        "this migration is intentionally irreversible"
    )
