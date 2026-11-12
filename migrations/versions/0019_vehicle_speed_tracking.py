"""Persist calibrated vehicle speed settings and measurements."""
from alembic import op

revision = "0019_vehicle_speed_tracking"
down_revision = "0018_camera_seed_exclusions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE IF NOT EXISTS camera_speed_settings (
            camera_id TEXT PRIMARY KEY
                REFERENCES cameras(camera_id) ON DELETE CASCADE,
            distance_m DOUBLE PRECISION NOT NULL CHECK (distance_m > 0),
            speed_limit_kmh DOUBLE PRECISION NOT NULL
                CHECK (speed_limit_kmh > 0),
            gate_a JSONB NOT NULL,
            gate_b JSONB NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT true,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )"""
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS vehicle_speed_measurements (
            id BIGSERIAL PRIMARY KEY,
            camera_id TEXT NOT NULL
                REFERENCES cameras(camera_id) ON DELETE CASCADE,
            track_id INTEGER NOT NULL,
            vehicle_type TEXT NOT NULL,
            speed_kmh DOUBLE PRECISION NOT NULL CHECK (speed_kmh > 0),
            speed_limit_kmh DOUBLE PRECISION NOT NULL
                CHECK (speed_limit_kmh > 0),
            speeding BOOLEAN NOT NULL,
            direction TEXT NOT NULL,
            measured_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )"""
    )
    op.execute(
        """CREATE INDEX IF NOT EXISTS idx_vehicle_speed_measurements_camera_time
           ON vehicle_speed_measurements (camera_id, measured_at DESC)"""
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS vehicle_speed_measurements")
    op.execute("DROP TABLE IF EXISTS camera_speed_settings")
