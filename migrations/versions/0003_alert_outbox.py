"""Add a persistent retryable alert delivery outbox.

Revision ID: 0003_alert_outbox
Revises: 0002_access
"""
from alembic import op

revision = "0003_alert_outbox"
down_revision = "0002_access"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """CREATE TABLE IF NOT EXISTS alert_outbox (
            id BIGSERIAL PRIMARY KEY,
            violation_id BIGINT NOT NULL REFERENCES violations(id) ON DELETE CASCADE,
            rule_id BIGINT NOT NULL REFERENCES alert_rules(id) ON DELETE CASCADE,
            channel TEXT NOT NULL,
            payload JSONB NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'processing', 'sent', 'dead')),
            attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
            next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            locked_at TIMESTAMPTZ,
            last_error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (violation_id, rule_id, channel)
        )"""
    )
    op.execute(
        """CREATE INDEX IF NOT EXISTS idx_alert_outbox_pending
           ON alert_outbox (next_attempt_at, created_at)
           WHERE status IN ('pending', 'processing')"""
    )


def downgrade():
    raise RuntimeError("Alert delivery history is retained; this migration is intentionally irreversible")
