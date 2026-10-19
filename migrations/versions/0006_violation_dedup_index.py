"""Index recent violations for persistent duplicate suppression.

Revision ID: 0006_violation_dedup_index
Revises: 0005_camera_status_samples
"""
from alembic import op

revision = "0006_violation_dedup_index"
down_revision = "0005_camera_status_samples"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """CREATE INDEX IF NOT EXISTS idx_violations_dedup
           ON violations (camera_id, track_id, violation_type, epoch_time DESC)"""
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_violations_dedup")
