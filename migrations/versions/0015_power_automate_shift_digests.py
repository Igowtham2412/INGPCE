"""Track idempotent Power Automate shift digest deliveries."""
from alembic import op

revision = "0015_shift_digest"
down_revision = "0014_model_scoped_face_hash"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE IF NOT EXISTS power_automate_shift_digest_log (
            id BIGSERIAL PRIMARY KEY,
            shift_key TEXT NOT NULL UNIQUE,
            shift_name TEXT NOT NULL,
            starts_at TIMESTAMPTZ NOT NULL,
            ends_at TIMESTAMPTZ NOT NULL,
            status TEXT NOT NULL DEFAULT 'sending'
                CHECK (status IN ('sending', 'sent', 'failed')),
            attempt_count INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            sent_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )"""
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS power_automate_shift_digest_log")
