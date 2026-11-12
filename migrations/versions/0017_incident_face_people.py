"""Store independently matched faces for each person in an incident."""
from alembic import op

revision = "0017_incident_face_people"
down_revision = "0016_alert_rule_camera_scopes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE IF NOT EXISTS incident_face_person_matches (
            incident_id BIGINT NOT NULL
                REFERENCES violations(id) ON DELETE CASCADE,
            person_track_id INTEGER NOT NULL,
            person_bbox JSONB NOT NULL,
            status TEXT NOT NULL CHECK (status IN (
                'Queued', 'Processing', 'MATCHED', 'NEEDS_REVIEW',
                'UNKNOWN', 'FACE_NOT_VISIBLE', 'Error'
            )),
            matched_person_id BIGINT
                REFERENCES face_enrollments(person_id) ON DELETE SET NULL,
            similarity DOUBLE PRECISION,
            margin_to_second DOUBLE PRECISION,
            face_quality JSONB NOT NULL DEFAULT '{}'::jsonb,
            face_bbox JSONB,
            face_crop BYTEA,
            top3_candidates JSONB NOT NULL DEFAULT '[]'::jsonb,
            top5_candidates JSONB NOT NULL DEFAULT '[]'::jsonb,
            model_name TEXT,
            model_version TEXT,
            processed_at TIMESTAMPTZ,
            reviewed_by BIGINT REFERENCES users(id) ON DELETE SET NULL,
            review_decision TEXT CHECK (
                review_decision IN ('Confirmed', 'Rejected', 'Reassigned', 'Unknown')
            ),
            reviewed_at TIMESTAMPTZ,
            review_comment TEXT,
            source TEXT NOT NULL DEFAULT 'live'
                CHECK (source IN ('live', 'historian')),
            thresholds JSONB NOT NULL DEFAULT '{}'::jsonb,
            violation_type TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (incident_id, person_track_id)
        )"""
    )
    op.execute(
        """CREATE INDEX IF NOT EXISTS idx_incident_face_person_matches_review
           ON incident_face_person_matches (status, processed_at DESC)"""
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS incident_face_person_matches")
