"""
main.py — FastAPI app: dynamic camera lifecycle, area groups, WebSocket
video/events, REST API, alert rules CRUD, snapshot image serving.

KEY ENDPOINT for the "cameras missing from frontend" fix:
  POST /api/cameras/sync-seed
    Runs db.sync_seed_data() on demand to add missing cameras and disable
    retired seed cameras without overwriting operator-managed stream URLs.
    Exposed as a "Sync Default Cameras" button in the dashboard's Cameras tab.
"""
import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import sys
import time
import traceback
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlencode, urlsplit, urlunsplit

import cv2
import numpy as np
import uvicorn
from fastapi import (
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator

from . import auth, config, db, feedback
from .alert_manager import AlertManager
from .camera_manager import CameraManager
from .detector import PPEDetector
from .face_recognition import FaceRecognitionWorker
from .ws_hub import hub

logger = logging.getLogger("ppe.api")


def _out(msg: str):
    print(msg, file=sys.stderr, flush=True)


_detector: PPEDetector | None = None
_alert_manager = AlertManager()
_camera_manager: CameraManager | None = None
_face_recognition_worker: FaceRecognitionWorker | None = None


def _get_detector():
    return _detector


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        _out("=" * 70)
        _out("INGPCE PPE COMPLIANCE SYSTEM - STARTING UP")
        _out("=" * 70)
        global _detector, _camera_manager, _face_recognition_worker
        _out(f"[1/3] Loading detection model from {os.path.abspath(config.MODEL_PATH)} ...")
        if not os.path.isfile(config.MODEL_PATH):
            raise FileNotFoundError(
                f"\n\n================ MODEL FILE NOT FOUND ================\n"
                f"Expected a YOLO model at: {os.path.abspath(config.MODEL_PATH)}\n"
                f"Download a model before starting the app:\n"
                f"  python scripts/download_model.py\n"
                f"========================================================\n"
            )
        _detector = PPEDetector()
        _out("      Model loaded successfully.")

        _out("[2/3] Connecting to PostgreSQL...")
        await db.init_pool()
        os.makedirs(config.SNAPSHOT_DIR, exist_ok=True)

        _out("[3/3] Starting camera workers...")
        if config.FACE_ENABLED:
            _face_recognition_worker = FaceRecognitionWorker()
            await _face_recognition_worker.start()
        _camera_manager = CameraManager(
            _get_detector, _alert_manager, _hikvision_playback_url,
            _open_playback_capture, _face_recognition_worker,
        )
        await _camera_manager.reconcile_all()
        await _alert_manager.start()

        _out("=" * 70)
        _out("STARTUP COMPLETE - application is ready.")
        _out("=" * 70)

    except BaseException:
        _out("!" * 70)
        _out("STARTUP FAILED - FULL TRACEBACK BELOW")
        _out("!" * 70)
        _out(traceback.format_exc())
        _out("!" * 70)
        raise

    yield

    if _camera_manager:
        await _camera_manager.stop_all()
    if _face_recognition_worker:
        await _face_recognition_worker.stop()
    await _alert_manager.stop()
    await db.close_pool()


app = FastAPI(title="INGPCE Industrial Safety Operations", lifespan=lifespan)

DASHBOARD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dashboard")
if os.path.isdir(DASHBOARD_DIR):
    app.mount("/static", StaticFiles(directory=DASHBOARD_DIR), name="static")


def _minimum_role(path: str, method: str) -> str:
    if path == "/api/alerts/power-automate/test":
        return "manager"
    if path == "/api/violations/cleanup":
        return "admin"
    if (path == "/api/audit-log" or path.startswith("/api/auth/users")
            or path.startswith("/api/auth/invites")):
        return "admin"
    if method in {"GET", "HEAD", "OPTIONS"}:
        return "viewer"
    if path == "/api/auth/logout":
        return "viewer"
    if re.fullmatch(r"/api/violations/\d+/review", path):
        return "operator"
    return "manager"


@app.middleware("http")
async def authenticated_api(request: Request, call_next):
    if not config.AUTH_ENABLED:
        response = await call_next(request)
    elif (request.url.path in {
            "/", "/api/health", "/api/auth/login", "/api/auth/me",
            "/api/auth/accept-invite",
        } or request.url.path.startswith("/static/")):
        request.state.user = None
        response = await call_next(request)
    else:
        session = await auth.authenticate_token(request.cookies.get("ppe_session", ""))
        if session is None:
            return JSONResponse({"detail": "Authentication required"}, status_code=401)
        request.state.user = session
        method = request.method.upper()
        if method not in {"GET", "HEAD", "OPTIONS"}:
            if not secrets.compare_digest(
                request.headers.get("x-csrf-token", "").encode("utf-8"),
                session["csrf_token"].encode("utf-8"),
            ):
                return JSONResponse({"detail": "CSRF token missing or invalid"}, status_code=403)

        role_order = {"viewer": 0, "operator": 1, "manager": 2, "admin": 3}
        required_role = _minimum_role(request.url.path, method)
        if role_order.get(session["role"], -1) < role_order[required_role]:
            return JSONResponse({"detail": f"{required_role.title()} role required"}, status_code=403)
        response = await call_next(request)

    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


def _public_user(user: dict[str, object]) -> dict[str, object]:
    return {
        "id": user["id"], "email": user["email"],
        "username": user.get("username"), "role": user["role"],
    }


def _require_face_manager(request: Request) -> dict[str, Any]:
    if not config.AUTH_ENABLED:
        raise HTTPException(
            status_code=403,
            detail="Authentication must be enabled to access biometric data",
        )
    actor = getattr(request.state, "user", None)
    if actor is None or actor.get("role") not in {"admin", "manager"}:
        raise HTTPException(status_code=403, detail="Manager role required")
    return actor


def _require_face_admin(request: Request) -> dict[str, Any]:
    actor = _require_face_manager(request)
    if actor.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    return actor


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"

def _redact_rtsp_credentials(rtsp_url: str) -> str:
    parsed = urlsplit(rtsp_url)
    if parsed.hostname is None:
        return rtsp_url
    netloc = parsed.netloc.rsplit("@", 1)[-1]
    return urlunsplit(parsed._replace(netloc=netloc, query="", fragment=""))


def _preserve_rtsp_credentials(rtsp_url: str, existing_url: str | None) -> str:
    if not existing_url:
        return rtsp_url
    parsed = urlsplit(rtsp_url)
    existing = urlsplit(existing_url)
    if not parsed.netloc:
        return rtsp_url
    host = parsed.netloc.rsplit("@", 1)[-1]
    if parsed.username is None and existing.username is not None:
        netloc = f"{existing.netloc.rsplit('@', 1)[0]}@{host}"
    elif (
        parsed.username is not None
        and not parsed.password
        and existing.password is not None
    ):
        netloc = f"{parsed.username}:{existing.password}@{host}"
    else:
        netloc = parsed.netloc
    return urlunsplit(parsed._replace(
        netloc=netloc,
        query=parsed.query or existing.query,
        fragment=parsed.fragment or existing.fragment,
    ))


def _validate_rtsp_url(value: str | None) -> str | None:
    if value is None:
        return value
    if value != value.strip() or any(character.isspace() for character in value):
        raise ValueError("RTSP URL must not contain whitespace")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise ValueError("RTSP URL is malformed or has an invalid port") from error
    if (
        parsed.scheme.lower() != "rtsp"
        or not hostname
        or not parsed.path
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ValueError(
            "Enter a valid RTSP URL with the rtsp:// scheme, host, and stream path"
        )
    return value


def _validate_camera_urls(*urls: str | None) -> None:
    for url in urls:
        try:
            _validate_rtsp_url(url)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error


def _public_camera(camera: dict) -> dict:
    public = dict(camera)
    for key in ("rtsp_url", "main_stream_url", "sub_stream_url"):
        if public.get(key):
            public[key] = _redact_rtsp_credentials(public[key])
    return public


def _hikvision_playback_url(rtsp_url: str, start: datetime, end: datetime) -> str:
    parsed = urlsplit(rtsp_url)
    channel_match = re.fullmatch(
        r"/Streaming/Channels/(\d+)/?", parsed.path, flags=re.IGNORECASE,
    )
    if parsed.scheme.lower() != "rtsp" or not parsed.hostname or not channel_match:
        raise HTTPException(
            status_code=422,
            detail="Playback requires a Hikvision-compatible RTSP channel URL",
        )

    playback_path = f"/Streaming/tracks/{channel_match.group(1)}"
    start_utc = start.astimezone(UTC)
    end_utc = end.astimezone(UTC)
    query = urlencode({
        "starttime": start_utc.strftime("%Y%m%dt%H%M%Sz").lower(),
        "endtime": end_utc.strftime("%Y%m%dt%H%M%Sz").lower(),
    })
    return urlunsplit((parsed.scheme, parsed.netloc, playback_path, query, ""))


def _open_playback_capture(playback_url: str):
    capture = cv2.VideoCapture(
        playback_url,
        cv2.CAP_FFMPEG,
        [
            cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 10000,
            cv2.CAP_PROP_READ_TIMEOUT_MSEC, 10000,
        ],
    )
    if not capture.isOpened():
        capture.release()
        raise RuntimeError("The NVR did not open the requested playback stream")
    available, frame = capture.read()
    if not available:
        capture.release()
        raise RuntimeError("No recorded video is available for the selected time")
    return capture, frame


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)


class InviteIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    role: Literal["admin", "manager", "operator", "viewer"]


class AcceptInviteIn(BaseModel):
    token: str = Field(min_length=20, max_length=256)
    password: str = Field(min_length=12, max_length=256)


class FaceDatasetImportIn(BaseModel):
    consent_name: str | None = Field(default=None, max_length=200)
    consent_date: str | None = Field(default=None, max_length=10)
    consent_reference: str | None = Field(default=None, max_length=500)


class FaceMatchReviewIn(BaseModel):
    decision: Literal["Confirmed", "Rejected", "Reassigned"]
    person_id: int | None = Field(default=None, ge=1)
    comment: str | None = Field(default=None, max_length=2000)
    add_crop_embedding: bool = False

    @model_validator(mode="after")
    def validate_reassignment(self):
        if self.decision == "Reassigned" and self.person_id is None:
            raise ValueError("Reassignment requires an enrolled person")
        if self.decision != "Reassigned" and self.person_id is not None:
            raise ValueError("A person can only be selected for reassignment")
        if self.add_crop_embedding and self.decision not in {"Confirmed", "Reassigned"}:
            raise ValueError("An incident crop can only be added after confirmation")
        return self


@app.get("/api/auth/me")
async def auth_me(request: Request):
    if not config.AUTH_ENABLED:
        return {"auth_required": False, "authenticated": False}
    session = await auth.authenticate_token(request.cookies.get("ppe_session", ""))
    if session is None:
        return {"auth_required": True, "authenticated": False}
    return {
        "auth_required": True,
        "authenticated": True,
        "user": {
            "id": session["user_id"], "email": session["email"],
            "username": session.get("username"),
            "role": session["role"],
        },
        "csrf_token": session["csrf_token"],
    }


@app.post("/api/auth/login")
async def auth_login(payload: LoginIn, request: Request, response: Response):
    if not config.AUTH_ENABLED:
        raise HTTPException(status_code=404, detail="Authentication is disabled")
    ip_address = _client_ip(request)
    locked_until = await db.login_lock_expiry(ip_address)
    if locked_until:
        raise HTTPException(status_code=429, detail="Too many failed logins; try again later")
    user = await db.get_user_by_login(payload.email.strip())
    valid = user is not None and auth.verify_password(payload.password, user["password_hash"])
    if not valid or not user["is_active"]:
        await db.record_login_failure(ip_address)
        await db.write_audit_log(
            "login_failed", actor_email=payload.email.strip().lower(),
            details={"remote_ip": ip_address},
        )
        raise HTTPException(status_code=401, detail="Invalid username/email or password")

    await db.clear_login_failures(ip_address)
    token, csrf_token, expires_at = await auth.create_session(user)
    response.set_cookie(
        "ppe_session", token, httponly=True, secure=config.AUTH_COOKIE_SECURE,
        samesite="lax", max_age=expires_at - int(time.time()), path="/",
    )
    await db.write_audit_log(
        "login_succeeded", int(user["id"]), user["email"],
        details={"remote_ip": ip_address},
    )
    return {"user": _public_user(user), "csrf_token": csrf_token}


@app.post("/api/auth/logout")
async def auth_logout(request: Request, response: Response):
    token = auth.parse_session_token(
        request.cookies.get("ppe_session", ""), config.AUTH_SESSION_SECRET,
    )
    user = getattr(request.state, "user", None)
    if token:
        await db.delete_auth_session(token[0])
    if user:
        await db.write_audit_log(
            "logout", int(user["user_id"]), user["email"],
        )
    response.delete_cookie(
        "ppe_session", path="/", secure=config.AUTH_COOKIE_SECURE,
        httponly=True, samesite="lax",
    )
    return {"logged_out": True}


@app.post("/api/auth/invites")
async def create_invite(payload: InviteIn, request: Request):
    email = payload.email.strip().lower()
    if email.count("@") != 1 or "." not in email.rsplit("@", 1)[1]:
        raise HTTPException(status_code=422, detail="A valid email address is required")
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + timedelta(hours=24)
    await db.create_user_invite(auth.invite_token_hash(token), email, payload.role, expires_at)
    actor = request.state.user
    await db.write_audit_log(
        "user_invited", int(actor["user_id"]), actor["email"],
        target_type="user", target_id=email, details={"role": payload.role},
    )
    return {
        "email": email, "role": payload.role, "invite_token": token,
        "expires_at": expires_at.isoformat(),
    }


@app.post("/api/auth/accept-invite")
async def accept_invite(payload: AcceptInviteIn):
    user = await db.accept_user_invite(
        auth.invite_token_hash(payload.token), auth.hash_password(payload.password),
    )
    if user is None:
        raise HTTPException(status_code=400, detail="Invite is invalid, expired, or already used")
    await db.write_audit_log(
        "invite_accepted", int(user["id"]), user["email"],
        target_type="user", target_id=str(user["id"]),
    )
    return {"user": _public_user(user)}


@app.get("/api/auth/users")
async def auth_list_users():
    return await db.list_users()


@app.get("/api/audit-log")
async def get_audit_log(limit: int = 100):
    if not 1 <= limit <= 500:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 500")
    return await db.get_audit_log(limit)


@app.delete("/api/auth/users/{user_id}")
async def auth_revoke_user(user_id: int, request: Request):
    actor = request.state.user
    if int(actor["user_id"]) == user_id:
        raise HTTPException(status_code=409, detail="Administrators cannot deactivate their own account")
    user = await db.set_user_active(user_id, False)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    await db.write_audit_log(
        "user_deactivated", int(actor["user_id"]), actor["email"],
        target_type="user", target_id=str(user_id),
    )
    return user


async def _authorize_websocket(websocket: WebSocket) -> bool:
    if not config.AUTH_ENABLED:
        return True
    origin = websocket.headers.get("origin", "")
    host = websocket.headers.get("host", "")
    if not origin or not host or origin.split("://", 1)[-1].rstrip("/") != host:
        await websocket.close(code=4403, reason="Same-origin websocket required")
        return False
    session = await auth.authenticate_token(websocket.cookies.get("ppe_session", ""))
    if session is None:
        await websocket.close(code=4401, reason="Authentication required")
        return False
    return True


@app.websocket("/ws/video/{camera_id}")
async def ws_video(websocket: WebSocket, camera_id: str):
    if not await _authorize_websocket(websocket):
        return
    await websocket.accept()
    q = await hub.subscribe_video(camera_id)
    try:
        while True:
            frame_bytes = await q.get()
            await websocket.send_bytes(frame_bytes)
    except WebSocketDisconnect:
        pass
    finally:
        await hub.unsubscribe_video(camera_id, q)


@app.websocket("/ws/video")
async def ws_video_wall(websocket: WebSocket, camera_ids: str = Query(default="")):
    if not await _authorize_websocket(websocket):
        return
    requested_ids = list(dict.fromkeys(
        camera_id.strip() for camera_id in camera_ids.split(",") if camera_id.strip()
    ))
    running_ids = set(_camera_manager.running_camera_ids()) if _camera_manager else set()
    selected_ids = [camera_id for camera_id in requested_ids if camera_id in running_ids]
    if not selected_ids:
        await websocket.close(code=4404, reason="No active cameras were requested")
        return

    await websocket.accept()
    queues = {
        camera_id: await hub.subscribe_video(camera_id)
        for camera_id in selected_ids
    }
    pending = {
        asyncio.create_task(queue.get()): camera_id
        for camera_id, queue in queues.items()
    }
    try:
        while pending:
            completed, _ = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED,
            )
            for task in completed:
                camera_id = pending.pop(task)
                frame_bytes = task.result()
                camera_id_bytes = camera_id.encode("utf-8")
                packet = len(camera_id_bytes).to_bytes(2, "big") + camera_id_bytes + frame_bytes
                await websocket.send_bytes(packet)
                pending[asyncio.create_task(queues[camera_id].get())] = camera_id
    except WebSocketDisconnect:
        pass
    finally:
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for camera_id, queue in queues.items():
            await hub.unsubscribe_video(camera_id, queue)


@app.websocket("/ws/fullscreen/{camera_id}")
async def ws_fullscreen_video(websocket: WebSocket, camera_id: str):
    if not await _authorize_websocket(websocket):
        return
    if _camera_manager is None or not await _camera_manager.acquire_fullscreen_stream(camera_id):
        await websocket.close(code=4404, reason="Camera is not available")
        return
    await websocket.accept()
    subscriber_key = f"fullscreen:{camera_id}"
    q = await hub.subscribe_video(subscriber_key)
    try:
        while True:
            frame_bytes = await q.get()
            await websocket.send_bytes(frame_bytes)
    except WebSocketDisconnect:
        pass
    finally:
        await hub.unsubscribe_video(subscriber_key, q)
        await _camera_manager.release_fullscreen_stream(camera_id)


@app.websocket("/ws/events")
async def ws_events(websocket: WebSocket):
    if not await _authorize_websocket(websocket):
        return
    await websocket.accept()
    q = await hub.subscribe_events()
    try:
        while True:
            event = await q.get()
            await websocket.send_text(json.dumps(event))
    except WebSocketDisconnect:
        pass
    finally:
        await hub.unsubscribe_events(q)


@app.get("/")
async def root():
    index_path = os.path.join(DASHBOARD_DIR, "index.html")
    if os.path.isfile(index_path):
        return FileResponse(index_path)
    return {"service": "PPE Compliance Detection System"}


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ws": hub.stats(),
        "camera_streams": (
            _camera_manager.stream_telemetry() if _camera_manager else {}
        ),
    }


@app.get("/api/face/readiness")
async def face_readiness(request: Request):
    actor = _require_face_admin(request)
    try:
        enrolled_people = await db.count_active_face_enrollments()
        from .face_readiness import get_face_readiness

        result = await asyncio.to_thread(
            get_face_readiness, enrolled_people,
        )
    except Exception as exc:
        logger.error("Could not check face-recognition readiness (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="Could not check face-recognition readiness",
        ) from exc
    await db.write_audit_log(
        "face_readiness_checked", int(actor["user_id"]), actor["email"],
        target_type="face_recognition_readiness",
        details={
            "ready_for_recognition": result["ready_for_recognition"],
            "feature_enabled": result["feature_enabled"],
            "active_enrolled_people": enrolled_people,
        },
    )
    return result

@app.get("/api/face/status")
async def face_recognition_status(request: Request):
    actor = _require_face_manager(request)
    enabled_types = sorted(config.FACE_VIOLATION_TYPES)
    await db.write_audit_log(
        "face_recognition_status_viewed",
        int(actor["user_id"]), actor["email"],
        target_type="face_recognition_status",
        details={
            "feature_enabled": config.FACE_ENABLED,
            "enabled_violation_types": enabled_types,
        },
    )
    return {
        "feature_enabled": config.FACE_ENABLED,
        "enabled_violation_types": enabled_types,
        "track_reuse_seconds": config.FACE_TRACK_REUSE_SECONDS,
        "quality_min_by_violation": config.FACE_QUALITY_MIN_BY_VIOLATION,
    }


@app.get("/api/camera-status/history")
async def camera_status_history(
    window: Literal["1h", "24h", "7d", "30d"] = "30d",
    area_group_id: int | None = None,
    camera_id: str | None = None,
):
    now = datetime.now(UTC)
    durations = {"1h": timedelta(hours=1), "24h": timedelta(days=1),
                 "7d": timedelta(days=7), "30d": timedelta(days=30)}
    start = now - durations[window]
    try:
        report = await db.get_camera_status_history(start, now, area_group_id, camera_id)
    except Exception as exc:
        logger.exception("Could not load camera status history")
        raise HTTPException(
            status_code=503, detail="Could not load camera status history",
        ) from exc
    cameras = report["cameras"]
    events_by_camera: dict[str, list[dict]] = {}
    for event in report["previous"] + report["events"]:
        events_by_camera.setdefault(event["camera_id"], []).append(event)
    samples_by_camera: dict[str, list[dict]] = {}
    for sample in report.get("previous_samples", []) + report.get("samples", []):
        samples_by_camera.setdefault(sample["camera_id"], []).append(sample)
    bucket_count = {"1h": 12, "24h": 24, "7d": 7, "30d": 30}[window]
    bucket_seconds = durations[window].total_seconds() / bucket_count
    edges = [
        start + timedelta(seconds=bucket_seconds * index)
        for index in range(bucket_count + 1)
    ]
    camera_rows = []
    uptime_values = []
    online = 0
    offline = 0
    logs = []

    for camera in cameras:
        camera_id = camera["camera_id"]
        events = sorted(
            events_by_camera.get(camera_id, []),
            key=lambda event: event["observed_at"],
        )
        connected_seconds = [0.0] * bucket_count
        known_seconds = [0.0] * bucket_count

        def add_segment(segment_start, segment_end, connected_state):
            if segment_end <= segment_start:
                return
            first_bucket = max(
                0, int((segment_start - start).total_seconds() // bucket_seconds),
            )
            last_bucket = min(
                bucket_count - 1,
                int(max(0, (segment_end - start).total_seconds() - 1e-6) // bucket_seconds),
            )
            for bucket_index in range(first_bucket, last_bucket + 1):
                overlap_start = max(segment_start, edges[bucket_index])
                overlap_end = min(segment_end, edges[bucket_index + 1])
                seconds = max(0.0, (overlap_end - overlap_start).total_seconds())
                known_seconds[bucket_index] += seconds
                if connected_state:
                    connected_seconds[bucket_index] += seconds

        samples = sorted(
            samples_by_camera.get(camera_id, []),
            key=lambda sample: sample["observed_at"],
        )
        for index, sample in enumerate(samples):
            sample_start = max(start, sample["observed_at"])
            next_sample = (
                samples[index + 1]["observed_at"]
                if index + 1 < len(samples) else now
            )
            # A stale heartbeat indicates the application was not observing
            # the camera, so do not count the gap as online or offline.
            sample_end = min(
                next_sample,
                sample["observed_at"] + timedelta(seconds=90),
                now,
            )
            add_segment(sample_start, sample_end, sample["connected"])

        buckets = []
        for index in range(bucket_count):
            known = known_seconds[index]
            up = connected_seconds[index]
            ratio = (up / known) if known else None
            state_name = (
                "unknown" if ratio is None else
                "online" if ratio >= 0.8 else
                "offline" if ratio <= 0.2 else "partial"
            )
            buckets.append({
                "start": edges[index].isoformat(),
                "end": edges[index + 1].isoformat(),
                "state": state_name,
                "uptime_pct": round(ratio * 100, 1) if ratio is not None else None,
            })

        known_total = sum(known_seconds)
        uptime_pct = (
            round(sum(connected_seconds) / known_total * 100, 1)
            if known_total else None
        )
        if uptime_pct is not None:
            uptime_values.append(uptime_pct)
        status_time = camera["last_updated"]
        is_currently_online = bool(
            camera["enabled"] and camera["connected"]
            and status_time and (now - status_time).total_seconds() <= 20
        )
        if is_currently_online:
            online += 1
        elif camera["enabled"]:
            offline += 1

        camera_rows.append({
            "camera_id": camera_id,
            "name": camera["name"],
            "area_group_name": camera["area_group_name"] or "Unassigned",
            "enabled": camera["enabled"],
            "connected": is_currently_online,
            "uptime_pct": uptime_pct,
            "buckets": buckets,
        })
        for event in events:
            if start < event["observed_at"] <= now:
                logs.append({
                    "camera_id": camera_id,
                    "camera_name": camera["name"],
                    "area_group_name": camera["area_group_name"] or "Unassigned",
                    "status": "ONLINE" if event["connected"] else "OFFLINE",
                    "observed_at": event["observed_at"].isoformat(),
                })

    logs.sort(key=lambda log: log["observed_at"], reverse=True)
    return {
        "window": window,
        "start": start.isoformat(),
        "end": now.isoformat(),
        "summary": {
            "uptime_pct": round(sum(uptime_values) / len(uptime_values), 1)
            if uptime_values else None,
            "online": online,
            "offline": offline,
            "total": len(cameras),
        },
        "bucket_count": bucket_count,
        "cameras": camera_rows,
        "logs": logs[:200],
    }


class PowerAutomateTestIn(BaseModel):
    recipient: str = Field(
        min_length=6,
        max_length=254,
        pattern=r"^[^@\s]+@[^@\s.]+(?:\.[^@\s.]+)+$",
    )


@app.post("/api/alerts/power-automate/test")
async def send_power_automate_test(payload: PowerAutomateTestIn, request: Request):
    if not config.POWER_AUTOMATE_WEBHOOK_URL:
        raise HTTPException(
            status_code=503,
            detail="Configure PPE_POWER_AUTOMATE_WEBHOOK_URL in the server .env first",
        )
    occurred_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = (
        "INGPCE Power Automate test message\n"
        f"Recipient: {payload.recipient}\n"
        f"Sent at: {occurred_at}"
    )
    delivered = await asyncio.to_thread(
        _alert_manager._send_power_automate,
        [payload.recipient],
        "INGPCE Power Automate Test",
        body,
        "TEST",
        "Power Automate setup",
        "Test message",
        occurred_at,
        "test",
    )
    if not delivered:
        raise HTTPException(
            status_code=502,
            detail="Power Automate test failed. Check the flow URL, run history, and server log.",
        )
    actor = getattr(request.state, "user", None)
    if actor:
        await db.write_audit_log(
            "power_automate_test_sent", int(actor["user_id"]), actor["email"],
            target_type="power_automate_test", target_id=payload.recipient,
        )
    return {"sent": True, "recipient": payload.recipient}


class CameraIn(BaseModel):
    camera_id: str
    name: str
    rtsp_url: str | None = None
    main_stream_url: str | None = None
    sub_stream_url: str | None = None
    area_group_id: int | None = None
    required_ppe: list[str] = ["helmet", "vest"]
    enabled: bool = True


class CameraUpdate(BaseModel):
    name: str | None = None
    rtsp_url: str | None = None
    main_stream_url: str | None = None
    sub_stream_url: str | None = None
    area_group_id: int | None = None
    required_ppe: list[str] | None = None
    enabled: bool | None = None


class CameraZoneIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    polygon: list[list[float]]
    enabled: bool = True

    @field_validator("polygon")
    @classmethod
    def validate_polygon(cls, polygon):
        if len(polygon) < 3:
            raise ValueError("A zone polygon must have at least 3 points")
        if any(
            len(point) != 2 or any(not 0 <= value <= 1 for value in point)
            for point in polygon
        ):
            raise ValueError("Zone points must be normalized [x, y] values in [0, 1]")
        return polygon


class CameraZoneUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    polygon: list[list[float]] | None = None
    enabled: bool | None = None

    @field_validator("polygon")
    @classmethod
    def validate_optional_polygon(cls, polygon):
        if polygon is None:
            return polygon
        return CameraZoneIn(name="validation", polygon=polygon).polygon


@app.get("/api/cameras")
async def list_cameras():
    try:
        cameras = await db.list_cameras()
        statuses = {s["camera_id"]: s for s in await db.get_camera_statuses()}
    except Exception as e:
        logger.exception("Database error while listing cameras")
        raise HTTPException(status_code=503, detail="Could not list cameras") from e

    out = []
    for cam in cameras:
        persisted = statuses.get(cam["camera_id"], {})
        live_running = _camera_manager.is_running(cam["camera_id"]) if _camera_manager else False
        out.append({
            "camera_id": cam["camera_id"],
            "name": cam["name"],
            "ip_address": urlsplit(
                cam.get("main_stream_url") or cam["rtsp_url"]
            ).hostname,
            "area_group_id": cam["area_group_id"],
            "area_group_name": cam["area_group_name"],
            "required_ppe": cam["required_ppe"],
            "enabled": cam["enabled"],
            "connected": persisted.get("connected", False) if live_running else False,
            "worker_running": live_running,
        })
    return out


@app.get("/api/cameras/{camera_id}/playback")
async def camera_playback(
    camera_id: str,
    start: datetime,
    duration_minutes: int = Query(default=10, ge=1, le=120),
):
    try:
        camera = await db.get_camera(camera_id)
    except Exception as exc:
        logger.exception("Database error while loading camera playback settings")
        raise HTTPException(
            status_code=503, detail="Could not load camera playback settings",
        ) from exc
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")

    end = start + timedelta(minutes=duration_minutes)
    playback_url = _hikvision_playback_url(
        camera.get("main_stream_url") or camera["rtsp_url"], start, end,
    )
    try:
        capture, first_frame = await asyncio.to_thread(
            _open_playback_capture, playback_url,
        )
    except Exception as exc:
        logger.exception("Could not open recorded playback for camera %s", camera_id)
        raise HTTPException(
            status_code=502,
            detail="Could not open playback. Check the selected time and camera NVR recording.",
        ) from exc

    async def frames():
        frame = first_frame
        try:
            while True:
                encoded, jpeg = await asyncio.to_thread(cv2.imencode, ".jpg", frame)
                if encoded:
                    image = jpeg.tobytes()
                    yield (
                        b"--frame\r\nContent-Type: image/jpeg\r\n"
                        + f"Content-Length: {len(image)}\r\n\r\n".encode("ascii")
                        + image + b"\r\n"
                    )
                available, frame = await asyncio.to_thread(capture.read)
                if not available:
                    break
        except Exception:
            logger.exception("Recorded playback stream failed for camera %s", camera_id)
            raise
        finally:
            await asyncio.to_thread(capture.release)

    return StreamingResponse(
        frames(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/cameras/{camera_id}")
async def get_camera(camera_id: str):
    cam = await db.get_camera(camera_id)
    if not cam:
        raise HTTPException(status_code=404, detail="Camera not found")
    return _public_camera(cam)


@app.post("/api/cameras")
async def add_camera(camera: CameraIn):
    _validate_camera_urls(
        camera.rtsp_url, camera.main_stream_url, camera.sub_stream_url,
    )
    existing = await db.get_camera(camera.camera_id)
    if existing:
        raise HTTPException(status_code=409, detail=f"Camera '{camera.camera_id}' already exists")

    if camera.main_stream_url:
        main_stream_url = camera.main_stream_url
        sub_stream_url = (
            camera.sub_stream_url
            or config.sub_stream_url_for(main_stream_url)
        )
    elif camera.sub_stream_url:
        main_stream_url = config.main_stream_url_for(camera.sub_stream_url)
        sub_stream_url = camera.sub_stream_url
    elif camera.rtsp_url:
        main_stream_url, sub_stream_url = config.camera_stream_urls_for(
            camera.rtsp_url,
        )
    else:
        raise HTTPException(
            status_code=422,
            detail="A main_stream_url or legacy rtsp_url is required",
        )
    await db.create_camera(
        camera.camera_id, camera.name, main_stream_url,
        camera.area_group_id, camera.required_ppe, camera.enabled,
        main_stream_url=main_stream_url, sub_stream_url=sub_stream_url,
    )
    full = await db.get_camera(camera.camera_id)
    if camera.enabled:
        await _camera_manager.start_camera(full)
    return _public_camera(full)


@app.post("/api/cameras/sync-seed")
async def sync_seed_cameras():
    """Synchronize configured camera feeds while retaining old incident data."""
    result = await db.sync_seed_data()
    for camera_id in result["updated_camera_ids"]:
        await _camera_manager.refresh_camera(camera_id)
    for camera_id in result["disabled_camera_ids"]:
        await _camera_manager.stop_camera(camera_id)
    for camera_id in result["newly_created_camera_ids"]:
        full = await db.get_camera(camera_id)
        if full and full["enabled"]:
            await _camera_manager.start_camera(full)
    return result


@app.put("/api/cameras/{camera_id}")
async def edit_camera(camera_id: str, update: CameraUpdate):
    _validate_camera_urls(
        update.rtsp_url, update.main_stream_url, update.sub_stream_url,
    )
    existing = await db.get_camera(camera_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Camera not found")

    fields = {k: v for k, v in update.model_dump().items() if v is not None}
    if "area_group_id" in update.model_fields_set:
        fields["area_group_id"] = update.area_group_id
    if not fields:
        return _public_camera(existing)

    for key in ("rtsp_url", "main_stream_url", "sub_stream_url"):
        if key in fields:
            fields[key] = _preserve_rtsp_credentials(
                fields[key], existing.get(key),
            )

    if (
        fields.get("rtsp_url") == existing.get("rtsp_url")
        and "main_stream_url" not in fields
        and "sub_stream_url" not in fields
    ):
        fields.pop("rtsp_url")

    if "rtsp_url" in fields and "main_stream_url" not in fields:
        main_stream_url, sub_stream_url = config.camera_stream_urls_for(
            fields["rtsp_url"],
        )
        fields["main_stream_url"] = main_stream_url
        fields.setdefault("sub_stream_url", sub_stream_url)
    elif "main_stream_url" in fields and "sub_stream_url" not in fields:
        fields["sub_stream_url"] = config.sub_stream_url_for(
            fields["main_stream_url"],
        )
    if "main_stream_url" in fields:
        fields["rtsp_url"] = fields["main_stream_url"]

    rtsp_changed = any(
        key in fields and fields[key] != existing.get(key)
        for key in ("rtsp_url", "main_stream_url", "sub_stream_url")
    )
    enabled_changed = "enabled" in fields and fields["enabled"] != existing["enabled"]

    await db.update_camera(camera_id, **fields)
    updated = await db.get_camera(camera_id)

    if rtsp_changed or enabled_changed or "required_ppe" in fields:
        if updated["enabled"]:
            await _camera_manager.refresh_camera(
                camera_id,
                restart_stream=rtsp_changed or enabled_changed,
            )
        else:
            await _camera_manager.stop_camera(camera_id)

    return _public_camera(updated)


@app.post("/api/cameras/{camera_id}/refresh")
async def refresh_camera(camera_id: str):
    ok = await _camera_manager.refresh_camera(camera_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Camera not found")
    return {"camera_id": camera_id, "refreshed": True}


@app.get("/api/cameras/{camera_id}/zones")
async def get_camera_zones(camera_id: str):
    if not await db.get_camera(camera_id):
        raise HTTPException(status_code=404, detail="Camera not found")
    return await db.list_camera_zones(camera_id, enabled_only=False)


@app.post("/api/cameras/{camera_id}/zones")
async def add_camera_zone(camera_id: str, zone: CameraZoneIn):
    if not await db.get_camera(camera_id):
        raise HTTPException(status_code=404, detail="Camera not found")
    created = await db.create_camera_zone(
        camera_id, zone.name.strip(), zone.polygon, zone.enabled,
    )
    await _camera_manager.refresh_camera(camera_id)
    return created


@app.put("/api/cameras/{camera_id}/zones/{zone_id}")
async def edit_camera_zone(camera_id: str, zone_id: int, update: CameraZoneUpdate):
    fields = {key: value for key, value in update.model_dump().items() if value is not None}
    updated = await db.update_camera_zone(zone_id, camera_id, **fields)
    if updated is None:
        raise HTTPException(status_code=404, detail="Camera zone not found")
    await _camera_manager.refresh_camera(camera_id)
    return updated


@app.delete("/api/cameras/{camera_id}/zones/{zone_id}")
async def remove_camera_zone(camera_id: str, zone_id: int):
    if not await db.delete_camera_zone(zone_id, camera_id):
        raise HTTPException(status_code=404, detail="Camera zone not found")
    await _camera_manager.refresh_camera(camera_id)
    return {"deleted": zone_id}


@app.delete("/api/cameras/{camera_id}")
async def remove_camera(camera_id: str):
    existing = await db.get_camera(camera_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Camera not found")
    await _camera_manager.stop_camera(camera_id)
    await db.delete_camera(camera_id)
    return {"deleted": camera_id}


class AreaGroupIn(BaseModel):
    name: str
    description: str = ""


class AreaGroupUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


@app.get("/api/area-groups")
async def list_area_groups():
    try:
        return await db.list_area_groups()
    except Exception as e:
        logger.exception("Database error while listing area groups")
        raise HTTPException(status_code=503, detail="Could not list area groups") from e


@app.post("/api/area-groups")
async def add_area_group(group: AreaGroupIn):
    return await db.create_area_group(group.name, group.description)


@app.put("/api/area-groups/{group_id}")
async def edit_area_group(group_id: int, update: AreaGroupUpdate):
    fields = {k: v for k, v in update.model_dump().items() if v is not None}
    updated = await db.update_area_group(group_id, **fields)
    if not updated:
        raise HTTPException(status_code=404, detail="Area group not found")
    return updated


@app.delete("/api/area-groups/{group_id}")
async def remove_area_group(group_id: int):
    ok = await db.delete_area_group(group_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Area group not found")
    return {"deleted": group_id}


@app.get("/api/violations")
async def violations(request: Request,
                      limit: int = Query(default=50, ge=1, le=500),
                      offset: int = Query(default=0, ge=0, le=100000),
                      camera_id: str | None = None,
                      violation_type: str | None = None, area_group_id: int | None = None,
                      since_epoch: float | None = None,
                      employee_code: str | None = None,
                      department: str | None = None):
    face_filter_actor = None
    if employee_code is not None or department is not None:
        face_filter_actor = _require_face_manager(request)
    try:
        if offset == 0 and employee_code is None and department is None:
            rows = await db.get_recent_violations(
                limit, camera_id, violation_type, area_group_id, since_epoch,
            )
        else:
            rows = await db.get_recent_violations(
                limit, camera_id, violation_type, area_group_id, since_epoch,
                offset=offset, employee_code=employee_code,
                department=department,
            )
    except Exception as e:
        logger.exception("Database error while loading violations")
        raise HTTPException(status_code=503, detail="Could not load violations") from e

    if face_filter_actor is not None:
        await db.write_audit_log(
            "face_confirmed_incidents_filtered",
            int(face_filter_actor["user_id"]), face_filter_actor["email"],
            target_type="violations",
            details={
                "employee_filter_used": employee_code is not None,
                "department_filter_used": department is not None,
                "result_count": len(rows),
            },
        )
    available_ids = (
        await db.get_face_match_availability([int(row["id"]) for row in rows])
        if config.FACE_ENABLED else set()
    )
    for row in rows:
        row["face_recognition_enabled"] = config.FACE_ENABLED
        row["face_match_available"] = int(row["id"]) in available_ids
        row["snapshot_url"] = f"/api/snapshot/{row['id']}" if row.get("snapshot_path") else None
        row["clean_snapshot_url"] = (
            f"/api/violations/{row['id']}/evidence/clean"
            if row.get("clean_snapshot_path") else None
        )
        row["video_url"] = f"/api/violations/{row['id']}/evidence/video" if row.get("video_path") else None
    return rows


@app.get("/api/violations/{violation_id}/face-match")
async def get_violation_face_match(
        violation_id: int, request: Request):
    actor = _require_face_manager(request)
    violation = await db.get_violation_by_id(violation_id)
    if violation is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    match = await db.get_face_match(violation_id)
    await db.write_audit_log(
        "face_identity_viewed", int(actor["user_id"]), actor["email"],
        target_type="incident", target_id=str(violation_id),
        details={"view": "match_details", "match_available": match is not None},
    )
    if match is None:
        return {"incident_id": violation_id, "status": "Not available"}
    encrypted_face_crop = match.pop("face_crop", None)
    match["face_crop_available"] = encrypted_face_crop is not None
    for key in ("face_quality", "face_bbox", "top3_candidates"):
        if isinstance(match.get(key), str):
            match[key] = json.loads(match[key])
    return match


@app.get("/api/violations/{violation_id}/face-crop")
async def get_violation_face_crop(violation_id: int, request: Request):
    actor = _require_face_manager(request)
    match = await db.get_face_match(violation_id)
    if (
        match is None
        or match["status"] not in {"Identified", "Suggested"}
        or not match.get("face_crop")
    ):
        raise HTTPException(status_code=404, detail="Face crop is not available")
    from .face_dataset import _encryption_key, decrypt_face_crop

    try:
        face_crop = await asyncio.to_thread(
            decrypt_face_crop, match["face_crop"], _encryption_key(),
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="Face crop is unavailable") from exc
    await db.write_audit_log(
        "face_identity_viewed", int(actor["user_id"]), actor["email"],
        target_type="incident", target_id=str(violation_id),
        details={"view": "face_crop"},
    )
    return Response(content=face_crop, media_type="image/jpeg")


@app.get("/api/face/enrollments")
async def list_face_enrollments(request: Request):
    actor = _require_face_manager(request)
    enrollments = await db.list_face_enrollments()
    await db.write_audit_log(
        "face_enrollments_viewed", int(actor["user_id"]), actor["email"],
        target_type="face_enrollments",
        details={"record_count": len(enrollments)},
    )
    return enrollments


@app.post("/api/face/import")
async def import_face_dataset_api(payload: FaceDatasetImportIn, request: Request):
    actor = _require_face_manager(request)
    from .face_dataset import (
        ConsentError,
        FaceModelUnavailable,
        import_face_dataset,
    )

    try:
        result = await import_face_dataset(
            consent_name=payload.consent_name,
            consent_date=payload.consent_date,
            consent_reference=payload.consent_reference,
            actor_user_id=int(actor["user_id"]),
            actor_email=actor["email"],
        )
    except ConsentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FaceModelUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (OSError, ValueError, RuntimeError) as exc:
        logger.error("Face dataset import failed (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="Face dataset import failed; sensitive details were omitted",
        ) from exc
    except Exception as exc:
        logger.error("Face dataset import failed (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="Face dataset import failed; sensitive details were omitted",
        ) from exc
    result.pop("report_path", None)
    result["report_url"] = f"/api/face/import-runs/{result['run_id']}/report"
    return result


@app.get("/api/face/import-runs")
async def list_face_import_runs(request: Request):
    actor = _require_face_manager(request)
    runs = await db.get_face_import_runs()
    await db.write_audit_log(
        "face_import_history_viewed", int(actor["user_id"]), actor["email"],
        target_type="face_import_runs",
        details={"record_count": len(runs)},
    )
    return runs


@app.get("/api/face/import-runs/{run_id}/report")
async def get_face_import_report(run_id: int, request: Request):
    actor = _require_face_manager(request)
    run = await db.get_face_import_run(run_id)
    if run is None or not run.get("report_path"):
        raise HTTPException(status_code=404, detail="Import report not found")
    report_root = Path(config.FACE_REPORT_DIR).resolve()
    try:
        report_path = Path(run["report_path"]).resolve(strict=True)
        report_path.relative_to(report_root)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="Import report not found") from exc
    await db.write_audit_log(
        "face_import_report_viewed", int(actor["user_id"]), actor["email"],
        target_type="face_import_run", target_id=str(run_id),
    )
    return FileResponse(report_path, media_type="text/csv")


def _remove_private_face_photos(photo_paths: list[str]) -> int:
    photo_root = Path(config.FACE_PHOTO_DIR).resolve()
    failures = 0
    for photo_path in photo_paths:
        try:
            resolved_path = Path(photo_path).resolve()
            resolved_path.relative_to(photo_root)
        except (OSError, ValueError):
            logger.warning("Skipped an enrollment photo outside the private photo directory")
            failures += 1
            continue
        try:
            resolved_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning(
                "Could not remove an enrollment photo (%s)", type(exc).__name__,
            )
            failures += 1
    return failures


@app.post("/api/face/enrollments/{person_id}/deactivate")
async def deactivate_face_enrollment(person_id: int, request: Request):
    actor = _require_face_manager(request)
    result = await db.remove_face_enrollment(person_id, permanently=False)
    if result is None:
        raise HTTPException(status_code=404, detail="Enrollment not found")
    photo_removal_failures = _remove_private_face_photos(result["photo_paths"])
    await db.write_audit_log(
        "face_enrollment_deactivated", int(actor["user_id"]), actor["email"],
        target_type="face_enrollment", target_id=str(person_id),
        details={
            "embeddings_removed": True,
            "photo_removal_failures": photo_removal_failures,
        },
    )
    if photo_removal_failures:
        raise HTTPException(
            status_code=503,
            detail="Enrollment data was removed, but stored photos require administrator cleanup",
        )
    return {"person_id": person_id, "active": False}


@app.delete("/api/face/enrollments/{person_id}")
async def delete_face_enrollment(person_id: int, request: Request):
    actor = _require_face_admin(request)
    result = await db.remove_face_enrollment(person_id, permanently=True)
    if result is None:
        raise HTTPException(status_code=404, detail="Enrollment not found")
    photo_removal_failures = _remove_private_face_photos(result["photo_paths"])
    await db.write_audit_log(
        "face_enrollment_deleted", int(actor["user_id"]), actor["email"],
        target_type="face_enrollment", target_id=str(person_id),
        details={
            "embeddings_removed": True,
            "photo_removal_failures": photo_removal_failures,
        },
    )
    if photo_removal_failures:
        raise HTTPException(
            status_code=503,
            detail="Enrollment data was removed, but stored photos require administrator cleanup",
        )
    return {"person_id": person_id, "deleted": True}


async def _add_reviewed_incident_embedding(
        violation_id: int, person_id: int, actor: dict[str, Any],
) -> bool:
    from .face_dataset import (
        InsightFaceAnalyzer,
        _encryption_key,
        decrypt_face_crop,
        encrypt_embedding,
    )

    enrollment = await db.get_face_enrollment(person_id)
    if enrollment is None or not enrollment["active"]:
        raise HTTPException(status_code=422, detail="Selected employee is not enrolled")
    match = await db.get_face_match(violation_id)
    if match is None or not match.get("face_crop"):
        raise HTTPException(status_code=422, detail="Incident face crop is not available")
    try:
        crop_bytes = await asyncio.to_thread(
            decrypt_face_crop, match["face_crop"], _encryption_key(),
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="Incident face crop is unavailable") from exc
    image = cv2.imdecode(np.frombuffer(crop_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.size == 0:
        raise HTTPException(status_code=422, detail="Incident face crop is not available")
    analyzer = await asyncio.to_thread(InsightFaceAnalyzer)
    embedding, measurements = await asyncio.to_thread(analyzer.analyze, image)
    ok, encoded = await asyncio.to_thread(
        cv2.imencode, ".jpg", image,
        [cv2.IMWRITE_JPEG_QUALITY, config.JPEG_QUALITY],
    )
    if not ok:
        raise HTTPException(status_code=503, detail="Could not prepare the incident crop")
    image_hash = hashlib.sha256(encoded.tobytes()).hexdigest()
    embedding_id = await db.insert_face_embedding(
        person_id=person_id,
        embedding=encrypt_embedding(embedding, _encryption_key()),
        quality_score=float(measurements["quality_score"]),
        image_hash=image_hash,
        photo_path=None,
        model_name=analyzer.model_name,
        model_version=analyzer.model_version,
        source="reviewer-confirmed incident crop",
    )
    if embedding_id is None:
        return False
    await db.write_audit_log(
        "face_incident_embedding_added",
        int(actor["user_id"]), actor["email"],
        target_type="face_enrollment", target_id=str(person_id),
        details={"source": "reviewer-confirmed incident crop"},
    )
    return True


@app.post("/api/violations/{violation_id}/face-review")
async def review_violation_face_match(
        violation_id: int, payload: FaceMatchReviewIn, request: Request):
    actor = _require_face_manager(request)
    if payload.add_crop_embedding and payload.decision not in {"Confirmed", "Reassigned"}:
        raise HTTPException(
            status_code=422,
            detail="An incident crop can only be added after confirming or reassigning a match",
        )
    if payload.add_crop_embedding and actor.get("role") != "admin":
        raise HTTPException(
            status_code=403,
            detail="Admin approval is required to add an incident crop",
        )
    result = await db.review_face_match(
        violation_id, payload.decision, int(actor["user_id"]),
        payload.comment, payload.person_id,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Face match not found")
    await db.write_audit_log(
        "face_match_reviewed", int(actor["user_id"]), actor["email"],
        target_type="incident", target_id=str(violation_id),
        details={"decision": payload.decision},
    )
    embedding_added = False
    if payload.add_crop_embedding:
        if result["matched_person_id"] is None:
            raise HTTPException(status_code=422, detail="A reviewed employee match is required")
        person_id = int(result["matched_person_id"])
        embedding_added = await _add_reviewed_incident_embedding(
            violation_id, person_id, actor,
        )
    return {"review": result, "embedding_added": embedding_added}


@app.post("/api/face/incidents/{violation_id}/reprocess")
async def reprocess_face_match(violation_id: int, request: Request):
    actor = _require_face_manager(request)
    if not config.FACE_ENABLED:
        raise HTTPException(status_code=409, detail="Face recognition is disabled")
    queued = await db.requeue_face_recognition_job(violation_id)
    if not queued:
        raise HTTPException(
            status_code=422,
            detail="Only incidents of enabled face-recognition types can be reprocessed",
        )
    await db.write_audit_log(
        "face_match_reprocess_requested", int(actor["user_id"]), actor["email"],
        target_type="incident", target_id=str(violation_id),
    )
    return {"incident_id": violation_id, "status": "Queued"}


@app.post("/api/face/reprocess-batch")
async def reprocess_face_match_batch(
        request: Request, limit: int = Query(default=100, ge=1, le=500)):
    actor = _require_face_manager(request)
    if not config.FACE_ENABLED:
        raise HTTPException(status_code=409, detail="Face recognition is disabled")
    incident_ids = await db.requeue_face_recognition_batch(limit)
    await db.write_audit_log(
        "face_match_batch_reprocess_requested",
        int(actor["user_id"]), actor["email"],
        target_type="incident_face_matches",
        details={"incident_count": len(incident_ids)},
    )
    return {"queued": len(incident_ids)}

@app.get("/api/face/matches")
async def list_face_matches(
        request: Request,
        violation_type: Literal[
            "no_helmet", "no_vest", "phone_detected",
        ] | None = None,
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0, le=100000)):
    actor = _require_face_manager(request)
    rows = await db.list_face_matches(violation_type, limit, offset)
    await db.write_audit_log(
        "face_matches_filtered",
        int(actor["user_id"]), actor["email"],
        target_type="incident_face_matches",
        details={
            "violation_type": violation_type,
            "result_count": len(rows),
            "offset": offset,
        },
    )
    return rows


@app.get("/api/face/analytics")
async def face_analytics(
        request: Request,
        window_hours: int = Query(default=168, ge=1, le=8760),
        employee_code: str | None = None,
        department: str | None = None,
        violation_type: Literal[
            "no_helmet", "no_vest", "phone_detected",
        ] | None = None):
    actor = _require_face_manager(request)
    since_epoch = time.time() - window_hours * 60 * 60
    rows = await db.get_face_repeat_violations(
        since_epoch, department, employee_code, violation_type,
    )
    await db.write_audit_log(
        "face_analytics_viewed", int(actor["user_id"]), actor["email"],
        target_type="face_analytics",
        details={
            "record_count": len(rows),
            "window_hours": window_hours,
            "violation_type": violation_type,
        },
    )
    return rows


@app.post("/api/violations/cleanup")
async def cleanup_old_violations(request: Request):
    cutoff_epoch = time.time() - 2 * 24 * 60 * 60
    try:
        result = await db.delete_violations_before(cutoff_epoch)
    except Exception as e:
        logger.exception("Could not clean old violations")
        raise HTTPException(status_code=503, detail="Could not clean old violations") from e
    actor = getattr(request.state, "user", None)
    await db.write_audit_log(
        "old_violations_cleaned",
        int(actor["user_id"]) if actor else None,
        actor["email"] if actor else None,
        target_type="violations",
        details=result,
    )
    return result


@app.get("/api/stats")
async def stats(window_hours: int = Query(default=24, ge=1, le=720)):
    try:
        return JSONResponse(await db.get_compliance_stats(window_hours))
    except Exception as e:
        logger.exception("Database error while loading compliance stats")
        raise HTTPException(status_code=503, detail="Could not load compliance stats") from e


@app.get("/api/export/violations.csv")
async def export_violations_csv(limit: int = Query(default=5000, ge=1, le=10000)):
    import csv
    import io
    rows = await db.get_recent_violations(limit)
    buf = io.StringIO()
    if rows:
        writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=violations.csv"},
    )


@app.get("/api/snapshot/{violation_id}")
async def get_snapshot(violation_id: int):
    violation = await db.get_violation_by_id(violation_id)
    if not violation or not violation.get("snapshot_path"):
        raise HTTPException(status_code=404, detail="No snapshot found for this violation")
    path = violation["snapshot_path"]
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"Snapshot file missing on disk: {path}")
    return FileResponse(path, media_type="image/jpeg")


@app.get("/api/violations/{violation_id}/evidence/{kind}")
async def get_violation_evidence(violation_id: int, kind: Literal["annotated", "clean", "video"]):
    violation = await db.get_violation_by_id(violation_id)
    if not violation:
        raise HTTPException(status_code=404, detail="Violation not found")
    path = {
        "annotated": violation.get("snapshot_path"),
        "clean": violation.get("clean_snapshot_path"),
        "video": violation.get("video_path"),
    }[kind]
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"{kind.title()} evidence is not available")
    media_type = "video/mp4" if kind == "video" else "image/jpeg"
    return FileResponse(path, media_type=media_type)


class ViolationReviewIn(BaseModel):
    status: Literal["confirmed", "false_positive"]
    comment: str | None = None
    correction_type: Literal["false_person", "ppe_corrected"] | None = None
    corrected_class: Literal["helmet", "vest", "gloves", "boots", "goggles", "mask", "arc_flash_suit"] | None = None
    corrected_box: list[int] | None = None

    @model_validator(mode="after")
    def validate_correction(self):
        if self.correction_type == "false_person":
            if self.status != "false_positive":
                raise ValueError("False-person correction must be reviewed as a false positive")
            if self.corrected_class is not None or self.corrected_box is not None:
                raise ValueError("False-person correction cannot include PPE labels")
        elif self.correction_type == "ppe_corrected":
            if self.status != "false_positive":
                raise ValueError("PPE correction must be reviewed as a false positive")
            if self.corrected_class is None or self.corrected_box is None:
                raise ValueError("PPE correction requires a class and a drawn box")
        elif self.corrected_class is not None or self.corrected_box is not None:
            raise ValueError("Corrected PPE fields require a correction type")
        return self


@app.post("/api/violations/{violation_id}/review")
async def review_violation(violation_id: int, review: ViolationReviewIn, request: Request):
    violation = await db.get_violation_by_id(violation_id)
    if not violation:
        raise HTTPException(status_code=404, detail="Violation not found")
    try:
        image_path, label_path = feedback.export_reviewed_violation(
            violation, review.status, config.FEEDBACK_DATASET_DIR,
            review.correction_type, review.corrected_class, review.corrected_box,
        )
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    updated = None
    try:
        updated = await db.review_violation(
            violation_id, review.status,
            request.state.user["email"] if getattr(request.state, "user", None) else None,
            review.comment or review.correction_type, image_path, label_path,
        )
    finally:
        if updated is None:
            for path in (image_path, label_path):
                try:
                    Path(path).unlink(missing_ok=True)
                except OSError:
                    pass
    if not updated:
        raise HTTPException(status_code=404, detail="Violation not found")
    superseded_paths = updated.pop("_superseded_feedback_paths", [])
    for old_path in superseded_paths:
        try:
            Path(old_path).unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove superseded feedback file: %s", old_path, exc_info=True)
    reviewer = getattr(request.state, "user", None)
    await db.write_audit_log(
        "violation_reviewed",
        int(reviewer["user_id"]) if reviewer else None,
        reviewer["email"] if reviewer else None,
        target_type="violation", target_id=str(violation_id),
        details={"status": review.status},
    )
    updated["feedback_image_path"] = image_path
    updated["feedback_label_path"] = label_path
    return updated


class RuleIn(BaseModel):
    name: str
    camera_id: str | None = None
    zone: str | None = None
    violation_type: Literal[
        "no_helmet", "no_arc_flash_suit", "phone_detected",
    ] = "no_helmet"
    min_severity: int = 1
    channels: list[Literal["power_automate"]] = Field(
        default_factory=lambda: ["power_automate"],
    )
    cooldown_seconds: int = 300
    email_recipients: list[str] = Field(default_factory=list)
    enabled: bool = True


class RuleUpdate(BaseModel):
    name: str | None = None
    camera_id: str | None = None
    zone: str | None = None
    violation_type: Literal[
        "no_helmet", "no_arc_flash_suit", "phone_detected",
    ] | None = None
    min_severity: int | None = None
    channels: list[Literal["power_automate"]] | None = None
    cooldown_seconds: int | None = None
    email_recipients: list[str] | None = None
    enabled: bool | None = None


@app.get("/api/rules")
async def get_rules():
    try:
        return await db.list_rules()
    except Exception as e:
        logger.exception("Database error while loading rules")
        raise HTTPException(status_code=503, detail="Could not load alert rules") from e


@app.post("/api/rules")
async def add_rule(rule: RuleIn):
    created = await db.create_rule(
        rule.name, rule.camera_id, rule.zone, rule.violation_type, rule.min_severity,
        rule.channels, rule.cooldown_seconds, rule.email_recipients, rule.enabled,
    )
    await _alert_manager.rules_engine.force_refresh()
    return created


@app.put("/api/rules/{rule_id}")
async def edit_rule(rule_id: int, rule: RuleUpdate):
    fields = {k: v for k, v in rule.model_dump().items() if v is not None}
    updated = await db.update_rule(rule_id, **fields)
    if not updated:
        raise HTTPException(status_code=404, detail="Rule not found")
    await _alert_manager.rules_engine.force_refresh()
    return updated


@app.delete("/api/rules/{rule_id}")
async def remove_rule(rule_id: int):
    ok = await db.delete_rule(rule_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Rule not found")
    await _alert_manager.rules_engine.force_refresh()
    return {"deleted": rule_id}


def run():
    try:
        uvicorn.run(app, host=config.API_HOST, port=config.API_PORT)
    except BaseException:
        _out("!" * 70)
        _out("UVICORN FAILED TO START. Full traceback:")
        _out("!" * 70)
        _out(traceback.format_exc())
        _out("!" * 70)
        raise


if __name__ == "__main__":
    run()
