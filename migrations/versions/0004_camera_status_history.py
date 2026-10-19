"""Record camera connectivity changes for status timeline reports.

Revision ID: 0004_camera_status_history
Revises: 0003_alert_outbox
"""
from alembic import op

revision = "0004_camera_status_history"
down_revision = "0003_alert_outbox"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """CREATE TABLE IF NOT EXISTS camera_status_events (
            id BIGSERIAL PRIMARY KEY,
            camera_id TEXT NOT NULL REFERENCES cameras(camera_id) ON DELETE CASCADE,
            connected BOOLEAN NOT NULL,
            observed_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )"""
    )
    op.execute(
        """CREATE INDEX IF NOT EXISTS idx_camera_status_events_camera_time
           ON camera_status_events (camera_id, observed_at DESC)"""
    )
    op.execute(
        """INSERT INTO camera_status_events (camera_id, connected, observed_at)
           SELECT status.camera_id, status.connected, status.last_updated
           FROM camera_status AS status
           WHERE status.connected IS NOT NULL
             AND NOT EXISTS (
                 SELECT 1 FROM camera_status_events AS event
                 WHERE event.camera_id = status.camera_id
             )"""
    )


def downgrade():
    raise RuntimeError("Camera connectivity history is retained; this migration is intentionally irreversible")
