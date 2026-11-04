"""Support multiple camera scopes per alert rule."""
from alembic import op

revision = "0016_alert_rule_camera_scopes"
down_revision = "0015_shift_digest"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """ALTER TABLE alert_rules
           ADD COLUMN IF NOT EXISTS camera_ids TEXT[] NOT NULL DEFAULT '{}'"""
    )
    op.execute(
        """UPDATE alert_rules
           SET camera_ids = ARRAY[camera_id]
           WHERE camera_id IS NOT NULL AND cardinality(camera_ids) = 0"""
    )


def downgrade() -> None:
    op.execute("ALTER TABLE alert_rules DROP COLUMN IF EXISTS camera_ids")
