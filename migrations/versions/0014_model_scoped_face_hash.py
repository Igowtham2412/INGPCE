"""Allow the same source photo to be enrolled for multiple face models.

Revision ID: 0014_model_scoped_face_hash
Revises: 0013_face_match_workflows
"""

from alembic import op

revision = "0014_model_scoped_face_hash"
down_revision = "0013_face_match_workflows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE face_embeddings "
        "DROP CONSTRAINT IF EXISTS face_embeddings_image_hash_key"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "idx_face_embeddings_model_image_hash "
        "ON face_embeddings (model_name, model_version, image_hash)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_face_embeddings_model_image_hash")
    op.execute(
        "ALTER TABLE face_embeddings "
        "ADD CONSTRAINT face_embeddings_image_hash_key UNIQUE (image_hash)"
    )
