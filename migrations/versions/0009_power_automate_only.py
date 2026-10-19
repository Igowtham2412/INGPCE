"""Route legacy alert channels through the Power Automate provider."""
from alembic import op

revision = "0009_power_automate_only"
down_revision = "0008_shift_email_reports"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """UPDATE alert_outbox AS legacy
           SET status = 'dead',
               last_error = 'Superseded by an existing Power Automate delivery',
               locked_at = NULL,
               next_attempt_at = now(),
               updated_at = now()
           WHERE legacy.channel IN ('teams', 'email')
             AND legacy.status IN ('pending', 'processing')
             AND EXISTS (
                 SELECT 1
                 FROM alert_outbox AS current
                 WHERE current.violation_id = legacy.violation_id
                   AND current.rule_id = legacy.rule_id
                   AND current.channel = 'power_automate'
             )"""
    )
    op.execute(
        """WITH ranked_legacy_jobs AS (
               SELECT id,
                      row_number() OVER (
                          PARTITION BY violation_id, rule_id
                          ORDER BY id
                      ) AS job_rank
               FROM alert_outbox
               WHERE channel IN ('teams', 'email')
                 AND status IN ('pending', 'processing')
           )
           UPDATE alert_outbox AS duplicate
           SET status = 'dead',
               last_error = 'Superseded by another legacy delivery during provider migration',
               locked_at = NULL,
               next_attempt_at = now(),
               updated_at = now()
           FROM ranked_legacy_jobs AS ranked
           WHERE duplicate.id = ranked.id
             AND ranked.job_rank > 1"""
    )
    op.execute(
        """UPDATE alert_outbox
           SET channel = 'power_automate',
               status = 'pending',
               locked_at = NULL,
               next_attempt_at = now(),
               updated_at = now()
           WHERE channel IN ('teams', 'email')
             AND status IN ('pending', 'processing')"""
    )
    op.execute(
        """UPDATE alert_rules AS rule
           SET channels = (
               SELECT COALESCE(
                   array_agg(DISTINCT CASE
                       WHEN channel IN ('teams', 'email') THEN 'power_automate'
                       ELSE channel
                   END),
                   ARRAY[]::text[]
               )
               FROM unnest(rule.channels) AS channel
           )
           WHERE rule.channels && ARRAY['teams', 'email']::text[]"""
    )


def downgrade():
    raise RuntimeError(
        "Power Automate alert routing cannot be safely mapped back to legacy providers"
    )
