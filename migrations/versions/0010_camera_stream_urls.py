"""Add per-camera main and sub-stream URLs.

Revision ID: 0010_camera_stream_urls
Revises: 0009_power_automate_only
"""
from alembic import op

revision = "0010_camera_stream_urls"
down_revision = "0009_power_automate_only"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE cameras ADD COLUMN IF NOT EXISTS main_stream_url TEXT")
    op.execute("ALTER TABLE cameras ADD COLUMN IF NOT EXISTS sub_stream_url TEXT")
    op.execute(
        """UPDATE cameras
           SET main_stream_url = COALESCE(
                   main_stream_url,
                   CASE
                       WHEN rtsp_url ~ '/Streaming/Channels/[0-9]+02([/?#]|$)'
                       THEN regexp_replace(
                           rtsp_url,
                           '(/Streaming/Channels/[0-9]+)02([/?#]|$)',
                           E'\\\\1' || '01' || E'\\\\2'
                       )
                       ELSE rtsp_url
                   END
               ),
               sub_stream_url = COALESCE(
                   sub_stream_url,
                   CASE
                       WHEN rtsp_url ~ '/Streaming/Channels/[0-9]+01([/?#]|$)'
                       THEN regexp_replace(
                           rtsp_url,
                           '(/Streaming/Channels/[0-9]+)01([/?#]|$)',
                           E'\\\\1' || '02' || E'\\\\2'
                       )
                       ELSE rtsp_url
                   END
               )
           WHERE main_stream_url IS NULL OR sub_stream_url IS NULL"""
    )
    op.execute("ALTER TABLE cameras ALTER COLUMN main_stream_url SET NOT NULL")
    op.execute("ALTER TABLE cameras ALTER COLUMN sub_stream_url SET NOT NULL")


def downgrade():
    op.execute("ALTER TABLE cameras DROP COLUMN IF EXISTS sub_stream_url")
    op.execute("ALTER TABLE cameras DROP COLUMN IF EXISTS main_stream_url")
