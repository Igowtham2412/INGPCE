"""
db.py — async PostgreSQL data layer (asyncpg, connection-pooled).

`sync_seed_data()` is the fix for cameras silently missing from the
frontend: it runs on EVERY app startup AND is exposed via
POST /api/cameras/sync-seed (a dashboard button), and works per-camera:
  - ensures every area group in SEED_AREA_GROUPS exists (create if missing)
  - creates missing configured cameras and updates their configured RTSP URL
  - disables retired seed cameras without deleting their incident history
This makes seeding idempotent and self-healing while preserving operator
names, PPE settings, and records for existing cameras.
"""
import asyncio
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

import asyncpg

from . import config

logger = logging.getLogger("ppe.db")

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
    area_group_id BIGINT REFERENCES area_groups(id) ON DELETE SET NULL,
    required_ppe TEXT[] NOT NULL DEFAULT '{helmet,vest}',
    enabled BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

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

_pool: Optional[asyncpg.Pool] = None


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


async def sync_seed_data() -> Dict[str, Any]:
    """Synchronize configured feed URLs and safely retire removed seed cameras."""
    groups_created = 0
    cameras_created = 0
    cameras_updated = 0
    cameras_disabled = 0
    updated_camera_ids: List[str] = []
    disabled_camera_ids: List[str] = []

    existing_groups = await list_area_groups()
    group_name_to_id = {g["name"]: g["id"] for g in existing_groups}

    for group in config.SEED_AREA_GROUPS:
        if group["name"] not in group_name_to_id:
            row = await create_area_group(group["name"], group.get("description", ""))
            group_name_to_id[group["name"]] = row["id"]
            groups_created += 1

    existing_cameras = await list_cameras()
    cameras_by_id = {c["camera_id"]: c for c in existing_cameras}
    newly_created_camera_ids: List[str] = []

    for cam in config.SEED_CAMERAS:
        camera_id = cam["camera_id"]
        existing = cameras_by_id.get(camera_id)
        if existing:
            if existing["rtsp_url"] != cam["rtsp_url"]:
                await update_camera(camera_id, rtsp_url=cam["rtsp_url"])
                cameras_updated += 1
                updated_camera_ids.append(camera_id)
            continue
        area_group_id = group_name_to_id.get(cam.get("area_group_name"))
        await create_camera(
            camera_id, cam["name"], cam["rtsp_url"], area_group_id,
            cam.get("required_ppe", ["helmet", "vest"]),
        )
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


async def list_area_groups() -> List[Dict[str, Any]]:
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT ag.*, COUNT(c.id) as camera_count
               FROM area_groups ag
               LEFT JOIN cameras c ON c.area_group_id = ag.id
               GROUP BY ag.id ORDER BY ag.name ASC"""
        )
        return [dict(r) for r in rows]


async def create_area_group(name: str, description: str = "") -> Dict[str, Any]:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO area_groups (name, description) VALUES ($1,$2) RETURNING *",
            name, description,
        )
        return dict(row)


async def update_area_group(group_id: int, **fields) -> Optional[Dict[str, Any]]:
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


async def list_cameras() -> List[Dict[str, Any]]:
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT c.*, ag.name as area_group_name
               FROM cameras c
               LEFT JOIN area_groups ag ON ag.id = c.area_group_id
               ORDER BY c.camera_id ASC"""
        )
        return [dict(r) for r in rows]


async def list_camera_zones(camera_id: str, enabled_only: bool = True) -> List[Dict[str, Any]]:
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
                             polygon: List[List[float]], enabled: bool = True) -> Dict[str, Any]:
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


async def update_camera_zone(zone_id: int, camera_id: str, **fields) -> Optional[Dict[str, Any]]:
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


async def get_camera(camera_id: str) -> Optional[Dict[str, Any]]:
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
                         area_group_id: Optional[int], required_ppe: List[str],
                         enabled: bool = True) -> Dict[str, Any]:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO cameras (camera_id, name, rtsp_url, area_group_id, required_ppe, enabled)
               VALUES ($1,$2,$3,$4,$5,$6) RETURNING *""",
            camera_id, name, rtsp_url, area_group_id, required_ppe, enabled,
        )
        return dict(row)


async def update_camera(camera_id: str, **fields) -> Optional[Dict[str, Any]]:
    if not fields:
        return None
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
                         violation_type: str, snapshot_path: Optional[str] = None,
                         area_group_id: Optional[int] = None, area_group: Optional[str] = None,
                         clean_snapshot_path: Optional[str] = None,
                         detection_metadata: Optional[Dict[str, Any]] = None,
                         cooldown_seconds: float = config.VIOLATION_COOLDOWN_SECONDS) -> Optional[int]:
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
                           reviewed_by: Optional[str], review_comment: Optional[str],
                           dataset_image_path: str, dataset_label_path: str) -> Optional[Dict[str, Any]]:
    if review_status not in {"confirmed", "false_positive"}:
        raise ValueError("review_status must be confirmed or false_positive")
    async with get_pool().acquire() as conn:
        async with conn.transaction():
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
            return dict(row)


async def update_violation_video_path(violation_id: int, video_path: str) -> bool:
    async with get_pool().acquire() as conn:
        result = await conn.execute(
            "UPDATE violations SET video_path = $2 WHERE id = $1",
            violation_id, video_path,
        )
        return result.endswith("1")


async def get_user_by_login(login: str) -> Optional[Dict[str, Any]]:
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


async def set_username(user_id: int, username: str) -> Optional[Dict[str, Any]]:
    normalized = username.strip().lower()
    if not normalized or len(normalized) > 64:
        raise ValueError("username must contain between 1 and 64 characters")
    async with get_pool().acquire() as conn:
        async with conn.transaction():
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
    async with get_pool().acquire() as conn:
        async with conn.transaction():
            result = await conn.execute(
                "UPDATE users SET password_hash = $2, updated_at = now() WHERE id = $1",
                user_id, password_hash,
            )
            if not result.endswith("1"):
                return False
            await conn.execute("DELETE FROM auth_sessions WHERE user_id = $1", user_id)
            return True


async def create_user(email: str, password_hash: str, role: str) -> Dict[str, Any]:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO users (email, password_hash, role)
               VALUES (lower($1), $2, $3)
               RETURNING id, email, username, role, is_active, created_at""",
            email, password_hash, role,
        )
        return dict(row)


async def list_users() -> List[Dict[str, Any]]:
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, email, username, role, is_active, created_at FROM users ORDER BY email"
        )
        return [dict(row) for row in rows]


async def set_user_active(user_id: int, active: bool) -> Optional[Dict[str, Any]]:
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


async def get_auth_session(session_id: str, expires_at) -> Optional[Dict[str, Any]]:
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


async def accept_user_invite(token_hash: str, password_hash: str) -> Optional[Dict[str, Any]]:
    async with get_pool().acquire() as conn:
        async with conn.transaction():
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


async def write_audit_log(action: str, actor_user_id: Optional[int] = None,
                          actor_email: Optional[str] = None,
                          target_type: Optional[str] = None,
                          target_id: Optional[str] = None,
                          details: Optional[Dict[str, Any]] = None) -> None:
    async with get_pool().acquire() as conn:
        await conn.execute(
            """INSERT INTO audit_log
               (actor_user_id, actor_email, action, target_type, target_id, details)
               VALUES ($1,$2,$3,$4,$5,$6::jsonb)""",
            actor_user_id, actor_email, action, target_type, target_id,
            json.dumps(details or {}),
        )


async def get_audit_log(limit: int = 100) -> List[Dict[str, Any]]:
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, actor_user_id, actor_email, action, target_type, target_id,
                      details, created_at
               FROM audit_log ORDER BY created_at DESC LIMIT $1""",
            limit,
        )
        return [dict(row) for row in rows]


async def mark_alert_dispatched(violation_id: int):
    async with get_pool().acquire() as conn:
        await conn.execute("UPDATE violations SET alert_dispatched = true WHERE id = $1", violation_id)


async def enqueue_alert_job(violation_id: int, rule_id: int, channel: str,
                            payload: Dict[str, Any]) -> None:
    async with get_pool().acquire() as conn:
        await conn.execute(
            """INSERT INTO alert_outbox
               (violation_id, rule_id, channel, payload)
               VALUES ($1, $2, $3, $4::jsonb)
               ON CONFLICT (violation_id, rule_id, channel) DO NOTHING""",
            violation_id, rule_id, channel, json.dumps(payload),
        )


async def claim_alert_job() -> Optional[Dict[str, Any]]:
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
                           max_attempts: int, error: Optional[str]) -> None:
    retry_delay = min(2 ** max(attempt_count - 1, 0), 300)
    async with get_pool().acquire() as conn:
        async with conn.transaction():
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


async def log_alert_dispatch(violation_id: int, rule_id: Optional[int], channel: str, success: bool):
    async with get_pool().acquire() as conn:
        await conn.execute(
            """INSERT INTO alert_dispatch_log (violation_id, rule_id, channel, success)
               VALUES ($1,$2,$3,$4)""",
            violation_id, rule_id, channel, success,
        )


async def get_violation_by_id(violation_id: int) -> Optional[Dict[str, Any]]:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM violations WHERE id = $1", violation_id)
        return dict(row) if row else None


async def get_recent_violations(limit: int = 50, camera_id: Optional[str] = None,
                                 violation_type: Optional[str] = None,
                                 area_group_id: Optional[int] = None,
                                 since_epoch: Optional[float] = None,
                                 offset: int = 0) -> List[Dict[str, Any]]:
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
    params.extend((limit, offset))
    query += f" ORDER BY epoch_time DESC, id DESC LIMIT ${len(params) - 1} OFFSET ${len(params)}"

    async with get_pool().acquire() as conn:
        rows = await conn.fetch(query, *params)
        return [dict(r) for r in rows]


async def delete_violations_before(cutoff_epoch: float) -> Dict[str, int]:
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


async def get_compliance_stats(window_hours: int = 24) -> Dict[str, Any]:
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
    area_group_id: Optional[int] = None,
    camera_id: Optional[str] = None,
) -> Dict[str, Any]:
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


async def get_camera_statuses() -> List[Dict[str, Any]]:
    async with get_pool().acquire() as conn:
        rows = await conn.fetch("SELECT * FROM camera_status")
        return [dict(r) for r in rows]


async def delete_camera_status(camera_id: str):
    async with get_pool().acquire() as conn:
        await conn.execute("DELETE FROM camera_status WHERE camera_id = $1", camera_id)


async def list_rules(enabled_only: bool = False) -> List[Dict[str, Any]]:
    query = "SELECT * FROM alert_rules"
    if enabled_only:
        query += " WHERE enabled = true"
    query += " ORDER BY id ASC"
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(query)
        return [dict(r) for r in rows]


async def create_rule(name, camera_id, zone, violation_type, min_severity,
                       channels, cooldown_seconds, email_recipients, enabled=True) -> Dict[str, Any]:
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


async def update_rule(rule_id: int, **fields) -> Optional[Dict[str, Any]]:
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
