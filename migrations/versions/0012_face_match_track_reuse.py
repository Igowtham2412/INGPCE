"""Store incident type and face-result track reuse provenance."""
from alembic import op

revision = "0012_face_match_track_reuse"
down_revision = "0011_face_recognition"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE incident_face_matches "
        "ADD COLUMN IF NOT EXISTS violation_type TEXT"
    )
    op.execute(
        """UPDATE incident_face_matches AS matches
           SET violation_type = violations.violation_type
           FROM violations
           WHERE violations.id = matches.incident_id
             AND matches.violation_type IS NULL"""
    )
    op.execute(
        "ALTER TABLE incident_face_matches "
        "ALTER COLUMN violation_type SET NOT NULL"
    )
    op.execute(
        "ALTER TABLE incident_face_matches "
        "ADD COLUMN IF NOT EXISTS reused_from_track BOOLEAN NOT NULL DEFAULT false"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_incident_face_matches_type_processed "
        "ON incident_face_matches (violation_type, processed_at DESC)"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_incident_face_matches_type_processed")
    op.execute(
        "ALTER TABLE incident_face_matches "
        "DROP COLUMN IF EXISTS reused_from_track"
    )
    op.execute(
        "ALTER TABLE incident_face_matches "
        "DROP COLUMN IF EXISTS violation_type"
    )
