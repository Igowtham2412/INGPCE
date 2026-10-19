"""Store periodic camera status samples for honest uptime calculations.

Revision ID: 0005_camera_status_samples
Revises: 0004_camera_status_history
"""
from alembic import op

revision = "0005_camera_status_samples"
down_revision = "0004_camera_status_history"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """CREATE TABLE IF NOT EXISTS camera_status_samples (
            camera_id TEXT NOT NULL REFERENCES cameras(camera_id) ON DELETE CASCADE,
            observed_at TIMESTAMPTZ NOT NULL,
            connected BOOLEAN NOT NULL,
            PRIMARY KEY (camera_id, observed_at)
        )"""
    )
    op.execute(
        """CREATE INDEX IF NOT EXISTS idx_camera_status_samples_time
           ON camera_status_samples (observed_at DESC)"""
    )
    op.execute(
        """INSERT INTO camera_status_samples (camera_id, observed_at, connected)
           SELECT camera_id, date_trunc('minute', last_updated), connected
           FROM camera_status
           WHERE connected IS NOT NULL
           ON CONFLICT (camera_id, observed_at) DO NOTHING"""
    )


def downgrade():
    raise RuntimeError("Camera status samples are retained; this migration is intentionally irreversible")
