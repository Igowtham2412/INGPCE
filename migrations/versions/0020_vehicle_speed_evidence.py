"""Store snapshot and clip evidence for vehicle speed measurements."""
from alembic import op

revision = "0020_vehicle_speed_evidence"
down_revision = "0019_vehicle_speed_tracking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """ALTER TABLE vehicle_speed_measurements
           ADD COLUMN IF NOT EXISTS snapshot_path TEXT"""
    )
    op.execute(
        """ALTER TABLE vehicle_speed_measurements
           ADD COLUMN IF NOT EXISTS video_path TEXT"""
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE vehicle_speed_measurements DROP COLUMN IF EXISTS video_path"
    )
    op.execute(
        "ALTER TABLE vehicle_speed_measurements DROP COLUMN IF EXISTS snapshot_path"
    )
