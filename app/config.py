"""Central settings and the configured seed list for camera feeds."""
import os
import json
from typing import List, Dict
from math import isfinite
from urllib.parse import quote

from dotenv import load_dotenv

load_dotenv()

def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)

def _env_bool(key: str, default: bool) -> bool:
    return _env(key, str(default)).lower() in ("1", "true", "yes")

def _json_env(key: str) -> Dict:
    raw = _env(key)
    if not raw:
        return {}
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be a JSON object")
    return value

def _camera_rois() -> Dict[str, List[List[float]]]:
    rois = _json_env("PPE_CAMERA_ROIS")
    validated = {}
    for camera_id, polygon in rois.items():
        if not isinstance(camera_id, str) or not isinstance(polygon, list) or len(polygon) < 3:
            raise ValueError("PPE_CAMERA_ROIS must map camera IDs to polygons with at least 3 points")
        points = []
        for point in polygon:
            if (not isinstance(point, list) or len(point) != 2
                    or any(not isinstance(value, (int, float)) or not 0 <= value <= 1 for value in point)):
                raise ValueError(f"ROI points for {camera_id} must be normalized [x, y] coordinates between 0 and 1")
            points.append([float(point[0]), float(point[1])])
        validated[camera_id] = points
    return validated

def _rtsp_url(host: str, channel: int) -> str:
    nvr_prefix = f"PPE_NVR_{host.replace('.', '_')}"
    username = quote(_env(f"{nvr_prefix}_USERNAME", _env("PPE_CAMERA_USERNAME", "admin")), safe="")
    password = quote(_env(f"{nvr_prefix}_PASSWORD", _env("PPE_CAMERA_PASSWORD")), safe="")
    return f"rtsp://{username}:{password}@{host}:554/Streaming/Channels/{channel}"

# ---------------------------------------------------------------------------
# 1. SEED DATA - synced on startup AND via the dashboard's "Sync Default
#    Cameras" button / POST /api/cameras/sync-seed
# ---------------------------------------------------------------------------
SEED_AREA_GROUPS: List[Dict] = [
    {"name": "Substation",        "description": "High-voltage electrical substation"},
    {"name": "MCC",                "description": "Motor control center rooms"},
    {"name": "SYLOC",               "description": "SYLOC processing area"},
    {"name": "Pumphouse",            "description": "Pumphouse and pumphouse MCC"},
    {"name": "Yard",                  "description": "Container yard and scrap yard"},
    {"name": "Gates & Security",       "description": "Plant entry/exit gates and security posts"},
    {"name": "Safety",                  "description": "Designated safety zones"},
    {"name": "Facilities",               "description": "Canteen / non-process facilities"},
    {"name": "Process",                   "description": "Chemical process areas (e.g. Caustic)"},
]

SEED_CAMERAS: List[Dict] = [
    {"camera_id": "CAM01", "name": "CAM01", "rtsp_url": _rtsp_url("192.169.0.65", 101),
     "required_ppe": ["helmet", "vest"]},
    {"camera_id": "CAM02", "name": "CAM02", "rtsp_url": _rtsp_url("192.169.0.65", 201),
     "required_ppe": ["helmet", "vest"]},
    {"camera_id": "CAM05", "name": "CAM05", "rtsp_url": _rtsp_url("192.169.0.65", 501),
     "required_ppe": ["helmet", "vest"]},
    {"camera_id": "CAM06", "name": "CAM06", "rtsp_url": _rtsp_url("192.169.0.65", 601),
     "required_ppe": ["helmet", "vest"]},
    {"camera_id": "CAM07", "name": "CAM07", "rtsp_url": _rtsp_url("192.169.0.65", 701),
     "required_ppe": ["helmet", "vest"]},
    {"camera_id": "CAM08", "name": "CAM08", "rtsp_url": _rtsp_url("192.169.0.65", 801),
     "required_ppe": ["helmet", "vest"]},
    {"camera_id": "CAM09", "name": "CAM09", "rtsp_url": _rtsp_url("192.169.0.65", 901),
     "required_ppe": ["helmet", "vest"]},
    {"camera_id": "CAM10", "name": "CAM10", "rtsp_url": _rtsp_url("192.169.0.65", 1001),
     "required_ppe": ["helmet", "vest"]},
    {"camera_id": "CAM11", "name": "CAM11", "rtsp_url": _rtsp_url("192.168.100.65", 501),
     "required_ppe": ["helmet", "vest"]},
    {"camera_id": "CAM12", "name": "CAM12", "rtsp_url": _rtsp_url("192.168.100.65", 602),
     "required_ppe": ["helmet", "vest"]},
    {"camera_id": "CAM13", "name": "CAM13", "rtsp_url": _rtsp_url("192.168.100.65", 701),
     "required_ppe": ["helmet", "vest"]},
    {"camera_id": "CAM14", "name": "CAM14", "rtsp_url": _rtsp_url("192.168.100.65", 801),
     "required_ppe": ["helmet", "vest"]},
    {"camera_id": "CAM15", "name": "CAM15", "rtsp_url": _rtsp_url("192.168.100.65", 901),
     "required_ppe": ["helmet", "vest"]},
    {"camera_id": "CAM16", "name": "CAM16", "rtsp_url": _rtsp_url("192.168.100.65", 201),
     "required_ppe": ["helmet", "vest"]},
    {"camera_id": "CAM17", "name": "CAM17", "rtsp_url": _rtsp_url("192.168.100.65", 302),
     "required_ppe": ["helmet", "vest"]},
    {"camera_id": "CAM18", "name": "CAM18", "rtsp_url": _rtsp_url("192.168.100.65", 601),
     "required_ppe": ["helmet", "vest"]},
    {"camera_id": "CAM19", "name": "CAM19", "rtsp_url": _rtsp_url("192.168.100.65", 1001),
     "required_ppe": ["helmet", "vest"]},
    {"camera_id": "CAM20", "name": "CAM20", "rtsp_url": _rtsp_url("192.168.100.65", 1101),
     "required_ppe": ["helmet", "vest"]},
    {"camera_id": "CAM21", "name": "CAM21", "rtsp_url": _rtsp_url("192.168.100.65", 1201),
     "required_ppe": ["helmet", "vest"]},
    {"camera_id": "CAM22", "name": "CAM22", "rtsp_url": _rtsp_url("192.168.100.65", 1301),
     "required_ppe": ["helmet", "vest"]},
    {"camera_id": "CAM23", "name": "CAM23", "rtsp_url": _rtsp_url("192.168.100.65", 1401),
     "required_ppe": ["helmet", "vest"]},
    {"camera_id": "CAM24", "name": "CAM24", "rtsp_url": _rtsp_url("192.168.100.65", 1502),
     "required_ppe": ["helmet", "vest"]},
]
RETIRED_SEED_CAMERA_IDS = ["CAM03", "CAM04"]

# ---------------------------------------------------------------------------
# 2. MODEL SETTINGS
# ---------------------------------------------------------------------------
MODEL_PATH: str = _env("PPE_MODEL_PATH", "models/best.pt")
DEVICE: str = _env("PPE_DEVICE", "cpu")
CONFIDENCE_THRESHOLD: float = float(_env("PPE_CONF_THRESHOLD", "0.45"))
IOU_THRESHOLD: float = float(_env("PPE_IOU_THRESHOLD", "0.45"))
INFERENCE_IMG_SIZE: int = int(_env("PPE_IMG_SIZE", "640"))
FRAME_SKIP: int = int(_env("PPE_FRAME_SKIP", "2"))
CLASS_CONFIDENCE_THRESHOLDS: Dict[str, float] = {
    str(name): float(value) for name, value in _json_env("PPE_CLASS_CONFIDENCE").items()
}
if any(not 0 <= value <= 1 for value in CLASS_CONFIDENCE_THRESHOLDS.values()):
    raise ValueError("PPE_CLASS_CONFIDENCE values must be between 0 and 1")
CLASS_BOX_FILTERS: Dict[str, Dict[str, float]] = {}
for _name, _filter in _json_env("PPE_CLASS_BOX_FILTERS").items():
    if not isinstance(_filter, dict):
        raise ValueError(f"PPE_CLASS_BOX_FILTERS entry for {_name} must be a JSON object")
    _normalized_filter = {str(key): float(value) for key, value in _filter.items()}
    if set(_normalized_filter) - {"min_area_fraction", "min_aspect_ratio", "max_aspect_ratio"}:
        raise ValueError(f"Unsupported box filter key for {_name}")
    if _normalized_filter.get("min_area_fraction", 0) < 0:
        raise ValueError(f"min_area_fraction for {_name} cannot be negative")
    if _normalized_filter.get("min_aspect_ratio", 0) < 0:
        raise ValueError(f"min_aspect_ratio for {_name} cannot be negative")
    if _normalized_filter.get("max_aspect_ratio", float("inf")) <= 0:
        raise ValueError(f"max_aspect_ratio for {_name} must be positive")
    if _normalized_filter.get("min_aspect_ratio", 0) > _normalized_filter.get("max_aspect_ratio", float("inf")):
        raise ValueError(f"min_aspect_ratio cannot exceed max_aspect_ratio for {_name}")
    CLASS_BOX_FILTERS[str(_name)] = _normalized_filter
CAMERA_ROIS = _camera_rois()
PPE_ASSOCIATION_REGIONS: Dict[str, List[float]] = {
    "helmet": [0.0, 0.4],
    "mask": [0.0, 0.4],
    "goggles": [0.0, 0.45],
    "vest": [0.15, 0.85],
    "gloves": [0.2, 1.0],
    "boots": [0.6, 1.0],
}
for _name, _region in _json_env("PPE_ASSOCIATION_REGIONS").items():
    if (not isinstance(_region, list) or len(_region) != 2
            or any(not isinstance(value, (int, float)) or not 0 <= value <= 1 for value in _region)
            or _region[0] > _region[1]):
        raise ValueError(f"PPE_ASSOCIATION_REGIONS entry for {_name} must be [min_y, max_y] in [0, 1]")
    PPE_ASSOCIATION_REGIONS[str(_name)] = [float(_region[0]), float(_region[1])]
PPE_COMPLIANCE_MODE = _env("PPE_COMPLIANCE_MODE", "explicit_negative").lower()
if PPE_COMPLIANCE_MODE not in {"explicit_negative", "positive_absence"}:
    raise ValueError("PPE_COMPLIANCE_MODE must be explicit_negative or positive_absence")

# ---------------------------------------------------------------------------
# 3. CLASS MAPPING
# ---------------------------------------------------------------------------
CLASS_MAP: Dict[str, str] = {
    "person": "person", "Person": "person",
    "helmet": "helmet", "Helmet": "helmet", "Hardhat": "helmet", "hardhat": "helmet",
    "no-helmet": "no_helmet", "No-Helmet": "no_helmet", "NO-Hardhat": "no_helmet", "no_hardhat": "no_helmet",
    "vest": "vest", "Vest": "vest", "Safety Vest": "vest", "safety_vest": "vest",
    "no-vest": "no_vest", "No-Vest": "no_vest", "NO-Safety Vest": "no_vest",
    "gloves": "gloves", "Gloves": "gloves", "no-gloves": "no_gloves",
    "boots": "boots", "Safety Boots": "boots", "safety_shoe": "boots", "no-boots": "no_boots",
    "goggles": "goggles", "Goggles": "goggles", "no-goggle": "no_goggles",
    "mask": "mask", "Mask": "mask", "no-mask": "no_mask", "NO-Mask": "no_mask",
}

POSITIVE_PPE_CLASSES = {"helmet", "vest", "gloves", "boots", "goggles", "mask"}
NEGATIVE_PPE_CLASSES = {
    "helmet": "no_helmet", "vest": "no_vest", "gloves": "no_gloves",
    "boots": "no_boots", "goggles": "no_goggles", "mask": "no_mask",
}

# ---------------------------------------------------------------------------
# 4. COMPLIANCE LOGIC
# ---------------------------------------------------------------------------
MATCH_OVERLAP_THRESHOLD: float = 0.30
VIOLATION_CONFIRM_FRAMES: int = int(_env("PPE_VIOLATION_CONFIRM_FRAMES", "5"))
TEMPORAL_CONFIRMATION: Dict[str, Dict[str, int]] = {}
for _name, _confirmation in _json_env("PPE_TEMPORAL_CONFIRMATION").items():
    if not isinstance(_confirmation, dict) or set(_confirmation) - {"required", "window"}:
        raise ValueError(f"PPE_TEMPORAL_CONFIRMATION entry for {_name} needs required/window values")
    _required = int(_confirmation.get("required", VIOLATION_CONFIRM_FRAMES))
    _window = int(_confirmation.get("window", max(VIOLATION_CONFIRM_FRAMES, 8)))
    if _required < 1 or _window < _required:
        raise ValueError(f"Invalid N-of-M confirmation values for {_name}: require 1 <= required <= window")
    TEMPORAL_CONFIRMATION[str(_name)] = {"required": _required, "window": _window}
VIOLATION_COOLDOWN_SECONDS: float = float(_env("PPE_VIOLATION_COOLDOWN_SECONDS", "300"))
if VIOLATION_COOLDOWN_SECONDS < 0:
    raise ValueError("PPE_VIOLATION_COOLDOWN_SECONDS cannot be negative")
TRACK_HIGH_THRESHOLD: float = float(_env("PPE_TRACK_HIGH_THRESHOLD", "0.5"))
TRACK_LOW_THRESHOLD: float = float(_env("PPE_TRACK_LOW_THRESHOLD", "0.1"))
TRACK_NEW_THRESHOLD: float = float(_env("PPE_TRACK_NEW_THRESHOLD", "0.6"))
TRACK_BUFFER: int = int(_env("PPE_TRACK_BUFFER", "30"))
TRACK_MATCH_THRESHOLD: float = float(_env("PPE_TRACK_MATCH_THRESHOLD", "0.8"))
if (not 0 <= TRACK_LOW_THRESHOLD <= TRACK_HIGH_THRESHOLD <= 1
        or not 0 <= TRACK_NEW_THRESHOLD <= 1
        or TRACK_BUFFER < 1
        or not 0 <= TRACK_MATCH_THRESHOLD <= 1):
    raise ValueError("Invalid ByteTrack configuration; thresholds must be in range and buffer must be positive")

# ---------------------------------------------------------------------------
# 5. ALERTING
# ---------------------------------------------------------------------------
ALERTS_ENABLED: bool = _env_bool("PPE_ALERTS_ENABLED", True)
POWER_AUTOMATE_WEBHOOK_URL: str = _env("PPE_POWER_AUTOMATE_WEBHOOK_URL", "")
ALERT_OUTBOX_POLL_SECONDS: float = float(_env("PPE_ALERT_OUTBOX_POLL_SECONDS", "1"))
ALERT_OUTBOX_MAX_ATTEMPTS: int = int(_env("PPE_ALERT_OUTBOX_MAX_ATTEMPTS", "5"))
if ALERT_OUTBOX_POLL_SECONDS <= 0 or ALERT_OUTBOX_MAX_ATTEMPTS < 1:
    raise ValueError("Alert outbox polling must be positive and max attempts must be at least 1")

# ---------------------------------------------------------------------------
# 6. POSTGRESQL
# ---------------------------------------------------------------------------
PG_HOST: str = _env("POSTGRES_HOST", "localhost")
PG_PORT: int = int(_env("POSTGRES_PORT", "5432"))
PG_DB: str = _env("POSTGRES_DB", "ppe_compliance")
PG_USER: str = _env("POSTGRES_USER", "ppe_user")
PG_PASSWORD: str = _env("POSTGRES_PASSWORD", "ppe_password")
PG_POOL_MIN: int = int(_env("POSTGRES_POOL_MIN", "4"))
PG_POOL_MAX: int = int(_env("POSTGRES_POOL_MAX", "30"))
PG_CONNECT_RETRY_SECONDS: int = int(_env("POSTGRES_CONNECT_RETRY_SECONDS", "20"))

SNAPSHOT_DIR: str = _env("PPE_SNAPSHOT_DIR", "snapshots")
FEEDBACK_DATASET_DIR: str = _env("PPE_FEEDBACK_DATASET_DIR", "dataset/feedback")
EVIDENCE_CLIP_DIR: str = _env("PPE_EVIDENCE_CLIP_DIR", "evidence/clips")
EVIDENCE_CLIP_PRE_SECONDS: float = float(_env("PPE_EVIDENCE_CLIP_PRE_SECONDS", "5"))
EVIDENCE_CLIP_POST_SECONDS: float = float(_env("PPE_EVIDENCE_CLIP_POST_SECONDS", "5"))
EVIDENCE_CLIP_FPS: float = float(_env("PPE_EVIDENCE_CLIP_FPS", "4"))
EVIDENCE_CLIP_WIDTH: int = int(_env("PPE_EVIDENCE_CLIP_WIDTH", "640"))
CAMERA_STATUS_HISTORY_DAYS: int = int(_env("PPE_CAMERA_STATUS_HISTORY_DAYS", "35"))
if (not isfinite(EVIDENCE_CLIP_PRE_SECONDS) or EVIDENCE_CLIP_PRE_SECONDS < 0
        or not isfinite(EVIDENCE_CLIP_POST_SECONDS) or EVIDENCE_CLIP_POST_SECONDS < 0
        or not isfinite(EVIDENCE_CLIP_FPS) or EVIDENCE_CLIP_FPS <= 0
        or EVIDENCE_CLIP_WIDTH < 64):
    raise ValueError("Invalid evidence clip configuration")
if CAMERA_STATUS_HISTORY_DAYS < 30:
    raise ValueError("PPE_CAMERA_STATUS_HISTORY_DAYS must be at least 30")

# ---------------------------------------------------------------------------
# 7. DASHBOARD / API
# ---------------------------------------------------------------------------
API_HOST: str = _env("PPE_API_HOST", "127.0.0.1")
API_PORT: int = int(_env("PPE_API_PORT", "8080"))
AUTH_ENABLED: bool = _env_bool("PPE_AUTH_ENABLED", False)
AUTH_SESSION_SECRET: str = _env("PPE_SESSION_SECRET")
AUTH_COOKIE_SECURE: bool = _env_bool("PPE_COOKIE_SECURE", False)
AUTH_SESSION_HOURS: int = int(_env("PPE_SESSION_HOURS", "12"))
if AUTH_ENABLED and len(AUTH_SESSION_SECRET.encode("utf-8")) < 32:
    raise ValueError("PPE_SESSION_SECRET must contain at least 32 bytes when PPE_AUTH_ENABLED=true")
if AUTH_ENABLED and AUTH_SESSION_SECRET.lower().startswith("replace-with-"):
    raise ValueError("Replace the PPE_SESSION_SECRET example with a unique random value")
if AUTH_SESSION_HOURS < 1:
    raise ValueError("PPE_SESSION_HOURS must be positive")
if API_HOST not in {"127.0.0.1", "localhost", "::1"} and not AUTH_ENABLED:
    raise ValueError("Refusing public API binding without PPE_AUTH_ENABLED=true")
if API_HOST not in {"127.0.0.1", "localhost", "::1"} and not AUTH_COOKIE_SECURE:
    raise ValueError("Public API binding requires PPE_COOKIE_SECURE=true")
JPEG_QUALITY: int = int(_env("PPE_JPEG_QUALITY", "70"))
BROADCAST_FPS: int = int(_env("PPE_BROADCAST_FPS", "24"))
CAMERA_STATUS_UPDATE_INTERVAL: float = float(_env("PPE_STATUS_UPDATE_INTERVAL", "4"))
CAMERA_RECONNECT_INITIAL_SECONDS: float = float(_env("PPE_CAMERA_RECONNECT_INITIAL_SECONDS", "2"))
CAMERA_RECONNECT_MAX_SECONDS: float = float(_env("PPE_CAMERA_RECONNECT_MAX_SECONDS", "60"))
CAMERA_WATCHDOG_INTERVAL_SECONDS: float = float(_env("PPE_CAMERA_WATCHDOG_INTERVAL_SECONDS", "2"))
if (CAMERA_RECONNECT_INITIAL_SECONDS <= 0
        or CAMERA_RECONNECT_MAX_SECONDS < CAMERA_RECONNECT_INITIAL_SECONDS
        or CAMERA_WATCHDOG_INTERVAL_SECONDS <= 0):
    raise ValueError("Invalid camera reconnect/watchdog configuration")

# ---------------------------------------------------------------------------
# 8. OUTPUT RESOLUTION — fixed broadcast resolution for every camera
# ---------------------------------------------------------------------------
OUTPUT_WIDTH: int = 1920
OUTPUT_HEIGHT: int = 1080
