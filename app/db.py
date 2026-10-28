"""
db.py — async PostgreSQL data layer (asyncpg, connection-pooled).

`sync_seed_data()` is the fix for cameras silently missing from the
frontend: it runs on EVERY app startup AND is exposed via
POST /api/cameras/sync-seed (a dashboard button), and works per-camera:
  - ensures every area group in SEED_AREA_GROUPS exists (create if missing)
  - creates missing configured cameras without overwriting existing stream URLs
  - disables retired seed cameras without deleting their incident history
This makes seeding idempotent while preserving operator-managed camera
settings and records for existing cameras.
"""
import asyncio
import json
import logging
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import asyncpg

from . import config

logger = logging.getLogger("ppe.db")


def _seed_credentials_refresh(
    existing_url: str | None, configured_url: str | None,
) -> str | None:
    if not existing_url or not configured_url:
        return None
    existing = urlsplit(existing_url)
    configured = urlsplit(configured_url)
    same_endpoint = (
        existing.scheme.lower(),
        (existing.hostname or "").lower(),
        existing.port,
        existing.path,
        existing.query,
    ) == (
        configured.scheme.lower(),
        (configured.hostname or "").lower(),
        configured.port,
        configured.path,
        configured.query,
    )
    if (
        not same_endpoint
        or configured.username is None
        or not configured.password
        or (
            existing.username == configured.username
            and existing.password == configured.password
        )
    ):
        return None
    return configured_url


SCHEMA = """
CREATE TABLE IF NOT EXISTS area_groups (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS cameras (
    id BIGSERIAL PRIMARY KEY,
    camera_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    rtsp_url TEXT NOT NULL,
    main_stream_url TEXT NOT NULL,
    sub_stream_url TEXT NOT NULL,
    area_group_id BIGINT REFERENCES area_groups(id) ON DELETE SET NULL,
    required_ppe TEXT[] NOT NULL DEFAULT '{helmet,vest}',
    enabled BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE cameras ADD COLUMN IF NOT EXISTS main_stream_url TEXT;
ALTER TABLE cameras ADD COLUMN IF NOT EXISTS sub_stream_url TEXT;
UPDATE cameras
SET main_stream_url = COALESCE(main_stream_url, rtsp_url),
    sub_stream_url = COALESCE(sub_stream_url, rtsp_url)
WHERE main_stream_url IS NULL OR sub_stream_url IS NULL;
ALTER TABLE cameras ALTER COLUMN main_stream_url SET NOT NULL;
ALTER TABLE cameras ALTER COLUMN sub_stream_url SET NOT NULL;

CREATE TABLE IF NOT EXISTS violations (
    id BIGSERIAL PRIMARY KEY,
    camera_id TEXT NOT NULL,
    camera_name TEXT,
    zone TEXT,
    area_group_id BIGINT,
    area_group TEXT,
    track_id INTEGER,
    violation_type TEXT NOT NULL,
    confidence_note TEXT,
    snapshot_path TEXT,
    detected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    epoch_time DOUBLE PRECISION NOT NULL,
    alert_dispatched BOOLEAN DEFAULT false
);
ALTER TABLE violations ADD COLUMN IF NOT EXISTS clean_snapshot_path TEXT;
ALTER TABLE violations ADD COLUMN IF NOT EXISTS video_path TEXT;
ALTER TABLE violations ADD COLUMN IF NOT EXISTS review_status TEXT NOT NULL DEFAULT 'unreviewed';
ALTER TABLE violations ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ;
ALTER TABLE violations ADD COLUMN IF NOT EXISTS reviewed_by TEXT;
ALTER TABLE violations ADD COLUMN IF NOT EXISTS review_comment TEXT;
ALTER TABLE violations ADD COLUMN IF NOT EXISTS detection_metadata JSONB NOT NULL DEFAULT '[]'::jsonb;
CREATE INDEX IF NOT EXISTS idx_violations_time ON violations (epoch_time DESC);
CREATE INDEX IF NOT EXISTS idx_violations_camera ON violations (camera_id);
CREATE INDEX IF NOT EXISTS idx_violations_type ON violations (violation_type);
CREATE INDEX IF NOT EXISTS idx_violations_area_group ON violations (area_group_id);
CREATE INDEX IF NOT EXISTS idx_violations_dedup
    ON violations (camera_id, track_id, violation_type, epoch_time DESC);

CREATE TABLE IF NOT EXISTS camera_status (
    camera_id TEXT PRIMARY KEY,
    camera_name TEXT,
    zone TEXT,
    connected BOOLEAN,
    last_frame_epoch DOUBLE PRECISION,
    last_updated TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS alert_rules (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    camera_id TEXT,
    zone TEXT,
    violation_type TEXT,
    min_severity INTEGER NOT NULL DEFAULT 1,
    channels TEXT[] NOT NULL DEFAULT '{teams}',
    cooldown_seconds INTEGER NOT NULL DEFAULT 300,
    email_recipients TEXT[] DEFAULT '{}',
    enabled BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS alert_dispatch_log (
    id BIGSERIAL PRIMARY KEY,
    violation_id BIGINT REFERENCES violations(id) ON DELETE CASCADE,
    rule_id BIGINT REFERENCES alert_rules(id) ON DELETE SET NULL,
    channel TEXT NOT NULL,
    success BOOLEAN NOT NULL,
    dispatched_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS violation_review_log (
    id BIGSERIAL PRIMARY KEY,
    violation_id BIGINT NOT NULL REFERENCES violations(id) ON DELETE CASCADE,
    review_status TEXT NOT NULL CHECK (review_status IN ('confirmed', 'false_positive')),
    reviewed_by TEXT,
    reviewed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    dataset_image_path TEXT NOT NULL,
    dataset_label_path TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS roles (
    name TEXT PRIMARY KEY,
    description TEXT NOT NULL
);
INSERT INTO roles (name, description) VALUES
    ('admin', 'Full system administration'),
    ('manager', 'Manage cameras, alert rules, and operations'),
    ('operator', 'Review incidents and operate cameras'),
    ('viewer', 'Read-only access')
ON CONFLICT (name) DO NOTHING;

CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    username TEXT,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL REFERENCES roles(name),
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS auth_sessions (
    session_id TEXT PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf_token TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_expiry ON auth_sessions (expires_at);

CREATE TABLE IF NOT EXISTS user_invites (
    token_hash TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    role TEXT NOT NULL REFERENCES roles(name),
    expires_at TIMESTAMPTZ NOT NULL,
    accepted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS auth_login_attempts (
    ip_address TEXT PRIMARY KEY,
    window_started_at TIMESTAMPTZ NOT NULL,
    failures INTEGER NOT NULL DEFAULT 0,
    locked_until TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS audit_log (
    id BIGSERIAL PRIMARY KEY,
    actor_user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
    actor_email TEXT,
    action TEXT NOT NULL,
    target_type TEXT,
    target_id TEXT,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_audit_log_created ON audit_log (created_at DESC);
"""

_pool: asyncpg.Pool | None = None


def _run_schema_migrations() -> None:
    from alembic import command
    from alembic.config import Config as AlembicConfig
    from sqlalchemy.engine import URL

    project_root = Path(__file__).resolve().parent.parent
    alembic_config = AlembicConfig(str(project_root / "alembic.ini"))
    url = URL.create(
        "postgresql+psycopg2",
        username=config.PG_USER,
        password=config.PG_PASSWORD,
        host=config.PG_HOST,
        port=config.PG_PORT,
        database=config.PG_DB,
    ).render_as_string(hide_password=False)
    alembic_config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(alembic_config, "head")


def _print_flush(msg: str):
    print(msg, file=sys.stderr, flush=True)


async def init_pool():
    global _pool
    deadline = time.time() + config.PG_CONNECT_RETRY_SECONDS
    attempt = 0
    last_error = None

    while time.time() < deadline:
        attempt += 1
        try:
            await asyncio.to_thread(_run_schema_migrations)
            _pool = await asyncpg.create_pool(
                host=config.PG_HOST, port=config.PG_PORT, database=config.PG_DB,
                user=config.PG_USER, password=config.PG_PASSWORD,
                min_size=config.PG_POOL_MIN, max_size=config.PG_POOL_MAX,
                timeout=10,
            )
            async with _pool.acquire() as conn:
                await conn.execute(
                    """DELETE FROM auth_sessions WHERE expires_at <= now();
                       DELETE FROM user_invites
                       WHERE expires_at <= now() OR accepted_at IS NOT NULL"""
                )
            _print_flush(f"[ppe.db] PostgreSQL pool ready ({config.PG_HOST}:{config.PG_PORT}/{config.PG_DB}) "
                         f"pool_size={config.PG_POOL_MIN}-{config.PG_POOL_MAX} after {attempt} attempt(s)")
            result = await sync_seed_data()
            _print_flush(f"[ppe.db] Seed sync: {result['groups_created']} area group(s) created, "
                         f"{result['cameras_created']} camera(s) created, "
                         f"{result['cameras_updated']} feed(s) updated, "
                         f"{result['cameras_disabled']} retired camera(s) disabled, "
                         f"{result['cameras_already_existed']} already existed")
            return _pool
        except Exception as e:
            last_error = e
            _print_flush(
                f"[ppe.db] [attempt {attempt}] Could not connect to PostgreSQL at "
                f"{config.PG_HOST}:{config.PG_PORT}/{config.PG_DB} - {type(e).__name__}: {e}. "
                f"Retrying in 3s... (giving up after {config.PG_CONNECT_RETRY_SECONDS}s total)"
            )
            await asyncio.sleep(3)

    error_msg = (
        f"\n\n================ DATABASE CONNECTION FAILED ================\n"
        f"Could not connect to PostgreSQL after {attempt} attempts over "
        f"{config.PG_CONNECT_RETRY_SECONDS} seconds.\n"
        f"  Host: {config.PG_HOST}  Port: {config.PG_PORT}  DB: {config.PG_DB}  User: {config.PG_USER}\n"
        f"Last error: {type(last_error).__name__}: {last_error}\n"
        f"==============================================================\n"
    )
    _print_flush(error_msg)
    raise RuntimeError(error_msg) from last_error


async def close_pool():
    if _pool:
        await _pool.close()


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized - call init_pool() at startup")
    return _pool


async def sync_seed_data() -> dict[str, Any]:
    """Add missing configured cameras and safely retire removed seed cameras."""
    groups_created = 0
    cameras_created = 0
    cameras_updated = 0
    cameras_disabled = 0
    updated_camera_ids: list[str] = []
    disabled_camera_ids: list[str] = []

    existing_groups = await list_area_groups()
    group_name_to_id = {g["name"]: g["id"] for g in existing_groups}

    for group in config.SEED_AREA_GROUPS:
        if group["name"] not in group_name_to_id:
            row = await create_area_group(group["name"], group.get("description", ""))
            group_name_to_id[group["name"]] = row["id"]
            groups_created += 1

    existing_cameras = await list_cameras()
    cameras_by_id = {c["camera_id"]: c for c in existing_cameras}
    newly_created_camera_ids: list[str] = []

    for cam in config.SEED_CAMERAS:
        camera_id = cam["camera_id"]
        existing = cameras_by_id.get(camera_id)
        if existing:
            configured_main = cam.get("main_stream_url")
            configured_sub = cam.get("sub_stream_url")
            main_refresh = _seed_credentials_refresh(
                existing.get("main_stream_url") or existing.get("rtsp_url"),
                configured_main,
            )
            sub_refresh = _seed_credentials_refresh(
                existing.get("sub_stream_url"),
                configured_sub,
            )
            if main_refresh and sub_refresh:
                await update_camera(
                    camera_id,
                    rtsp_url=main_refresh,
                    main_stream_url=main_refresh,
                    sub_stream_url=sub_refresh,
                )
                cameras_updated += 1
                updated_camera_ids.append(camera_id)

            required_ppe = list(existing.get("required_ppe") or [])
            if (
                "arc_flash_suit" in cam.get("required_ppe", [])
                and "arc_flash_suit" not in required_ppe
            ):
                required_ppe.append("arc_flash_suit")
                await update_camera(camera_id, required_ppe=required_ppe)
                cameras_updated += 1
                if camera_id not in updated_camera_ids:
                    updated_camera_ids.append(camera_id)
            if "main_stream_url" not in cam and "sub_stream_url" not in cam:
                if existing.get("rtsp_url") != cam["rtsp_url"]:
                    await update_camera(camera_id, rtsp_url=cam["rtsp_url"])
                    cameras_updated += 1
                    if camera_id not in updated_camera_ids:
                        updated_camera_ids.append(camera_id)
            continue

        inferred_main_url, inferred_sub_url = config.camera_stream_urls_for(
            cam["rtsp_url"],
        )
        main_stream_url = cam.get("main_stream_url", inferred_main_url)
        sub_stream_url = cam.get(
            "sub_stream_url",
            config.sub_stream_url_for(main_stream_url)
            if "main_stream_url" in cam else inferred_sub_url,
        )
        area_group_id = group_name_to_id.get(cam.get("area_group_name"))
        camera_args = (
            camera_id, cam["name"],
            main_stream_url if (
                "main_stream_url" in cam or "sub_stream_url" in cam
            ) else cam["rtsp_url"],
            area_group_id,
            cam.get("required_ppe", ["helmet", "vest"]),
        )
        if "main_stream_url" in cam or "sub_stream_url" in cam:
            await create_camera(
                *camera_args,
                main_stream_url=main_stream_url,
                sub_stream_url=sub_stream_url,
            )
        else:
            await create_camera(*camera_args)
        cameras_created += 1
        newly_created_camera_ids.append(camera_id)
        cameras_by_id[camera_id] = {"camera_id": camera_id}

    configured_ids = {cam["camera_id"] for cam in config.SEED_CAMERAS}
    for camera_id in config.RETIRED_SEED_CAMERA_IDS:
        existing = cameras_by_id.get(camera_id)
        if existing and existing.get("enabled", True) and camera_id not in configured_ids:
            await update_camera(camera_id, enabled=False)
            cameras_disabled += 1
            disabled_camera_ids.append(camera_id)

    return {
        "groups_created": groups_created,
        "cameras_created": cameras_created,
        "cameras_updated": cameras_updated,
        "cameras_disabled": cameras_disabled,
        "cameras_already_existed": len(cameras_by_id) - cameras_created,
        "updated_camera_ids": updated_camera_ids,
        "disabled_camera_ids": disabled_camera_ids,
        "newly_created_camera_ids": newly_created_camera_ids,
    }


async def list_area_groups() -> list[dict[str, Any]]:
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT ag.*, COUNT(c.id) as camera_count
               FROM area_groups ag
               LEFT JOIN cameras c ON c.area_group_id = ag.id
               GROUP BY ag.id ORDER BY ag.name ASC"""
        )
        return [dict(r) for r in rows]


async def create_area_group(name: str, description: str = "") -> dict[str, Any]:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO area_groups (name, description) VALUES ($1,$2) RETURNING *",
            name, description,
        )
        return dict(row)


async def update_area_group(group_id: int, **fields) -> dict[str, Any] | None:
    if not fields:
        return None
    set_clauses, params = [], []
    for key, value in fields.items():
        params.append(value)
        set_clauses.append(f"{key} = ${len(params)}")
    params.append(group_id)
    query = f"UPDATE area_groups SET {', '.join(set_clauses)} WHERE id = ${len(params)} RETURNING *"
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(query, *params)
        return dict(row) if row else None


async def delete_area_group(group_id: int) -> bool:
    async with get_pool().acquire() as conn:
        result = await conn.execute("DELETE FROM area_groups WHERE id = $1", group_id)
        return result.endswith("1")


async def list_cameras() -> list[dict[str, Any]]:
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT c.*, ag.name as area_group_name
               FROM cameras c
               LEFT JOIN area_groups ag ON ag.id = c.area_group_id
               ORDER BY c.camera_id ASC"""
        )
        return [dict(r) for r in rows]


async def list_camera_zones(camera_id: str, enabled_only: bool = True) -> list[dict[str, Any]]:
    query = "SELECT * FROM camera_zones WHERE camera_id = $1"
    if enabled_only:
        query += " AND enabled = true"
    query += " ORDER BY id"
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(query, camera_id)
        zones = [dict(row) for row in rows]
        for zone in zones:
            if isinstance(zone["polygon"], str):
                zone["polygon"] = json.loads(zone["polygon"])
        return zones


async def create_camera_zone(camera_id: str, name: str,
                             polygon: list[list[float]], enabled: bool = True) -> dict[str, Any]:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO camera_zones (camera_id, name, polygon, enabled)
               VALUES ($1, $2, $3::jsonb, $4) RETURNING *""",
            camera_id, name, json.dumps(polygon), enabled,
        )
        zone = dict(row)
        if isinstance(zone["polygon"], str):
            zone["polygon"] = json.loads(zone["polygon"])
        return zone


async def update_camera_zone(zone_id: int, camera_id: str, **fields) -> dict[str, Any] | None:
    if not fields:
        return None
    set_clauses, params = [], []
    for key, value in fields.items():
        if key not in {"name", "polygon", "enabled"}:
            raise ValueError(f"Unsupported zone field: {key}")
        if key == "polygon":
            value = json.dumps(value)
        params.append(value)
        cast = "::jsonb" if key == "polygon" else ""
        set_clauses.append(f"{key} = ${len(params)}{cast}")
    params.extend((zone_id, camera_id))
    query = (
        f"UPDATE camera_zones SET {', '.join(set_clauses)}, updated_at = now() "
        f"WHERE id = ${len(params) - 1} AND camera_id = ${len(params)} RETURNING *"
    )
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(query, *params)
        if row is None:
            return None
        zone = dict(row)
        if isinstance(zone["polygon"], str):
            zone["polygon"] = json.loads(zone["polygon"])
        return zone


async def delete_camera_zone(zone_id: int, camera_id: str) -> bool:
    async with get_pool().acquire() as conn:
        result = await conn.execute(
            "DELETE FROM camera_zones WHERE id = $1 AND camera_id = $2",
            zone_id, camera_id,
        )
        return result.endswith("1")


async def get_camera(camera_id: str) -> dict[str, Any] | None:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """SELECT c.*, ag.name as area_group_name
               FROM cameras c
               LEFT JOIN area_groups ag ON ag.id = c.area_group_id
               WHERE c.camera_id = $1""",
            camera_id,
        )
        return dict(row) if row else None


async def create_camera(camera_id: str, name: str, rtsp_url: str,
                         area_group_id: int | None, required_ppe: list[str],
                         enabled: bool = True,
                         main_stream_url: str | None = None,
                         sub_stream_url: str | None = None) -> dict[str, Any]:
    if main_stream_url is None:
        main_stream_url, inferred_sub_url = config.camera_stream_urls_for(rtsp_url)
    else:
        inferred_sub_url = config.sub_stream_url_for(main_stream_url)
    sub_stream_url = sub_stream_url or inferred_sub_url
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO cameras (
                   camera_id, name, rtsp_url, main_stream_url, sub_stream_url,
                   area_group_id, required_ppe, enabled
               )
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8) RETURNING *""",
            camera_id, name, rtsp_url, main_stream_url, sub_stream_url,
            area_group_id, required_ppe, enabled,
        )
        return dict(row)


async def update_camera(camera_id: str, **fields) -> dict[str, Any] | None:
    if not fields:
        return None
    if "rtsp_url" in fields and not {
        "main_stream_url", "sub_stream_url",
    }.intersection(fields):
        main_stream_url, sub_stream_url = config.camera_stream_urls_for(
            fields["rtsp_url"],
        )
        fields = {
            **fields,
            "main_stream_url": main_stream_url,
            "sub_stream_url": sub_stream_url,
        }
    set_clauses, params = [], []
    for key, value in fields.items():
        params.append(value)
        set_clauses.append(f"{key} = ${len(params)}")
    params.append(camera_id)
    query = f"UPDATE cameras SET {', '.join(set_clauses)}, updated_at = now() WHERE camera_id = ${len(params)} RETURNING *"
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(query, *params)
        return dict(row) if row else None


async def delete_camera(camera_id: str) -> bool:
    async with get_pool().acquire() as conn:
        result = await conn.execute("DELETE FROM cameras WHERE camera_id = $1", camera_id)
        return result.endswith("1")


async def log_violation(camera_id: str, camera_name: str, zone: str, track_id: int,
                         violation_type: str, snapshot_path: str | None = None,
                         area_group_id: int | None = None, area_group: str | None = None,
                         clean_snapshot_path: str | None = None,
                         detection_metadata: dict[str, Any] | None = None,
                         cooldown_seconds: float = config.VIOLATION_COOLDOWN_SECONDS) -> int | None:
    now = time.time()
    async with get_pool().acquire() as conn:
        async with conn.transaction():
            dedup_key = f"{camera_id}\x1f{track_id}\x1f{violation_type}"
            await conn.fetchval(
                "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                dedup_key,
            )
            existing_id = await conn.fetchval(
                """SELECT id FROM violations
                   WHERE camera_id = $1 AND track_id = $2 AND violation_type = $3
                     AND epoch_time >= $4
                   ORDER BY epoch_time DESC LIMIT 1""",
                camera_id, track_id, violation_type, now - max(0.0, cooldown_seconds),
            )
            if existing_id is not None:
                return None
            row = await conn.fetchrow(
                """INSERT INTO violations
                   (camera_id, camera_name, zone, area_group_id, area_group, track_id,
                    violation_type, snapshot_path, epoch_time, clean_snapshot_path, detection_metadata)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb) RETURNING id""",
                camera_id, camera_name, zone, area_group_id, area_group, track_id,
                violation_type, snapshot_path, now, clean_snapshot_path,
                json.dumps(detection_metadata or []),
            )
            return row["id"]


async def review_violation(violation_id: int, review_status: str,
                           reviewed_by: str | None, review_comment: str | None,
                           dataset_image_path: str, dataset_label_path: str) -> dict[str, Any] | None:
    if review_status not in {"confirmed", "false_positive"}:
        raise ValueError("review_status must be confirmed or false_positive")
    async with get_pool().acquire() as conn:
        async with conn.transaction():
            previous_feedback = await conn.fetch(
                """SELECT dataset_image_path, dataset_label_path
                   FROM violation_review_log WHERE violation_id = $1""",
                violation_id,
            )
            row = await conn.fetchrow(
                """UPDATE violations
                   SET review_status = $2, reviewed_at = now(), reviewed_by = $3, review_comment = $4
                   WHERE id = $1 RETURNING *""",
                violation_id, review_status, reviewed_by, review_comment,
            )
            if row is None:
                return None
            await conn.execute(
                """INSERT INTO violation_review_log
                   (violation_id, review_status, reviewed_by, dataset_image_path, dataset_label_path)
                   VALUES ($1,$2,$3,$4,$5)""",
                violation_id, review_status, reviewed_by, dataset_image_path, dataset_label_path,
            )
            result = dict(row)
            result["_superseded_feedback_paths"] = [
                path
                for feedback_row in previous_feedback
                for path in (
                    feedback_row["dataset_image_path"],
                    feedback_row["dataset_label_path"],
                )
            ]
            return result


async def update_violation_video_path(violation_id: int, video_path: str) -> bool:
    async with get_pool().acquire() as conn:
        result = await conn.execute(
            "UPDATE violations SET video_path = $2 WHERE id = $1",
            violation_id, video_path,
        )
        return result.endswith("1")


async def get_user_by_login(login: str) -> dict[str, Any] | None:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """SELECT id, email, username, password_hash, role, is_active
               FROM users WHERE lower(email) = lower($1)
                  OR lower(username) = lower($1)
               ORDER BY CASE WHEN lower(email) = lower($1) THEN 0 ELSE 1 END
               LIMIT 1""",
            login,
        )
        return dict(row) if row else None


async def set_username(user_id: int, username: str) -> dict[str, Any] | None:
    normalized = username.strip().lower()
    if not normalized or len(normalized) > 64:
        raise ValueError("username must contain between 1 and 64 characters")
    async with get_pool().acquire() as conn, conn.transaction():
        conflicting_email = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM users WHERE lower(email) = $1 AND id <> $2)",
            normalized, user_id,
        )
        if conflicting_email:
            raise ValueError("username conflicts with an existing user's email")
        row = await conn.fetchrow(
            """UPDATE users SET username = $1, updated_at = now()
                   WHERE id = $2 RETURNING id, email, username, role, is_active""",
            normalized, user_id,
        )
        return dict(row) if row else None


async def update_user_password(user_id: int, password_hash: str) -> bool:
    async with get_pool().acquire() as conn, conn.transaction():
        result = await conn.execute(
            "UPDATE users SET password_hash = $2, updated_at = now() WHERE id = $1",
            user_id, password_hash,
        )
        if not result.endswith("1"):
            return False
        await conn.execute("DELETE FROM auth_sessions WHERE user_id = $1", user_id)
        return True


async def create_user(email: str, password_hash: str, role: str) -> dict[str, Any]:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO users (email, password_hash, role)
               VALUES (lower($1), $2, $3)
               RETURNING id, email, username, role, is_active, created_at""",
            email, password_hash, role,
        )
        return dict(row)


async def list_users() -> list[dict[str, Any]]:
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, email, username, role, is_active, created_at FROM users ORDER BY email"
        )
        return [dict(row) for row in rows]


async def set_user_active(user_id: int, active: bool) -> dict[str, Any] | None:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """UPDATE users SET is_active = $2, updated_at = now()
               WHERE id = $1 RETURNING id, email, username, role, is_active""",
            user_id, active,
        )
        return dict(row) if row else None


async def create_auth_session(session_id: str, user_id: int, csrf_token: str,
                              expires_at) -> None:
    async with get_pool().acquire() as conn:
        await conn.execute(
            """INSERT INTO auth_sessions (session_id, user_id, csrf_token, expires_at)
               VALUES ($1,$2,$3,$4)""",
            session_id, user_id, csrf_token, expires_at,
        )


async def get_auth_session(session_id: str, expires_at) -> dict[str, Any] | None:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """SELECT s.session_id, s.csrf_token, s.expires_at,
                      u.id AS user_id, u.email, u.username, u.role
               FROM auth_sessions s JOIN users u ON u.id = s.user_id
               WHERE s.session_id = $1 AND s.expires_at > now() AND u.is_active = true""",
            session_id,
        )
        if row is None or row["expires_at"].timestamp() != expires_at:
            return None
        return dict(row)


async def delete_auth_session(session_id: str) -> None:
    async with get_pool().acquire() as conn:
        await conn.execute("DELETE FROM auth_sessions WHERE session_id = $1", session_id)


async def login_lock_expiry(ip_address: str):
    async with get_pool().acquire() as conn:
        return await conn.fetchval(
            "SELECT locked_until FROM auth_login_attempts WHERE ip_address = $1 AND locked_until > now()",
            ip_address,
        )


async def record_login_failure(ip_address: str) -> None:
    async with get_pool().acquire() as conn:
        await conn.execute(
            """INSERT INTO auth_login_attempts
                   (ip_address, window_started_at, failures, locked_until)
               VALUES ($1, now(), 1, NULL)
               ON CONFLICT (ip_address) DO UPDATE SET
                   failures = CASE
                       WHEN auth_login_attempts.window_started_at < now() - interval '15 minutes'
                           THEN 1
                       ELSE auth_login_attempts.failures + 1
                   END,
                   locked_until = CASE
                       WHEN auth_login_attempts.window_started_at < now() - interval '15 minutes'
                           THEN NULL
                       WHEN auth_login_attempts.failures + 1 >= 5
                           THEN now() + interval '15 minutes'
                       ELSE auth_login_attempts.locked_until
                   END,
                   window_started_at = CASE
                       WHEN auth_login_attempts.window_started_at < now() - interval '15 minutes'
                           THEN now()
                       ELSE auth_login_attempts.window_started_at
                   END""",
            ip_address,
        )


async def clear_login_failures(ip_address: str) -> None:
    async with get_pool().acquire() as conn:
        await conn.execute("DELETE FROM auth_login_attempts WHERE ip_address = $1", ip_address)


async def create_user_invite(token_hash: str, email: str, role: str, expires_at) -> None:
    async with get_pool().acquire() as conn:
        await conn.execute(
            """INSERT INTO user_invites (token_hash, email, role, expires_at)
               VALUES ($1, lower($2), $3, $4)""",
            token_hash, email, role, expires_at,
        )


async def accept_user_invite(token_hash: str, password_hash: str) -> dict[str, Any] | None:
    async with get_pool().acquire() as conn, conn.transaction():
        invite = await conn.fetchrow(
            """UPDATE user_invites SET accepted_at = now()
                   WHERE token_hash = $1 AND accepted_at IS NULL AND expires_at > now()
                   RETURNING email, role""",
            token_hash,
        )
        if invite is None:
            return None
        row = await conn.fetchrow(
            """INSERT INTO users (email, password_hash, role)
                   VALUES ($1, $2, $3)
                   RETURNING id, email, role, is_active""",
            invite["email"], password_hash, invite["role"],
        )
        return dict(row)


async def write_audit_log(action: str, actor_user_id: int | None = None,
                          actor_email: str | None = None,
                          target_type: str | None = None,
                          target_id: str | None = None,
                          details: dict[str, Any] | None = None) -> None:
    async with get_pool().acquire() as conn:
        await conn.execute(
            """INSERT INTO audit_log
               (actor_user_id, actor_email, action, target_type, target_id, details)
               VALUES ($1,$2,$3,$4,$5,$6::jsonb)""",
            actor_user_id, actor_email, action, target_type, target_id,
            json.dumps(details or {}),
        )


async def get_audit_log(limit: int = 100) -> list[dict[str, Any]]:
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, actor_user_id, actor_email, action, target_type, target_id,
                      details, created_at
               FROM audit_log ORDER BY created_at DESC LIMIT $1""",
            limit,
        )
        return [dict(row) for row in rows]


async def upsert_face_enrollment(
        employee_code: str, full_name: str, department: str | None,
        consent_basis: str, consent_date: date | None,
        enrolled_by: int | None) -> dict[str, Any]:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO face_enrollments (
                   employee_code, full_name, department, consent_recorded,
                   consent_basis, consent_date, enrolled_by
               )
               VALUES ($1, $2, $3, true, $4, $5, $6)
               ON CONFLICT (employee_code) DO UPDATE SET
                   full_name = EXCLUDED.full_name,
                   department = EXCLUDED.department,
                   consent_recorded = true,
                   consent_basis = EXCLUDED.consent_basis,
                   consent_date = EXCLUDED.consent_date,
                   enrolled_by = COALESCE(EXCLUDED.enrolled_by, face_enrollments.enrolled_by),
                   updated_at = now()
               RETURNING person_id, employee_code, active, embedding_count""",
            employee_code, full_name, department, consent_basis, consent_date,
            enrolled_by,
        )
        return dict(row)


async def list_face_enrollments() -> list[dict[str, Any]]:
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT person_id, employee_code, full_name, department,
                      consent_recorded, consent_basis, consent_date, enrolled_by,
                      enrolled_at, active, embedding_count
               FROM face_enrollments ORDER BY employee_code"""
        )
        return [dict(row) for row in rows]


async def count_active_face_enrollments() -> int:
    async with get_pool().acquire() as conn:
        return int(await conn.fetchval(
            "SELECT count(*) FROM face_enrollments WHERE active",
        ))


async def get_face_enrollment(person_id: int) -> dict[str, Any] | None:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """SELECT person_id, employee_code, full_name, department,
                      active, embedding_count
               FROM face_enrollments WHERE person_id = $1""",
            person_id,
        )
        return dict(row) if row else None


async def get_face_embedding_by_hash(image_hash: str) -> dict[str, Any] | None:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """SELECT embedding_id, person_id, image_hash, active
               FROM face_embeddings WHERE image_hash = $1""",
            image_hash,
        )
        return dict(row) if row else None


async def list_face_embeddings(
        person_id: int | None = None) -> list[dict[str, Any]]:
    query = """SELECT embedding_id, person_id, embedding, image_hash,
                      quality_score, model_name, model_version, active
               FROM face_embeddings"""
    params = ()
    if person_id is not None:
        query += " WHERE person_id = $1"
        params = (person_id,)
    query += " ORDER BY person_id, embedding_id"
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(query, *params)
        return [dict(row) for row in rows]


async def insert_face_embedding(
        person_id: int, embedding: bytes, quality_score: float,
        image_hash: str, photo_path: str | None, model_name: str,
        model_version: str, source: str = "dataset import") -> int | None:
    async with get_pool().acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            """INSERT INTO face_embeddings (
                       person_id, embedding, source, quality_score, image_hash,
                       photo_path, model_name, model_version
                   )
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
                   ON CONFLICT (image_hash) DO NOTHING
                   RETURNING embedding_id""",
            person_id, embedding, source, quality_score, image_hash,
            photo_path, model_name, model_version,
        )
        if row is None:
            return None
        await conn.execute(
            """UPDATE face_enrollments
                   SET embedding_count = (
                       SELECT count(*) FROM face_embeddings
                       WHERE person_id = $1 AND active
                   ), updated_at = now()
                   WHERE person_id = $1""",
            person_id,
        )
        return int(row["embedding_id"])


async def create_face_import_run(
        status: str, actor_user_id: int | None,
        summary: dict[str, Any], report_path: str | None) -> int:
    if status not in {"completed", "failed"}:
        raise ValueError("Invalid face import run status")
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO face_import_runs
                   (status, actor_user_id, summary, report_path)
               VALUES ($1, $2, $3::jsonb, $4)
               RETURNING id""",
            status, actor_user_id, json.dumps(summary), report_path,
        )
        return int(row["id"])


async def enqueue_face_recognition_job(incident_id: int) -> bool:
    async with get_pool().acquire() as conn, conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(64182, 2097)")
        existing = await conn.fetchval(
            "SELECT 1 FROM face_recognition_jobs WHERE incident_id = $1",
            incident_id,
        )
        if existing:
            return False
        pending_jobs = await conn.fetchval(
            """SELECT count(*) FROM face_recognition_jobs
                   WHERE status IN ('pending', 'processing')"""
        )
        if int(pending_jobs) >= config.FACE_JOB_QUEUE_MAX:
            logger.warning(
                "Face-recognition queue reached capacity; incident job deferred"
            )
            return False
        row = await conn.fetchrow(
            """INSERT INTO face_recognition_jobs (incident_id)
                   VALUES ($1)
                   ON CONFLICT (incident_id) DO NOTHING
                   RETURNING incident_id""",
            incident_id,
        )
        if row is None:
            return False
        await conn.execute(
            """INSERT INTO incident_face_matches (
                       incident_id, status, violation_type
                   )
                   SELECT id, 'Queued', violation_type
                   FROM violations WHERE id = $1
                   ON CONFLICT (incident_id) DO UPDATE SET
                       status = 'Queued', matched_person_id = NULL,
                       similarity = NULL, margin_to_second = NULL,
                       face_quality = '{}'::jsonb, face_bbox = NULL,
                       face_crop = NULL, top3_candidates = '[]'::jsonb,
                       violation_type = EXCLUDED.violation_type,
                       reused_from_track = false,
                       processed_at = NULL""",
            incident_id,
        )
        return True


async def claim_face_recognition_job() -> dict[str, Any] | None:
    async with get_pool().acquire() as conn, conn.transaction():
        job = await conn.fetchrow(
            """SELECT j.incident_id, j.attempt_count
                   FROM face_recognition_jobs AS j
                   WHERE j.next_attempt_at <= now()
                     AND (
                         j.status = 'pending'
                         OR (j.status = 'processing'
                             AND j.locked_at < now() - interval '5 minutes')
                     )
                   ORDER BY j.next_attempt_at, j.created_at
                   FOR UPDATE OF j SKIP LOCKED
                   LIMIT 1"""
        )
        if job is None:
            return None
        await conn.execute(
            """UPDATE face_recognition_jobs
                   SET status = 'processing', attempt_count = attempt_count + 1,
                       locked_at = now(), updated_at = now()
                   WHERE incident_id = $1""",
            job["incident_id"],
        )
        await conn.execute(
            """INSERT INTO incident_face_matches (
                       incident_id, status, violation_type
                   )
                   SELECT id, 'Processing', violation_type
                   FROM violations WHERE id = $1
                   ON CONFLICT (incident_id) DO UPDATE SET
                       status = 'Processing', matched_person_id = NULL,
                       similarity = NULL, margin_to_second = NULL,
                       face_quality = '{}'::jsonb, face_bbox = NULL,
                       face_crop = NULL, top3_candidates = '[]'::jsonb,
                       violation_type = EXCLUDED.violation_type,
                       reused_from_track = false,
                       processed_at = NULL""",
            job["incident_id"],
        )
        incident = await conn.fetchrow(
            """SELECT id, camera_id, track_id, violation_type,
                          clean_snapshot_path, snapshot_path, detection_metadata
                   FROM violations WHERE id = $1""",
            job["incident_id"],
        )
        if incident is None:
            await conn.execute(
                """UPDATE face_recognition_jobs
                       SET status = 'failed', locked_at = NULL, updated_at = now(),
                           last_error_code = 'incident_missing'
                       WHERE incident_id = $1""",
                job["incident_id"],
            )
            return None
        return {**dict(job), **dict(incident)}

async def reuse_face_match_for_track(
        incident_id: int, camera_id: str, track_id: int,
        reuse_seconds: float, violation_types: list[str],
) -> int | None:
    if reuse_seconds <= 0 or not violation_types:
        return None
    async with get_pool().acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            """WITH current_incident AS (
                   SELECT id, violation_type, epoch_time
                   FROM violations WHERE id = $1
               ), prior_match AS (
                   SELECT m.*
                   FROM incident_face_matches AS m
                   JOIN violations AS prior ON prior.id = m.incident_id
                   CROSS JOIN current_incident AS current
                   WHERE prior.camera_id = $2
                     AND prior.track_id = $3
                     AND prior.id <> current.id
                     AND prior.epoch_time <= current.epoch_time
                     AND prior.epoch_time >= current.epoch_time - $4
                     AND m.violation_type = ANY($5::text[])
                     AND m.status IN (
                         'Identified', 'Suggested', 'Not identified', 'No face found'
                     )
                     AND m.reused_from_track = false
                     AND m.review_decision IS DISTINCT FROM 'Rejected'
                   ORDER BY prior.epoch_time DESC, m.processed_at DESC
                   LIMIT 1
               ), copied_match AS (
                   INSERT INTO incident_face_matches (
                       incident_id, status, matched_person_id, similarity,
                       margin_to_second, face_quality, face_bbox, face_crop,
                       top3_candidates, model_name, model_version, processed_at,
                       violation_type, reused_from_track
                   )
                   SELECT current.id, prior.status, prior.matched_person_id,
                          prior.similarity, prior.margin_to_second,
                          prior.face_quality || jsonb_build_object(
                              'reused_from_track', true,
                              'source_incident_id', prior.incident_id
                          ),
                          prior.face_bbox, prior.face_crop, prior.top3_candidates,
                          prior.model_name, prior.model_version, now(),
                          current.violation_type, true
                   FROM current_incident AS current
                   CROSS JOIN prior_match AS prior
                   ON CONFLICT (incident_id) DO UPDATE SET
                       status = EXCLUDED.status,
                       matched_person_id = EXCLUDED.matched_person_id,
                       similarity = EXCLUDED.similarity,
                       margin_to_second = EXCLUDED.margin_to_second,
                       face_quality = EXCLUDED.face_quality,
                       face_bbox = EXCLUDED.face_bbox,
                       face_crop = EXCLUDED.face_crop,
                       top3_candidates = EXCLUDED.top3_candidates,
                       model_name = EXCLUDED.model_name,
                       model_version = EXCLUDED.model_version,
                       processed_at = EXCLUDED.processed_at,
                       violation_type = EXCLUDED.violation_type,
                       reused_from_track = true,
                       review_decision = NULL,
                       reviewed_by = NULL,
                       reviewed_at = NULL,
                       review_comment = NULL
                   RETURNING incident_id
               )
               SELECT prior.incident_id
               FROM prior_match AS prior
               JOIN copied_match AS copied ON true""",
            incident_id, camera_id, track_id, reuse_seconds,
            violation_types,
        )
        if row is None:
            return None
        await conn.execute(
            """UPDATE face_recognition_jobs
                   SET status = 'complete', locked_at = NULL,
                       last_error_code = NULL, updated_at = now()
                   WHERE incident_id = $1""",
            incident_id,
        )
        return int(row["incident_id"])


async def complete_face_recognition_job(
        incident_id: int, status: str, matched_person_id: int | None,
        similarity: float | None, margin_to_second: float | None,
        face_quality: dict[str, Any], face_bbox: list[int] | None,
        top3_candidates: list[dict[str, Any]], model_name: str,
        model_version: str, face_crop: bytes | None = None) -> None:
    if status not in {"Identified", "Suggested", "Not identified", "No face found"}:
        raise ValueError("Invalid completed face-recognition status")
    async with get_pool().acquire() as conn, conn.transaction():
        await conn.execute(
            """INSERT INTO incident_face_matches (
                       incident_id, status, matched_person_id, similarity,
                       margin_to_second, face_quality, face_bbox, face_crop,
                       top3_candidates, model_name, model_version, processed_at
                   )
                   VALUES (
                       $1,$2,$3,$4,$5,$6::jsonb,$7::jsonb,$8,$9::jsonb,
                       $10,$11,now()
                   )
                   ON CONFLICT (incident_id) DO UPDATE SET
                       status = EXCLUDED.status,
                       matched_person_id = EXCLUDED.matched_person_id,
                       similarity = EXCLUDED.similarity,
                       margin_to_second = EXCLUDED.margin_to_second,
                       face_quality = EXCLUDED.face_quality,
                       face_bbox = EXCLUDED.face_bbox,
                       face_crop = EXCLUDED.face_crop,
                       top3_candidates = EXCLUDED.top3_candidates,
                       model_name = EXCLUDED.model_name,
                       model_version = EXCLUDED.model_version,
                       processed_at = now()""",
            incident_id, status, matched_person_id, similarity,
            margin_to_second, json.dumps(face_quality),
            json.dumps(face_bbox) if face_bbox is not None else None,
            face_crop, json.dumps(top3_candidates), model_name, model_version,
        )
        await conn.execute(
            """UPDATE face_recognition_jobs
                   SET status = 'complete', locked_at = NULL, last_error_code = NULL,
                       updated_at = now()
                   WHERE incident_id = $1""",
            incident_id,
        )


async def fail_face_recognition_job(incident_id: int, error_code: str) -> None:
    async with get_pool().acquire() as conn, conn.transaction():
        await conn.execute(
            """INSERT INTO incident_face_matches (
                       incident_id, status, face_quality, processed_at
                   )
                   VALUES ($1, 'Error', $2::jsonb, now())
                   ON CONFLICT (incident_id) DO UPDATE SET
                       status = 'Error', face_quality = EXCLUDED.face_quality,
                       face_crop = NULL,
                       processed_at = now()""",
            incident_id, json.dumps({"error_code": error_code}),
        )
        await conn.execute(
            """UPDATE face_recognition_jobs
                   SET status = 'failed', locked_at = NULL,
                       last_error_code = $2, updated_at = now()
                   WHERE incident_id = $1""",
            incident_id, error_code,
        )


async def get_face_match(incident_id: int) -> dict[str, Any] | None:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """SELECT m.*, e.employee_code AS matched_employee_code,
                      e.full_name AS matched_full_name,
                      e.department AS matched_department,
                      e.active AS matched_person_active
               FROM incident_face_matches AS m
               LEFT JOIN face_enrollments AS e
                 ON e.person_id = m.matched_person_id
               WHERE m.incident_id = $1""",
            incident_id,
        )
        return dict(row) if row else None


async def get_face_match_availability(incident_ids: list[int]) -> set[int]:
    if not incident_ids:
        return set()
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT incident_id FROM incident_face_matches
               WHERE incident_id = ANY($1::bigint[])""",
            incident_ids,
        )
        return {int(row["incident_id"]) for row in rows}

async def list_face_matches(
        violation_type: str | None = None,
        limit: int = 100, offset: int = 0,
) -> list[dict[str, Any]]:
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT m.incident_id, m.violation_type, m.status,
                      m.similarity, m.margin_to_second, m.reused_from_track,
                      m.review_decision, m.reviewed_at, m.processed_at,
                      v.camera_id, v.camera_name, v.epoch_time,
                      e.employee_code, e.full_name, e.department
               FROM incident_face_matches AS m
               JOIN violations AS v ON v.id = m.incident_id
               LEFT JOIN face_enrollments AS e
                 ON e.person_id = m.matched_person_id
               WHERE ($1::text IS NULL OR m.violation_type = $1)
               ORDER BY m.processed_at DESC NULLS LAST, m.incident_id DESC
               LIMIT $2 OFFSET $3""",
            violation_type, limit, offset,
        )
        return [dict(row) for row in rows]


async def get_face_match_for_email(incident_id: int) -> dict[str, Any] | None:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """SELECT m.status, m.similarity, m.review_decision,
                      e.full_name
               FROM incident_face_matches AS m
               JOIN face_enrollments AS e
                 ON e.person_id = m.matched_person_id
               WHERE m.incident_id = $1
                 AND m.status = 'Identified' AND e.active""",
            incident_id,
        )
        return dict(row) if row else None


async def get_face_import_runs(limit: int = 50) -> list[dict[str, Any]]:
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, status, summary, started_at, completed_at
               FROM face_import_runs ORDER BY started_at DESC LIMIT $1""",
            limit,
        )
        results = [dict(row) for row in rows]
        for result in results:
            if isinstance(result.get("summary"), str):
                result["summary"] = json.loads(result["summary"])
        return results


async def get_face_import_run(run_id: int) -> dict[str, Any] | None:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """SELECT id, status, summary, report_path, started_at, completed_at
               FROM face_import_runs WHERE id = $1""",
            run_id,
        )
        if row is None:
            return None
        result = dict(row)
        if isinstance(result.get("summary"), str):
            result["summary"] = json.loads(result["summary"])
        return result


async def remove_face_enrollment(
        person_id: int, permanently: bool = False) -> dict[str, Any] | None:
    async with get_pool().acquire() as conn, conn.transaction():
        person = await conn.fetchrow(
            "SELECT person_id, employee_code, active FROM face_enrollments "
            "WHERE person_id = $1 FOR UPDATE",
            person_id,
        )
        if person is None:
            return None
        photos = await conn.fetch(
            "SELECT photo_path FROM face_embeddings "
            "WHERE person_id = $1 AND photo_path IS NOT NULL",
            person_id,
        )
        await conn.execute(
            """UPDATE incident_face_matches AS m
                   SET top3_candidates = COALESCE((
                           SELECT jsonb_agg(candidates.candidate)
                           FROM jsonb_array_elements(m.top3_candidates)
                                AS candidates(candidate)
                           WHERE candidates.candidate->>'person_id' IS NULL
                              OR (candidates.candidate->>'person_id')::bigint <> $1
                       ), '[]'::jsonb),
                       status = CASE WHEN m.matched_person_id = $1
                                     THEN 'Not identified' ELSE m.status END,
                       matched_person_id = CASE WHEN m.matched_person_id = $1
                                                THEN NULL ELSE m.matched_person_id END,
                       review_decision = CASE WHEN m.matched_person_id = $1
                                              THEN NULL ELSE m.review_decision END,
                       face_crop = CASE WHEN m.matched_person_id = $1
                                        THEN NULL ELSE m.face_crop END,
                       reviewed_by = CASE WHEN m.matched_person_id = $1
                                          THEN NULL ELSE m.reviewed_by END,
                       reviewed_at = CASE WHEN m.matched_person_id = $1
                                          THEN NULL ELSE m.reviewed_at END,
                       review_comment = CASE WHEN m.matched_person_id = $1
                                             THEN NULL ELSE m.review_comment END
                   WHERE m.matched_person_id = $1
                      OR EXISTS (
                           SELECT 1
                           FROM jsonb_array_elements(m.top3_candidates)
                                AS candidates(candidate)
                           WHERE candidates.candidate->>'person_id' = $1::text
                      )""",
            person_id,
        )
        await conn.execute(
            "DELETE FROM face_embeddings WHERE person_id = $1", person_id,
        )
        if permanently:
            await conn.execute(
                "DELETE FROM face_enrollments WHERE person_id = $1",
                person_id,
            )
        else:
            await conn.execute(
                """UPDATE face_enrollments
                       SET active = false, embedding_count = 0, updated_at = now()
                       WHERE person_id = $1""",
                person_id,
            )
        return {
            "person_id": int(person["person_id"]),
            "employee_code": person["employee_code"],
            "permanently_deleted": permanently,
            "photo_paths": [row["photo_path"] for row in photos],
        }


async def review_face_match(
        incident_id: int, decision: str, reviewer_id: int | None,
        comment: str | None, person_id: int | None = None,
) -> dict[str, Any] | None:
    if decision not in {"Confirmed", "Rejected", "Reassigned"}:
        raise ValueError("Invalid face review decision")
    async with get_pool().acquire() as conn:
        async with conn.transaction():
            match = await conn.fetchrow(
                """SELECT status, matched_person_id, similarity, top3_candidates
                   FROM incident_face_matches
                   WHERE incident_id = $1 FOR UPDATE""",
                incident_id,
            )
            if match is None:
                return None
            matched_person_id = match["matched_person_id"]
            similarity = match["similarity"]
            status = match["status"]
            if decision == "Confirmed":
                if status not in {"Identified", "Suggested"} or matched_person_id is None:
                    raise ValueError("Only a suggested or identified match can be confirmed")
                status = "Identified"
            elif decision == "Rejected":
                matched_person_id = None
                similarity = None
                status = "Not identified"
            else:
                if person_id is None:
                    raise ValueError("Reassignment requires an enrolled person")
                person = await conn.fetchrow(
                    """SELECT person_id FROM face_enrollments
                       WHERE person_id = $1 AND active FOR SHARE""",
                    person_id,
                )
                if person is None:
                    raise ValueError("Selected employee is not actively enrolled")
                matched_person_id = int(person["person_id"])
                status = "Identified"
                candidates = match["top3_candidates"] or []
                if isinstance(candidates, str):
                    candidates = json.loads(candidates)
                similarity = next(
                    (
                        candidate.get("similarity")
                        for candidate in candidates
                        if int(candidate.get("person_id", -1)) == matched_person_id
                    ),
                    None,
                )
            row = await conn.fetchrow(
                """UPDATE incident_face_matches
                   SET status = $2, matched_person_id = $3, similarity = $4,
                       review_decision = $5, reviewed_by = $6, reviewed_at = now(),
                       review_comment = $7,
                       face_crop = CASE WHEN $5 = 'Rejected'
                                        THEN NULL ELSE face_crop END
                   WHERE incident_id = $1
                   RETURNING incident_id, status, matched_person_id, similarity,
                             margin_to_second, top3_candidates, review_decision,
                             reviewed_by, reviewed_at, review_comment""",
                incident_id, status, matched_person_id, similarity, decision,
                reviewer_id, comment,
            )
            return dict(row)


async def requeue_face_recognition_job(incident_id: int) -> bool:
    async with get_pool().acquire() as conn, conn.transaction():
        incident = await conn.fetchrow(
            """SELECT violation_type FROM violations WHERE id = $1 FOR UPDATE""",
            incident_id,
        )
        if (
            incident is None
            or incident["violation_type"] not in config.FACE_VIOLATION_TYPES
        ):
            return False
        await conn.execute(
            """INSERT INTO face_recognition_jobs (
                       incident_id, status, attempt_count, next_attempt_at,
                       locked_at, last_error_code, updated_at
                   )
                   VALUES ($1, 'pending', 0, now(), NULL, NULL, now())
                   ON CONFLICT (incident_id) DO UPDATE SET
                       status = 'pending', attempt_count = 0,
                       next_attempt_at = now(), locked_at = NULL,
                       last_error_code = NULL, updated_at = now()""",
            incident_id,
        )
        await conn.execute(
            """INSERT INTO incident_face_matches (
                       incident_id, status, violation_type
                   )
                   SELECT id, 'Queued', violation_type
                   FROM violations WHERE id = $1
                   ON CONFLICT (incident_id) DO UPDATE SET
                       status = 'Queued', matched_person_id = NULL,
                       similarity = NULL, margin_to_second = NULL,
                       face_quality = '{}'::jsonb, face_bbox = NULL,
                       face_crop = NULL, top3_candidates = '[]'::jsonb,
                       violation_type = EXCLUDED.violation_type,
                       reused_from_track = false,
                       model_name = NULL,
                       model_version = NULL, processed_at = NULL,
                       review_decision = NULL, reviewed_by = NULL,
                       reviewed_at = NULL, review_comment = NULL""",
            incident_id,
        )
        return True


async def requeue_face_recognition_batch(limit: int = 100) -> list[int]:
    async with get_pool().acquire() as conn, conn.transaction():
        rows = await conn.fetch(
            """SELECT v.id
                   FROM violations AS v
                   JOIN incident_face_matches AS m ON m.incident_id = v.id
                   LEFT JOIN face_recognition_jobs AS j ON j.incident_id = v.id
                   WHERE v.violation_type = ANY($1::text[])
                     AND m.status IN ('Error', 'No face found')
                     AND (j.status IS NULL OR j.status NOT IN ('pending', 'processing'))
                   ORDER BY m.processed_at, v.id
                   LIMIT $2
                   FOR UPDATE OF v SKIP LOCKED""",
            sorted(config.FACE_VIOLATION_TYPES), limit,
        )
        incident_ids = [int(row["id"]) for row in rows]
        for incident_id in incident_ids:
            await conn.execute(
                """INSERT INTO face_recognition_jobs (
                           incident_id, status, attempt_count, next_attempt_at,
                           locked_at, last_error_code, updated_at
                       )
                       VALUES ($1, 'pending', 0, now(), NULL, NULL, now())
                       ON CONFLICT (incident_id) DO UPDATE SET
                           status = 'pending', attempt_count = 0,
                           next_attempt_at = now(), locked_at = NULL,
                           last_error_code = NULL, updated_at = now()""",
                incident_id,
            )
            await conn.execute(
                """UPDATE incident_face_matches
                       SET status = 'Queued', matched_person_id = NULL,
                           similarity = NULL, margin_to_second = NULL,
                           face_quality = '{}'::jsonb, face_bbox = NULL,
                           top3_candidates = '[]'::jsonb, model_name = NULL,
                           model_version = NULL, processed_at = NULL,
                           review_decision = NULL, reviewed_by = NULL,
                           reviewed_at = NULL, review_comment = NULL
                       WHERE incident_id = $1""",
                incident_id,
            )
        return incident_ids


async def get_face_repeat_violations(
        since_epoch: float | None = None,
        department: str | None = None,
        employee_code: str | None = None,
        violation_type: str | None = None,
) -> list[dict[str, Any]]:
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT e.person_id, e.employee_code, e.full_name, e.department,
                      v.violation_type,
                      count(*) AS confirmed_violations,
                      max(v.epoch_time) AS last_violation_epoch
               FROM incident_face_matches AS m
               JOIN face_enrollments AS e ON e.person_id = m.matched_person_id
               JOIN violations AS v ON v.id = m.incident_id
               WHERE m.review_decision IN ('Confirmed', 'Reassigned')
                 AND e.active
                 AND ($1::double precision IS NULL OR v.epoch_time >= $1)
                 AND ($2::text IS NULL OR e.department = $2)
                 AND ($3::text IS NULL OR e.employee_code = $3)
                 AND v.violation_type = ANY($4::text[])
                 AND ($5::text IS NULL OR v.violation_type = $5)
               GROUP BY e.person_id, e.employee_code, e.full_name, e.department,
                        v.violation_type
               ORDER BY confirmed_violations DESC, e.employee_code
               LIMIT 500""",
            since_epoch, department, employee_code,
            sorted(config.FACE_VIOLATION_TYPES), violation_type,
        )
        return [dict(row) for row in rows]


async def delete_expired_face_match_records(cutoff: datetime) -> int:
    async with get_pool().acquire() as conn, conn.transaction():
        await conn.execute(
            """DELETE FROM face_recognition_jobs
                   WHERE incident_id IN (
                       SELECT incident_id FROM incident_face_matches
                       WHERE COALESCE(processed_at, created_at) < $1
                         AND status <> 'Processing'
                   )""",
            cutoff,
        )
        result = await conn.execute(
            """DELETE FROM incident_face_matches
                   WHERE COALESCE(processed_at, created_at) < $1
                     AND status <> 'Processing'""",
            cutoff,
        )
        return int(result.rsplit(" ", 1)[-1])


async def mark_alert_dispatched(violation_id: int):
    async with get_pool().acquire() as conn:
        await conn.execute("UPDATE violations SET alert_dispatched = true WHERE id = $1", violation_id)


async def enqueue_alert_job(violation_id: int, rule_id: int, channel: str,
                            payload: dict[str, Any]) -> None:
    async with get_pool().acquire() as conn:
        await conn.execute(
            """INSERT INTO alert_outbox
               (violation_id, rule_id, channel, payload)
               VALUES ($1, $2, $3, $4::jsonb)
               ON CONFLICT (violation_id, rule_id, channel) DO NOTHING""",
            violation_id, rule_id, channel, json.dumps(payload),
        )


async def claim_alert_job() -> dict[str, Any] | None:
    async with get_pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """WITH next_job AS (
                       SELECT id
                       FROM alert_outbox
                       WHERE (status = 'pending' AND next_attempt_at <= now())
                          OR (status = 'processing'
                              AND locked_at < now() - interval '5 minutes')
                       ORDER BY created_at
                       FOR UPDATE SKIP LOCKED
                       LIMIT 1
                   )
                   UPDATE alert_outbox AS job
                   SET status = 'processing', locked_at = now(),
                       updated_at = now()
                   FROM next_job
                   WHERE job.id = next_job.id
                   RETURNING job.id, job.violation_id, job.rule_id, job.channel,
                             job.payload, job.attempt_count""",
            )
        return dict(row) if row else None


async def finish_alert_job(job_id: int, violation_id: int, rule_id: int,
                           channel: str, success: bool, attempt_count: int,
                           max_attempts: int, error: str | None) -> None:
    retry_delay = min(2 ** max(attempt_count - 1, 0), 300)
    async with get_pool().acquire() as conn, conn.transaction():
        await conn.execute(
            """UPDATE alert_outbox
                   SET status = CASE
                           WHEN $2 THEN 'sent'
                               WHEN $3::integer >= $4::integer THEN 'dead'
                           ELSE 'pending'
                       END,
                           attempt_count = $3::integer,
                       next_attempt_at = CASE
                               WHEN $2 OR $3::integer >= $4::integer THEN now()
                               ELSE now() + ($5::double precision * interval '1 second')
                       END,
                       locked_at = NULL, last_error = $6, updated_at = now()
                   WHERE id = $1""",
            job_id, success, attempt_count, max_attempts, retry_delay, error,
        )
        await conn.execute(
            """INSERT INTO alert_dispatch_log
                   (violation_id, rule_id, channel, success)
                   VALUES ($1, $2, $3, $4)""",
            violation_id, rule_id, channel, success,
        )
        await conn.execute(
            """UPDATE violations
                   SET alert_dispatched = true
                   WHERE id = $1 AND NOT EXISTS (
                       SELECT 1 FROM alert_outbox
                       WHERE violation_id = $1
                         AND status NOT IN ('sent', 'dead')
                   )""",
            violation_id,
        )


async def log_alert_dispatch(violation_id: int, rule_id: int | None, channel: str, success: bool):
    async with get_pool().acquire() as conn:
        await conn.execute(
            """INSERT INTO alert_dispatch_log (violation_id, rule_id, channel, success)
               VALUES ($1,$2,$3,$4)""",
            violation_id, rule_id, channel, success,
        )


async def get_violation_by_id(violation_id: int) -> dict[str, Any] | None:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM violations WHERE id = $1", violation_id)
        return dict(row) if row else None


async def get_recent_violations(limit: int = 50, camera_id: str | None = None,
                                 violation_type: str | None = None,
                                 area_group_id: int | None = None,
                                 since_epoch: float | None = None,
                                 offset: int = 0,
                                 employee_code: str | None = None,
                                 department: str | None = None) -> list[dict[str, Any]]:
    query = "SELECT * FROM violations WHERE 1=1"
    params = []
    if since_epoch is not None:
        params.append(since_epoch)
        query += f" AND epoch_time >= ${len(params)}"
    if camera_id:
        params.append(camera_id)
        query += f" AND camera_id = ${len(params)}"
    if violation_type:
        legacy_type = violation_type.removeprefix("no_")
        params.append([violation_type, legacy_type])
        query += f" AND violation_type = ANY(${len(params)})"
    if area_group_id:
        params.append(area_group_id)
        query += f" AND area_group_id = ${len(params)}"
    if employee_code is not None or department is not None:
        query += """ AND EXISTS (
            SELECT 1 FROM incident_face_matches AS fm
            JOIN face_enrollments AS fe ON fe.person_id = fm.matched_person_id
            WHERE fm.incident_id = violations.id
              AND fm.review_decision IN ('Confirmed', 'Reassigned')
              AND fe.active"""
        if employee_code is not None:
            params.append(employee_code)
            query += f" AND fe.employee_code = ${len(params)}"
        if department is not None:
            params.append(department)
            query += f" AND fe.department = ${len(params)}"
        query += ")"
    params.extend((limit, offset))
    query += f" ORDER BY epoch_time DESC, id DESC LIMIT ${len(params) - 1} OFFSET ${len(params)}"

    async with get_pool().acquire() as conn:
        rows = await conn.fetch(query, *params)
        return [dict(r) for r in rows]


async def delete_violations_before(cutoff_epoch: float) -> dict[str, int]:
    async with get_pool().acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """SELECT id, snapshot_path, clean_snapshot_path, video_path
                   FROM violations WHERE epoch_time < $1 FOR UPDATE""",
                cutoff_epoch,
            )
            if not rows:
                return {"violations_deleted": 0, "evidence_deleted": 0, "evidence_errors": 0}
            await conn.execute(
                "DELETE FROM violations WHERE id = ANY($1::bigint[])",
                [row["id"] for row in rows],
            )

    evidence_paths = {
        path
        for row in rows
        for path in (row["snapshot_path"], row["clean_snapshot_path"], row["video_path"])
        if path
    }
    allowed_roots = (
        Path(config.SNAPSHOT_DIR).resolve(),
        Path(config.EVIDENCE_CLIP_DIR).resolve(),
    )
    evidence_deleted = 0
    evidence_errors = 0
    for evidence_path in evidence_paths:
        path = Path(evidence_path).resolve()
        if not any(path.is_relative_to(root) for root in allowed_roots):
            logger.error("Refusing to remove evidence outside configured directories: %s", path)
            evidence_errors += 1
            continue
        try:
            if path.is_file():
                path.unlink()
                evidence_deleted += 1
        except OSError:
            logger.exception("Failed to remove expired violation evidence: %s", path)
            evidence_errors += 1

    return {
        "violations_deleted": len(rows),
        "evidence_deleted": evidence_deleted,
        "evidence_errors": evidence_errors,
    }


async def get_compliance_stats(window_hours: int = 24) -> dict[str, Any]:
    cutoff = time.time() - window_hours * 3600
    async with get_pool().acquire() as conn:
        total = await conn.fetchval("SELECT COUNT(*) FROM violations WHERE epoch_time >= $1", cutoff)
        by_type = await conn.fetch(
            """SELECT CASE violation_type
                       WHEN 'helmet' THEN 'no_helmet'
                       WHEN 'vest' THEN 'no_vest'
                       WHEN 'gloves' THEN 'no_gloves'
                       WHEN 'boots' THEN 'no_boots'
                       WHEN 'goggles' THEN 'no_goggles'
                       WHEN 'mask' THEN 'no_mask'
                       WHEN 'arc_flash_suit' THEN 'no_arc_flash_suit'
                       ELSE violation_type
                   END AS violation_type,
                   COUNT(*) as c FROM violations
               WHERE epoch_time >= $1
               GROUP BY 1 ORDER BY c DESC""", cutoff)
        by_camera = await conn.fetch(
            """SELECT camera_name, COUNT(*) as c FROM violations
               WHERE epoch_time >= $1 GROUP BY camera_name ORDER BY c DESC""", cutoff)
        by_area_group = await conn.fetch(
            """SELECT COALESCE(area_group, 'Unassigned') as area_group, COUNT(*) as c FROM violations
               WHERE epoch_time >= $1 GROUP BY area_group ORDER BY c DESC""", cutoff)
        hourly = await conn.fetch(
            """SELECT date_trunc('hour', to_timestamp(epoch_time)) as hour, COUNT(*) as c
               FROM violations WHERE epoch_time >= $1 GROUP BY hour ORDER BY hour ASC""", cutoff)
        return {
            "window_hours": window_hours,
            "total_violations": total,
            "by_type": [dict(r) for r in by_type],
            "by_camera": [dict(r) for r in by_camera],
            "by_area_group": [dict(r) for r in by_area_group],
            "hourly": [{"hour": r["hour"].isoformat(), "count": r["c"]} for r in hourly],
        }


async def update_camera_status(camera_id: str, camera_name: str, zone: str,
                                connected: bool, last_frame_epoch: float):
    async with get_pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """INSERT INTO camera_status_events (camera_id, connected)
                   SELECT $1, $2
                   WHERE NOT EXISTS (
                       SELECT 1 FROM camera_status
                       WHERE camera_id = $1
                         AND connected IS NOT DISTINCT FROM $2
                   )""",
                camera_id, connected,
            )
            await conn.execute(
                """INSERT INTO camera_status (camera_id, camera_name, zone, connected, last_frame_epoch, last_updated)
                   VALUES ($1,$2,$3,$4,$5, now())
                   ON CONFLICT (camera_id) DO UPDATE SET
                        camera_name = excluded.camera_name,
                        zone = excluded.zone,
                        connected = excluded.connected,
                        last_frame_epoch = excluded.last_frame_epoch,
                        last_updated = now()""",
                camera_id, camera_name, zone, connected, last_frame_epoch,
            )
            await conn.execute(
                """INSERT INTO camera_status_samples (camera_id, observed_at, connected)
                   VALUES ($1, date_trunc('minute', now()), $2)
                   ON CONFLICT (camera_id, observed_at) DO UPDATE
                   SET connected = excluded.connected""",
                camera_id, connected,
            )


async def get_camera_status_history(
    start: datetime,
    end: datetime,
    area_group_id: int | None = None,
    camera_id: str | None = None,
) -> dict[str, Any]:
    async with get_pool().acquire() as conn:
        await conn.execute(
            """DELETE FROM camera_status_samples
               WHERE observed_at < now() - ($1::integer * interval '1 day')""",
            config.CAMERA_STATUS_HISTORY_DAYS,
        )
        await conn.execute(
            """DELETE FROM camera_status_events
               WHERE observed_at < now() - ($1::integer * interval '1 day')""",
            config.CAMERA_STATUS_HISTORY_DAYS,
        )
        cameras = await conn.fetch(
            """SELECT camera.camera_id, camera.name, camera.enabled,
                      area.name AS area_group_name, status.connected,
                      status.last_updated
               FROM cameras AS camera
               LEFT JOIN area_groups AS area ON area.id = camera.area_group_id
               LEFT JOIN camera_status AS status ON status.camera_id = camera.camera_id
               WHERE ($1::bigint IS NULL OR camera.area_group_id = $1)
                 AND ($2::text IS NULL OR camera.camera_id = $2)
               ORDER BY camera.camera_id""",
            area_group_id, camera_id,
        )
        previous = await conn.fetch(
            """SELECT DISTINCT ON (camera_id)
                      camera_id, connected, observed_at
               FROM camera_status_events
               WHERE observed_at <= $1
                 AND ($2::bigint IS NULL OR camera_id IN (
                     SELECT camera_id FROM cameras WHERE area_group_id = $2
                 ))
                 AND ($3::text IS NULL OR camera_id = $3)
               ORDER BY camera_id, observed_at DESC""",
            start, area_group_id, camera_id,
        )
        events = await conn.fetch(
            """SELECT camera_id, connected, observed_at
               FROM camera_status_events
               WHERE observed_at > $1 AND observed_at <= $2
                 AND ($3::bigint IS NULL OR camera_id IN (
                     SELECT camera_id FROM cameras WHERE area_group_id = $3
                 ))
                 AND ($4::text IS NULL OR camera_id = $4)
               ORDER BY camera_id, observed_at""",
            start, end, area_group_id, camera_id,
        )
        previous_samples = await conn.fetch(
            """SELECT DISTINCT ON (camera_id)
                      camera_id, connected, observed_at
               FROM camera_status_samples
               WHERE observed_at <= $1
                 AND ($2::bigint IS NULL OR camera_id IN (
                     SELECT camera_id FROM cameras WHERE area_group_id = $2
                 ))
                 AND ($3::text IS NULL OR camera_id = $3)
               ORDER BY camera_id, observed_at DESC""",
            start, area_group_id, camera_id,
        )
        samples = await conn.fetch(
            """SELECT camera_id, connected, observed_at
               FROM camera_status_samples
               WHERE observed_at > $1 AND observed_at <= $2
                 AND ($3::bigint IS NULL OR camera_id IN (
                     SELECT camera_id FROM cameras WHERE area_group_id = $3
                 ))
                 AND ($4::text IS NULL OR camera_id = $4)
               ORDER BY camera_id, observed_at""",
            start, end, area_group_id, camera_id,
        )
        return {
            "cameras": [dict(row) for row in cameras],
            "previous": [dict(row) for row in previous],
            "events": [dict(row) for row in events],
            "previous_samples": [dict(row) for row in previous_samples],
            "samples": [dict(row) for row in samples],
        }


async def get_camera_statuses() -> list[dict[str, Any]]:
    async with get_pool().acquire() as conn:
        rows = await conn.fetch("SELECT * FROM camera_status")
        return [dict(r) for r in rows]


async def delete_camera_status(camera_id: str):
    async with get_pool().acquire() as conn:
        await conn.execute("DELETE FROM camera_status WHERE camera_id = $1", camera_id)


async def list_rules(enabled_only: bool = False) -> list[dict[str, Any]]:
    query = "SELECT * FROM alert_rules"
    if enabled_only:
        query += " WHERE enabled = true"
    query += " ORDER BY id ASC"
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(query)
        return [dict(r) for r in rows]


async def create_rule(name, camera_id, zone, violation_type, min_severity,
                       channels, cooldown_seconds, email_recipients, enabled=True) -> dict[str, Any]:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO alert_rules
               (name, camera_id, zone, violation_type, min_severity, channels,
                cooldown_seconds, email_recipients, enabled)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING *""",
            name, camera_id, zone, violation_type, min_severity, channels,
            cooldown_seconds, email_recipients, enabled,
        )
        return dict(row)


async def update_rule(rule_id: int, **fields) -> dict[str, Any] | None:
    if not fields:
        return None
    set_clauses, params = [], []
    for key, value in fields.items():
        params.append(value)
        set_clauses.append(f"{key} = ${len(params)}")
    params.append(rule_id)
    query = f"UPDATE alert_rules SET {', '.join(set_clauses)}, updated_at = now() WHERE id = ${len(params)} RETURNING *"
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(query, *params)
        return dict(row) if row else None


async def delete_rule(rule_id: int) -> bool:
    async with get_pool().acquire() as conn:
        result = await conn.execute("DELETE FROM alert_rules WHERE id = $1", rule_id)
        return result.endswith("1")
