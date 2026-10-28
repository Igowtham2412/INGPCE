"""Add model-aware face results, editable thresholds, and historian jobs.

Revision ID: 0013_face_match_workflows
Revises: 0012_face_match_track_reuse
"""
from alembic import op

revision = "0013_face_match_workflows"
down_revision = "0012_face_match_track_reuse"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE face_enrollments "
        "ADD COLUMN IF NOT EXISTS designation TEXT"
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS face_historian_jobs (
            id BIGSERIAL PRIMARY KEY,
            requested_by BIGINT REFERENCES users(id) ON DELETE SET NULL,
            date_from TIMESTAMPTZ NOT NULL,
            date_to TIMESTAMPTZ NOT NULL,
            camera_ids TEXT[] NOT NULL DEFAULT '{}',
            violation_types TEXT[] NOT NULL,
            status TEXT NOT NULL DEFAULT 'running' CHECK (status IN (
                'running', 'paused', 'cancelled', 'completed', 'failed'
            )),
            total_count INTEGER NOT NULL DEFAULT 0 CHECK (total_count >= 0),
            processed_count INTEGER NOT NULL DEFAULT 0 CHECK (processed_count >= 0),
            matched_count INTEGER NOT NULL DEFAULT 0 CHECK (matched_count >= 0),
            needs_review_count INTEGER NOT NULL DEFAULT 0 CHECK (needs_review_count >= 0),
            unknown_count INTEGER NOT NULL DEFAULT 0 CHECK (unknown_count >= 0),
            not_visible_count INTEGER NOT NULL DEFAULT 0 CHECK (not_visible_count >= 0),
            failed_count INTEGER NOT NULL DEFAULT 0 CHECK (failed_count >= 0),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (date_to > date_from)
        )"""
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS face_historian_job_items (
            job_id BIGINT NOT NULL
                REFERENCES face_historian_jobs(id) ON DELETE CASCADE,
            incident_id BIGINT NOT NULL
                REFERENCES violations(id) ON DELETE CASCADE,
            status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
                'pending', 'processing', 'complete', 'failed', 'cancelled'
            )),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (job_id, incident_id)
        )"""
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_face_historian_items_pending "
        "ON face_historian_job_items (job_id, status, incident_id)"
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS face_match_settings (
            id BOOLEAN PRIMARY KEY DEFAULT true CHECK (id),
            high_threshold DOUBLE PRECISION NOT NULL
                CHECK (high_threshold >= -1 AND high_threshold <= 1),
            low_threshold DOUBLE PRECISION NOT NULL
                CHECK (low_threshold >= -1 AND low_threshold <= high_threshold),
            minimum_margin DOUBLE PRECISION NOT NULL
                CHECK (minimum_margin >= 0 AND minimum_margin <= 2),
            quality_min_by_violation JSONB NOT NULL DEFAULT '{}'::jsonb,
            updated_by BIGINT REFERENCES users(id) ON DELETE SET NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )"""
    )
    op.execute(
        "ALTER TABLE face_recognition_jobs "
        "ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'live'"
    )
    op.execute(
        "ALTER TABLE face_recognition_jobs "
        "ADD COLUMN IF NOT EXISTS historian_job_id BIGINT "
        "REFERENCES face_historian_jobs(id) ON DELETE SET NULL"
    )
    op.execute(
        "ALTER TABLE incident_face_matches "
        "ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'live'"
    )
    op.execute(
        "ALTER TABLE incident_face_matches "
        "ADD COLUMN IF NOT EXISTS thresholds JSONB NOT NULL DEFAULT '{}'::jsonb"
    )
    op.execute(
        "ALTER TABLE incident_face_matches "
        "ADD COLUMN IF NOT EXISTS top5_candidates JSONB NOT NULL DEFAULT '[]'::jsonb"
    )
    op.execute(
        "UPDATE incident_face_matches SET top5_candidates = top3_candidates"
    )
    op.execute(
        "ALTER TABLE incident_face_matches "
        "DROP CONSTRAINT IF EXISTS incident_face_matches_status_check"
    )
    op.execute(
        """UPDATE incident_face_matches SET status = CASE status
               WHEN 'Identified' THEN 'MATCHED'
               WHEN 'Suggested' THEN 'NEEDS_REVIEW'
               WHEN 'Not identified' THEN 'UNKNOWN'
               WHEN 'No face found' THEN 'FACE_NOT_VISIBLE'
               ELSE status
           END"""
    )
    op.execute(
        """ALTER TABLE incident_face_matches ADD CONSTRAINT
           incident_face_matches_status_check CHECK (status IN (
               'Queued', 'Processing', 'MATCHED', 'NEEDS_REVIEW',
               'UNKNOWN', 'FACE_NOT_VISIBLE', 'Error'
           ))"""
    )
    op.execute(
        """ALTER TABLE incident_face_matches ADD CONSTRAINT
           incident_face_matches_source_check CHECK (source IN ('live', 'historian'))"""
    )
    op.execute(
        "ALTER TABLE incident_face_matches "
        "DROP CONSTRAINT IF EXISTS incident_face_matches_review_decision_check"
    )
    op.execute(
        """ALTER TABLE incident_face_matches ADD CONSTRAINT
           incident_face_matches_review_decision_check CHECK (
               review_decision IN ('Confirmed', 'Rejected', 'Reassigned', 'Unknown')
               OR review_decision IS NULL
           )"""
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_face_historian_jobs_created "
        "ON face_historian_jobs (created_at DESC)"
    )


def downgrade():
    raise RuntimeError(
        "Face-match audit and historian job data is retained; "
        "this migration is intentionally irreversible"
    )
