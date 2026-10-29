"""Central settings and the configured seed list for camera feeds."""
import json
import logging
import os
import re
from math import isfinite
from urllib.parse import quote, urlsplit, urlunsplit

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("ppe.config")

def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)

def _env_bool(key: str, default: bool) -> bool:
    return _env(key, str(default)).lower() in ("1", "true", "yes")

def _face_violation_types() -> frozenset[str]:
    aliases = {
        "missing_helmet": "no_helmet",
        "no_helmet": "no_helmet",
        "no_arc_flash_suit": "no_arc_flash_suit",
        "phone_detected": "phone_detected",
    }
    configured = _env(
        "PPE_FACE_VIOLATION_TYPES",
        "no_helmet,no_arc_flash_suit,phone_detected",
    )
    selected = {"no_helmet", "no_arc_flash_suit", "phone_detected"}
    for raw_value in configured.split(","):
        value = raw_value.strip().lower()
        if not value:
            continue
        violation_type = aliases.get(value)
        if violation_type is None:
            logger.warning(
                "Ignoring unknown PPE_FACE_VIOLATION_TYPES value %r", value,
            )
            continue
        selected.add(violation_type)
    return frozenset(selected)

def _face_quality_min_by_violation() -> dict[str, float]:
    aliases = {
        "missing_helmet": "no_helmet", "no_helmet": "no_helmet",
        "missing_vest": "no_vest", "no_vest": "no_vest",
        "phone_detected": "phone_detected",
    }
    configured = _json_env("PPE_FACE_QUALITY_MIN_BY_VIOLATION")
    thresholds = {}
    for raw_type, raw_threshold in configured.items():
        violation_type = aliases.get(str(raw_type).strip().lower())
        if violation_type is None:
            logger.warning(
                "Ignoring unknown PPE_FACE_QUALITY_MIN_BY_VIOLATION key %r",
                raw_type,
            )
            continue
        if (not isinstance(raw_threshold, (int, float))
                or not 0 <= raw_threshold <= 1):
            raise ValueError(
                "PPE_FACE_QUALITY_MIN_BY_VIOLATION values must be between 0 and 1"
            )
        thresholds[violation_type] = float(raw_threshold)
    return thresholds

def _json_env(key: str) -> dict:
    raw = _env(key)
    if not raw:
        return {}
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be a JSON object")
    return value

def _camera_rois() -> dict[str, list[list[float]]]:
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

def sub_stream_url_for(main_url: str) -> str:
    parsed = urlsplit(main_url)
    match = re.fullmatch(
        r"(/Streaming/Channels/\d+)01/?", parsed.path, flags=re.IGNORECASE,
    )
    if not match:
        return main_url
    return urlunsplit(parsed._replace(path=f"{match.group(1)}02"))

def main_stream_url_for(sub_url: str) -> str:
    parsed = urlsplit(sub_url)
    match = re.fullmatch(
        r"(/Streaming/Channels/\d+)02/?", parsed.path, flags=re.IGNORECASE,
    )
    if not match:
        return sub_url
    return urlunsplit(parsed._replace(path=f"{match.group(1)}01"))

def camera_stream_urls_for(rtsp_url: str) -> tuple[str, str]:
    parsed = urlsplit(rtsp_url)
    match = re.fullmatch(
        r"(/Streaming/Channels/\d+)(01|02)/?",
        parsed.path,
        flags=re.IGNORECASE,
    )
    if match and match.group(2) == "02":
        return main_stream_url_for(rtsp_url), rtsp_url
    if match and match.group(2) == "01":
        return rtsp_url, sub_stream_url_for(rtsp_url)
    return rtsp_url, rtsp_url

# ---------------------------------------------------------------------------
# 1. SEED DATA - synced on startup AND via the dashboard's "Sync Default
#    Cameras" button / POST /api/cameras/sync-seed
# ---------------------------------------------------------------------------
SEED_AREA_GROUPS: list[dict] = [
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

SEED_CAMERAS: list[dict] = [
    {"camera_id": "CAM01", "name": "CAM01", "rtsp_url": _rtsp_url("192.169.0.65", 101),
     "required_ppe": ["helmet", "vest"]},
    {"camera_id": "CAM02", "name": "CAM02", "rtsp_url": _rtsp_url("192.169.0.65", 201),
     "required_ppe": ["helmet", "vest"]},
    {"camera_id": "CAM05", "name": "CAM05", "rtsp_url": _rtsp_url("192.169.0.65", 501),
     "required_ppe": ["helmet", "vest"]},
    {"camera_id": "CAM06", "name": "CAM06", "rtsp_url": _rtsp_url("192.169.0.65", 601),
     "required_ppe": ["helmet", "vest"]},
    {"camera_id": "CAM07", "name": "CAM07", "rtsp_url": _rtsp_url("192.169.0.65", 701),
     "required_ppe": ["helmet", "vest", "arc_flash_suit"]},
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
for _camera in SEED_CAMERAS:
    (
        _camera["main_stream_url"],
        _camera["sub_stream_url"],
    ) = camera_stream_urls_for(_camera["rtsp_url"])
RETIRED_SEED_CAMERA_IDS = [
    "CAM03", "CAM04",
    *(f"D{camera_number}" for camera_number in range(1, 21)),
]

# ---------------------------------------------------------------------------
# 2. MODEL SETTINGS
# ---------------------------------------------------------------------------
MODEL_PATH: str = _env("PPE_MODEL_PATH", "models/best.pt")
PHONE_MODEL_PATH: str = _env("PPE_PHONE_MODEL_PATH", "models/yolo26m.pt")
DEVICE: str = _env("PPE_DEVICE", "auto")
CPU_INFERENCE_THREADS: int = int(_env("PPE_CPU_THREADS", "4"))
FACE_ENABLED: bool = _env_bool("PPE_FACE_ENABLED", False)
FACE_VIOLATION_TYPES: frozenset[str] = _face_violation_types()
FACE_QUALITY_MIN_BY_VIOLATION: dict[str, float] = (
    _face_quality_min_by_violation()
)
FACE_DATASET_PATH: str = _env("PPE_FACE_DATASET_PATH", "dataset")
FACE_MODEL_NAME: str = _env(
    "PPE_FACE_MODEL_NAME", "insightface_buffalo_l",
)
FACE_MODEL_DIR: str = _env("PPE_FACE_MODEL_DIR", "models/face")
FACE_PROVIDER: str = _env("PPE_FACE_PROVIDER", "auto").lower()
FACE_DETECTOR_MODEL_PATH: str = _env(
    "PPE_FACE_DETECTOR_MODEL_PATH", "models/face/yolov8n-face.pt",
)
FACE_DETECTOR_IMAGE_SIZE: int = int(
    _env("PPE_FACE_DETECTOR_IMAGE_SIZE", "1280"),
)
FACE_DETECTOR_CONFIDENCE: float = float(
    _env("PPE_FACE_DETECTOR_CONFIDENCE", "0.15"),
)
FACE_DETECTOR_CROP_PADDING: float = float(
    _env("PPE_FACE_DETECTOR_CROP_PADDING", "0.20"),
)
FACE_YUNET_MODEL_FILENAME: str = "face_detection_yunet_2023mar.onnx"
FACE_SFACE_MODEL_FILENAME: str = "face_recognition_sface_2021dec.onnx"
FACE_LICENSE_ACCEPTED: bool = _env_bool(
    "PPE_FACE_LICENSE_ACCEPTED", False,
)
FACE_REPORT_DIR: str = _env("PPE_FACE_REPORT_DIR", "dataset/faces/reports")
FACE_KEEP_PHOTOS: bool = _env_bool("PPE_FACE_KEEP_PHOTOS", True)
FACE_PHOTO_DIR: str = _env("PPE_FACE_PHOTO_DIR", "dataset/faces/enrolled_photos")
FACE_ENCRYPTION_KEY: str = _env("PPE_FACE_ENCRYPTION_KEY")
FACE_MIN_SIZE: int = int(_env("PPE_FACE_MIN_SIZE", "80"))
FACE_MIN_SHARPNESS: float = float(_env("PPE_FACE_MIN_SHARPNESS", "40"))
FACE_MIN_DETECTION_SCORE: float = float(_env("PPE_FACE_MIN_DETECTION_SCORE", "0.6"))
FACE_MIN_BRIGHTNESS: float = float(_env("PPE_FACE_MIN_BRIGHTNESS", "25"))
FACE_MAX_BRIGHTNESS: float = float(_env("PPE_FACE_MAX_BRIGHTNESS", "235"))
FACE_MAX_YAW: float = float(_env("PPE_FACE_MAX_YAW", "35"))
FACE_MAX_PITCH: float = float(_env("PPE_FACE_MAX_PITCH", "30"))
FACE_OUTLIER_THRESHOLD: float = float(_env("PPE_FACE_OUTLIER_THRESHOLD", "0.35"))
FACE_CROSS_ID_DUPLICATE_THRESHOLD: float = float(
    _env("PPE_FACE_CROSS_ID_DUPLICATE_THRESHOLD", "0.85")
)
FACE_MATCH_THRESHOLD: float = float(_env("PPE_FACE_MATCH_THRESHOLD", "0.60"))
FACE_SUGGEST_THRESHOLD: float = float(_env("PPE_FACE_SUGGEST_THRESHOLD", "0.45"))
FACE_MIN_MARGIN: float = float(_env("PPE_FACE_MIN_MARGIN", "0.10"))
FACE_MATCH_STRATEGY: str = _env("PPE_FACE_MATCH_STRATEGY", "max").lower()
FACE_MATCH_TOP_K: int = int(_env("PPE_FACE_MATCH_TOP_K", "5"))
# Track-based identification: how many best-quality faces per track are
# collected and aggregated into a single embedding before matching.
FACE_IDENTIFY_BEST_N: int = int(_env("PPE_FACE_IDENTIFY_BEST_N", "5"))
# How many usable faces a track must show before the identity is decided
# (1 keeps the legacy single-frame behaviour; 2+ waits for better frames,
# e.g. a phone user looking up).
FACE_IDENTIFY_MIN_FACES: int = int(_env("PPE_FACE_IDENTIFY_MIN_FACES", "1"))
# Aggregation of the best-N face embeddings: mean | weighted_mean | first.
FACE_IDENTIFY_AGGREGATION: str = _env(
    "PPE_FACE_IDENTIFY_AGGREGATION", "mean",
).lower()
FACE_BUFFER_FRAMES: int = int(_env("PPE_FACE_BUFFER_FRAMES", "4"))
FACE_BUFFER_TTL_SECONDS: float = float(_env("PPE_FACE_BUFFER_TTL_SECONDS", "10"))
FACE_JOB_POLL_SECONDS: float = float(_env("PPE_FACE_JOB_POLL_SECONDS", "1"))
FACE_JOB_QUEUE_MAX: int = int(_env("PPE_FACE_JOB_QUEUE_MAX", "500"))
FACE_MATCH_RETENTION_DAYS: int = int(_env("PPE_FACE_MATCH_RETENTION_DAYS", "365"))
FACE_INCLUDE_IN_EMAIL: bool = _env_bool("PPE_FACE_INCLUDE_IN_EMAIL", False)
if (FACE_MIN_SIZE < 1 or FACE_MIN_SHARPNESS < 0
        or not 0 <= FACE_MIN_DETECTION_SCORE <= 1
        or FACE_DETECTOR_IMAGE_SIZE < 320
        or not isfinite(FACE_DETECTOR_CONFIDENCE)
        or not 0 < FACE_DETECTOR_CONFIDENCE <= 1
        or not isfinite(FACE_DETECTOR_CROP_PADDING)
        or not 0 <= FACE_DETECTOR_CROP_PADDING <= 1
        or not 0 <= FACE_MIN_BRIGHTNESS < FACE_MAX_BRIGHTNESS <= 255
        or FACE_PROVIDER not in {"auto", "cpu", "cuda"}
        or FACE_MAX_YAW <= 0 or FACE_MAX_PITCH <= 0
        or not -1 <= FACE_OUTLIER_THRESHOLD <= 1
        or not -1 <= FACE_CROSS_ID_DUPLICATE_THRESHOLD <= 1
        or not isfinite(FACE_MATCH_THRESHOLD)
        or not isfinite(FACE_SUGGEST_THRESHOLD)
        or not -1 <= FACE_MATCH_THRESHOLD <= 1
        or not -1 <= FACE_SUGGEST_THRESHOLD <= FACE_MATCH_THRESHOLD
        or not 0 <= FACE_MIN_MARGIN <= 2
        or FACE_MATCH_STRATEGY not in {"max", "top_k_mean"}
        or FACE_MATCH_TOP_K < 1
        or FACE_IDENTIFY_BEST_N < 1
        or FACE_IDENTIFY_MIN_FACES < 1
        or FACE_IDENTIFY_MIN_FACES > FACE_IDENTIFY_BEST_N
        or FACE_IDENTIFY_AGGREGATION not in {"mean", "weighted_mean", "first"}
        or not 1 <= FACE_BUFFER_FRAMES <= 8
        or any(not isfinite(value) for value in FACE_QUALITY_MIN_BY_VIOLATION.values())
        or not isfinite(FACE_BUFFER_TTL_SECONDS)
        or not 0 < FACE_BUFFER_TTL_SECONDS <= 30
        or not isfinite(FACE_JOB_POLL_SECONDS)
        or not 0 < FACE_JOB_POLL_SECONDS <= 60
        or not 1 <= FACE_JOB_QUEUE_MAX <= 10000
        or FACE_MATCH_RETENTION_DAYS < 0):
    raise ValueError("Invalid optional face-recognition settings")
CONFIDENCE_THRESHOLD: float = float(_env("PPE_CONF_THRESHOLD", "0.45"))
IOU_THRESHOLD: float = float(_env("PPE_IOU_THRESHOLD", "0.45"))
INFERENCE_IMG_SIZE: int = int(_env("PPE_IMG_SIZE", "960"))
PHONE_CONFIDENCE_THRESHOLD: float = float(_env("PPE_PHONE_CONFIDENCE", "0.15"))
FRAME_SKIP: int = int(_env("PPE_FRAME_SKIP", "2"))
DETECT_FPS: float = float(_env("PPE_DETECT_FPS", "5"))
CLASS_CONFIDENCE_THRESHOLDS: dict[str, float] = {
    "no_helmet": 0.25,
    "phone": PHONE_CONFIDENCE_THRESHOLD,
    **{
        str(name): float(value)
        for name, value in _json_env("PPE_CLASS_CONFIDENCE").items()
    },
}
if any(not 0 <= value <= 1 for value in CLASS_CONFIDENCE_THRESHOLDS.values()):
    raise ValueError("PPE_CLASS_CONFIDENCE values must be between 0 and 1")
if not 0 <= PHONE_CONFIDENCE_THRESHOLD <= 1:
    raise ValueError("PPE_PHONE_CONFIDENCE must be between 0 and 1")
if CPU_INFERENCE_THREADS < 1:
    raise ValueError("PPE_CPU_THREADS must be at least 1")
CLASS_BOX_FILTERS: dict[str, dict[str, float]] = {}
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
PPE_ASSOCIATION_REGIONS: dict[str, list[float]] = {
    "helmet": [0.0, 0.4],
    "mask": [0.0, 0.4],
    "goggles": [0.0, 0.45],
    "arc_flash_suit": [0.0, 1.0],
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
CLASS_MAP: dict[str, str] = {
    "person": "person", "Person": "person",
    "helmet": "helmet", "Helmet": "helmet", "Hardhat": "helmet", "hardhat": "helmet",
    "no-helmet": "no_helmet", "No-Helmet": "no_helmet", "NO-Hardhat": "no_helmet", "no_hardhat": "no_helmet",
    "vest": "vest", "Vest": "vest", "Safety Vest": "vest", "safety_vest": "vest",
    "no-vest": "no_vest", "No-Vest": "no_vest", "NO-Safety Vest": "no_vest",
    "gloves": "gloves", "Gloves": "gloves", "no-gloves": "no_gloves",
    "boots": "boots", "Safety Boots": "boots", "safety_shoe": "boots", "no-boots": "no_boots",
    "goggles": "goggles", "Goggles": "goggles", "no-goggle": "no_goggles",
    "mask": "mask", "Mask": "mask", "no-mask": "no_mask", "NO-Mask": "no_mask",
    "arc_flash_suit": "arc_flash_suit",
    "Arc Flash Suit": "arc_flash_suit",
    "arc-flash-suit": "arc_flash_suit",
    "arc flash suit": "arc_flash_suit",
    "arcflash_suit": "arc_flash_suit",
    "ArcFlashSuit": "arc_flash_suit",
    "arc-flash suit": "arc_flash_suit",
    "NO-Arc Flash Suit": "no_arc_flash_suit",
    "No-Arc Flash Suit": "no_arc_flash_suit",
    "No Arc Flash Suit": "no_arc_flash_suit",
    "no_arc_flash_suit": "no_arc_flash_suit",
    "no-arc-flash-suit": "no_arc_flash_suit",
    "no arc flash suit": "no_arc_flash_suit",
    "no_arcflash_suit": "no_arc_flash_suit",
    "no-arcflash-suit": "no_arc_flash_suit",
    "no arcflash suit": "no_arc_flash_suit",
    "cell phone": "phone", "cell_phone": "phone", "mobile phone": "phone",
    "phone": "phone",
}

POSITIVE_PPE_CLASSES = {
    "helmet", "vest", "gloves", "boots", "goggles", "mask", "arc_flash_suit",
}
NEGATIVE_PPE_CLASSES = {
    "helmet": "no_helmet", "vest": "no_vest", "gloves": "no_gloves",
    "boots": "no_boots", "goggles": "no_goggles", "mask": "no_mask",
    "arc_flash_suit": "no_arc_flash_suit",
}

# ---------------------------------------------------------------------------
# 4. COMPLIANCE LOGIC
# ---------------------------------------------------------------------------
MATCH_OVERLAP_THRESHOLD: float = 0.30
VIOLATION_CONFIRM_FRAMES: int = int(_env("PPE_VIOLATION_CONFIRM_FRAMES", "5"))
TEMPORAL_CONFIRMATION: dict[str, dict[str, int]] = {}
for _name, _confirmation in _json_env("PPE_TEMPORAL_CONFIRMATION").items():
    if not isinstance(_confirmation, dict) or set(_confirmation) - {"required", "window"}:
        raise ValueError(f"PPE_TEMPORAL_CONFIRMATION entry for {_name} needs required/window values")
    _required = int(_confirmation.get("required", VIOLATION_CONFIRM_FRAMES))
    _window = int(_confirmation.get("window", max(VIOLATION_CONFIRM_FRAMES, 8)))
    if _required < 1 or _window < _required:
        raise ValueError(f"Invalid N-of-M confirmation values for {_name}: require 1 <= required <= window")
    TEMPORAL_CONFIRMATION[str(_name)] = {"required": _required, "window": _window}
TEMPORAL_CONFIRMATION.setdefault("phone", {"required": 2, "window": 3})
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
EVIDENCE_CATEGORY_BY_VIOLATION = {
    "helmet": "helmet_violations",
    "no_helmet": "helmet_violations",
    "phone_detected": "phone_detected",
}


def evidence_directory(base_dir: str, violation_type: str) -> str:
    category = EVIDENCE_CATEGORY_BY_VIOLATION.get(violation_type)
    return os.path.join(base_dir, category) if category else base_dir


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
BROADCAST_FPS: int = min(
    10, int(_env("PPE_TILE_FPS", _env("PPE_BROADCAST_FPS", "8")))
)
LIVE_TILE_WIDTH: int = int(_env("PPE_TILE_WIDTH", "640"))
FULLSCREEN_WIDTH: int = int(_env("PPE_FULLSCREEN_WIDTH", "1920"))
FULLSCREEN_FPS: int = min(15, int(_env("PPE_FULLSCREEN_FPS", "12")))
CAMERA_STATUS_UPDATE_INTERVAL: float = float(_env("PPE_STATUS_UPDATE_INTERVAL", "4"))
CAMERA_RECONNECT_INITIAL_SECONDS: float = float(_env("PPE_CAMERA_RECONNECT_INITIAL_SECONDS", "2"))
CAMERA_RECONNECT_MAX_SECONDS: float = float(_env("PPE_CAMERA_RECONNECT_MAX_SECONDS", "60"))
CAMERA_WATCHDOG_INTERVAL_SECONDS: float = float(_env("PPE_CAMERA_WATCHDOG_INTERVAL_SECONDS", "2"))
CAMERA_FRAME_TIMEOUT_SECONDS: float = float(_env("PPE_CAMERA_FRAME_TIMEOUT_SECONDS", "10"))
CAMERA_MAX_SIMULTANEOUS_RECONNECTS: int = int(
    _env("PPE_CAMERA_MAX_SIMULTANEOUS_RECONNECTS", "4")
)
if (CAMERA_RECONNECT_INITIAL_SECONDS <= 0
        or CAMERA_RECONNECT_MAX_SECONDS < CAMERA_RECONNECT_INITIAL_SECONDS
        or CAMERA_WATCHDOG_INTERVAL_SECONDS <= 0
        or CAMERA_FRAME_TIMEOUT_SECONDS <= 0
        or CAMERA_MAX_SIMULTANEOUS_RECONNECTS < 1
        or not isfinite(DETECT_FPS) or DETECT_FPS <= 0
        or BROADCAST_FPS < 1
        or LIVE_TILE_WIDTH < 64
        or FULLSCREEN_FPS < 1
        or FULLSCREEN_WIDTH < LIVE_TILE_WIDTH):
    raise ValueError("Invalid camera reconnect/watchdog configuration")

# ---------------------------------------------------------------------------
# 8. OUTPUT RESOLUTION — fixed broadcast resolution for every camera
# ---------------------------------------------------------------------------
OUTPUT_WIDTH: int = 1920
OUTPUT_HEIGHT: int = 1080
