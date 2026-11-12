"""Remember cameras explicitly deleted by an operator."""
from alembic import op

revision = "0018_camera_seed_exclusions"
down_revision = "0017_incident_face_people"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE IF NOT EXISTS camera_seed_exclusions (
            camera_id TEXT PRIMARY KEY,
            deleted_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )"""
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS camera_seed_exclusions")
