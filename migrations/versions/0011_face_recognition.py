"""Add consented face enrollment, embeddings, matches, and import/job tracking.

Revision ID: 0011_face_recognition
Revises: 0010_camera_stream_urls
"""
from alembic import op

revision = "0011_face_recognition"
down_revision = "0010_camera_stream_urls"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """CREATE TABLE IF NOT EXISTS face_enrollments (
            person_id BIGSERIAL PRIMARY KEY,
            employee_code TEXT NOT NULL UNIQUE,
            full_name TEXT NOT NULL,
            department TEXT,
            consent_recorded BOOLEAN NOT NULL CHECK (consent_recorded),
            consent_basis TEXT NOT NULL,
            consent_date DATE,
            enrolled_by BIGINT REFERENCES users(id) ON DELETE SET NULL,
            enrolled_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            active BOOLEAN NOT NULL DEFAULT true,
            embedding_count INTEGER NOT NULL DEFAULT 0 CHECK (embedding_count >= 0),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )"""
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS face_embeddings (
            embedding_id BIGSERIAL PRIMARY KEY,
            person_id BIGINT NOT NULL
                REFERENCES face_enrollments(person_id) ON DELETE CASCADE,
            embedding BYTEA NOT NULL,
            source TEXT NOT NULL CHECK (
                source IN ('dataset import', 'reviewer-confirmed incident crop', 'manual')
            ),
            quality_score DOUBLE PRECISION NOT NULL
                CHECK (quality_score >= 0 AND quality_score <= 1),
            image_hash TEXT NOT NULL UNIQUE,
            photo_path TEXT,
            model_name TEXT NOT NULL,
            model_version TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            active BOOLEAN NOT NULL DEFAULT true
        )"""
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_face_embeddings_person_active "
        "ON face_embeddings (person_id) WHERE active"
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS incident_face_matches (
            incident_id BIGINT PRIMARY KEY
                REFERENCES violations(id) ON DELETE CASCADE,
            status TEXT NOT NULL CHECK (status IN (
                'Queued', 'Processing', 'Identified', 'Suggested',
                'Not identified', 'No face found', 'Error'
            )),
            matched_person_id BIGINT
                REFERENCES face_enrollments(person_id) ON DELETE SET NULL,
            similarity DOUBLE PRECISION,
            margin_to_second DOUBLE PRECISION,
            face_quality JSONB NOT NULL DEFAULT '{}'::jsonb,
            face_bbox JSONB,
            face_crop BYTEA,
            top3_candidates JSONB NOT NULL DEFAULT '[]'::jsonb,
            model_name TEXT,
            model_version TEXT,
            processed_at TIMESTAMPTZ,
            reviewed_by BIGINT REFERENCES users(id) ON DELETE SET NULL,
            review_decision TEXT CHECK (
                review_decision IN ('Confirmed', 'Rejected', 'Reassigned')
            ),
            reviewed_at TIMESTAMPTZ,
            review_comment TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )"""
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS face_recognition_jobs (
            incident_id BIGINT PRIMARY KEY
                REFERENCES violations(id) ON DELETE CASCADE,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'processing', 'complete', 'failed')),
            attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
            next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            locked_at TIMESTAMPTZ,
            last_error_code TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )"""
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_face_recognition_jobs_due "
        "ON face_recognition_jobs (next_attempt_at, created_at) "
        "WHERE status IN ('pending', 'processing')"
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS face_import_runs (
            id BIGSERIAL PRIMARY KEY,
            status TEXT NOT NULL CHECK (status IN ('completed', 'failed')),
            actor_user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
            summary JSONB NOT NULL DEFAULT '{}'::jsonb,
            report_path TEXT,
            started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )"""
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_face_import_runs_created "
        "ON face_import_runs (completed_at DESC)"
    )


def downgrade():
    raise RuntimeError(
        "Biometric enrollment and audit data is retained; "
        "this migration is intentionally irreversible"
    )
