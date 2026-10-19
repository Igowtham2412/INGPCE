"""
main.py — FastAPI app: dynamic camera lifecycle, area groups, WebSocket
video/events, REST API, alert rules CRUD, snapshot image serving.

KEY ENDPOINT for the "cameras missing from frontend" fix:
  POST /api/cameras/sync-seed
    Runs db.sync_seed_data() on demand to add cameras, update configured
    stream URLs, and disable retired seed cameras without deleting history.
    Exposed as a "Sync Default Cameras" button in the dashboard's Cameras tab.
"""
import os
import re
import asyncio
import logging
import secrets
import sys
import time
import json
import traceback
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, Optional, List, Literal
from urllib.parse import urlencode, urlsplit, urlunsplit

import cv2
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
import uvicorn

from . import auth, config, db
from .detector import PPEDetector
from .alert_manager import AlertManager
from .camera_manager import CameraManager
from . import feedback
from .ws_hub import hub

logger = logging.getLogger("ppe.api")


def _out(msg: str):
    print(msg, file=sys.stderr, flush=True)


_detector: Optional[PPEDetector] = None
_alert_manager = AlertManager()
_camera_manager: Optional[CameraManager] = None


def _get_detector():
    return _detector


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        _out("=" * 70)
        _out("INGPCE PPE COMPLIANCE SYSTEM - STARTING UP")
        _out("=" * 70)
        global _detector, _camera_manager
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
        _camera_manager = CameraManager(_get_detector, _alert_manager)
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


def _public_user(user: Dict[str, object]) -> Dict[str, object]:
    return {
        "id": user["id"], "email": user["email"],
        "username": user.get("username"), "role": user["role"],
    }


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


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
    start_utc = start.astimezone(timezone.utc)
    end_utc = end.astimezone(timezone.utc)
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
    expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
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
    return {"status": "ok", "time": time.strftime("%Y-%m-%d %H:%M:%S"), "ws": hub.stats()}


@app.get("/api/camera-status/history")
async def camera_status_history(
    window: Literal["1h", "24h", "7d", "30d"] = "30d",
    area_group_id: Optional[int] = None,
    camera_id: Optional[str] = None,
):
    now = datetime.now(timezone.utc)
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
    events_by_camera: Dict[str, List[Dict]] = {}
    for event in report["previous"] + report["events"]:
        events_by_camera.setdefault(event["camera_id"], []).append(event)
    samples_by_camera: Dict[str, List[Dict]] = {}
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
    occurred_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
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
    rtsp_url: str
    area_group_id: Optional[int] = None
    required_ppe: List[str] = ["helmet", "vest"]
    enabled: bool = True


class CameraUpdate(BaseModel):
    name: Optional[str] = None
    rtsp_url: Optional[str] = None
    area_group_id: Optional[int] = None
    required_ppe: Optional[List[str]] = None
    enabled: Optional[bool] = None


class CameraZoneIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    polygon: List[List[float]]
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
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    polygon: Optional[List[List[float]]] = None
    enabled: Optional[bool] = None

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
            "ip_address": urlsplit(cam["rtsp_url"]).hostname,
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
    playback_url = _hikvision_playback_url(camera["rtsp_url"], start, end)
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
    return cam


@app.post("/api/cameras")
async def add_camera(camera: CameraIn):
    existing = await db.get_camera(camera.camera_id)
    if existing:
        raise HTTPException(status_code=409, detail=f"Camera '{camera.camera_id}' already exists")

    await db.create_camera(
        camera.camera_id, camera.name, camera.rtsp_url, camera.area_group_id,
        camera.required_ppe, camera.enabled,
    )
    full = await db.get_camera(camera.camera_id)
    if camera.enabled:
        await _camera_manager.start_camera(full)
    return full


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
    existing = await db.get_camera(camera_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Camera not found")

    fields = {k: v for k, v in update.model_dump().items() if v is not None}
    if not fields:
        return existing

    rtsp_changed = "rtsp_url" in fields and fields["rtsp_url"] != existing["rtsp_url"]
    enabled_changed = "enabled" in fields and fields["enabled"] != existing["enabled"]

    await db.update_camera(camera_id, **fields)
    updated = await db.get_camera(camera_id)

    if rtsp_changed or enabled_changed or "required_ppe" in fields:
        if updated["enabled"]:
            await _camera_manager.refresh_camera(camera_id)
        else:
            await _camera_manager.stop_camera(camera_id)

    return updated


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
    name: Optional[str] = None
    description: Optional[str] = None


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
async def violations(limit: int = Query(default=50, ge=1, le=500),
                      camera_id: Optional[str] = None,
                      violation_type: Optional[str] = None, area_group_id: Optional[int] = None,
                      since_epoch: Optional[float] = None):
    try:
        rows = await db.get_recent_violations(
            limit, camera_id, violation_type, area_group_id, since_epoch,
        )
    except Exception as e:
        logger.exception("Database error while loading violations")
        raise HTTPException(status_code=503, detail="Could not load violations") from e

    for row in rows:
        row["snapshot_url"] = f"/api/snapshot/{row['id']}" if row.get("snapshot_path") else None
        row["clean_snapshot_url"] = (
            f"/api/violations/{row['id']}/evidence/clean"
            if row.get("clean_snapshot_path") else None
        )
        row["video_url"] = f"/api/violations/{row['id']}/evidence/video" if row.get("video_path") else None
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
    comment: Optional[str] = None


@app.post("/api/violations/{violation_id}/review")
async def review_violation(violation_id: int, review: ViolationReviewIn, request: Request):
    violation = await db.get_violation_by_id(violation_id)
    if not violation:
        raise HTTPException(status_code=404, detail="Violation not found")
    try:
        image_path, label_path = feedback.export_reviewed_violation(
            violation, review.status, config.FEEDBACK_DATASET_DIR,
        )
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    updated = None
    try:
        updated = await db.review_violation(
            violation_id, review.status,
            request.state.user["email"] if getattr(request.state, "user", None) else None,
            review.comment, image_path, label_path,
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
    camera_id: Optional[str] = None
    zone: Optional[str] = None
    violation_type: Literal["no_helmet"] = "no_helmet"
    min_severity: int = 1
    channels: List[Literal["power_automate"]] = Field(
        default_factory=lambda: ["power_automate"],
    )
    cooldown_seconds: int = 300
    email_recipients: List[str] = Field(default_factory=list)
    enabled: bool = True


class RuleUpdate(BaseModel):
    name: Optional[str] = None
    camera_id: Optional[str] = None
    zone: Optional[str] = None
    violation_type: Optional[Literal["no_helmet"]] = None
    min_severity: Optional[int] = None
    channels: Optional[List[Literal["power_automate"]]] = None
    cooldown_seconds: Optional[int] = None
    email_recipients: Optional[List[str]] = None
    enabled: Optional[bool] = None


@app.get("/api/rules")
async def get_rules():
    try:
        return JSONResponse(await db.list_rules())
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
    return JSONResponse(created)


@app.put("/api/rules/{rule_id}")
async def edit_rule(rule_id: int, rule: RuleUpdate):
    fields = {k: v for k, v in rule.model_dump().items() if v is not None}
    updated = await db.update_rule(rule_id, **fields)
    if not updated:
        raise HTTPException(status_code=404, detail="Rule not found")
    await _alert_manager.rules_engine.force_refresh()
    return JSONResponse(updated)


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
