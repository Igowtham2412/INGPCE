"""Validated, non-secret application settings persisted outside source control."""
from __future__ import annotations

import json
import math
import os
import tempfile
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

SETTINGS_FILE = Path(__file__).resolve().parents[1] / ".app_settings.json"


@dataclass(frozen=True)
class Setting:
    key: str
    category: str
    label: str
    description: str
    value_type: str
    minimum: int | float | None = None
    maximum: int | float | None = None
    choices: tuple[str, ...] = ()


def _setting(
    key: str, category: str, label: str, description: str,
    value_type: str, minimum: float | None = None,
    maximum: float | None = None, choices: tuple[str, ...] = (),
) -> Setting:
    return Setting(
        key, category, label, description, value_type,
        minimum, maximum, choices,
    )


SETTINGS = (
    _setting("MODEL_PATH", "Models and inference", "PPE model path", "Local model file used for PPE detection.", "string"),
    _setting("PHONE_MODEL_PATH", "Models and inference", "Phone model path", "Local model file used for phone detection.", "string"),
    _setting("DEVICE", "Models and inference", "Inference device", "Select automatic device detection or a specific accelerator.", "choice", choices=("auto", "cpu", "cuda")),
    _setting("CPU_INFERENCE_THREADS", "Models and inference", "CPU threads", "Number of CPU inference threads.", "integer", 1, 64),
    _setting("INFERENCE_CONCURRENCY", "Models and inference", "Inference concurrency", "Maximum concurrent inference operations.", "integer", 1, 64),
    _setting("CONFIDENCE_THRESHOLD", "Models and inference", "PPE confidence", "Minimum confidence for PPE detections.", "number", 0, 1),
    _setting("IOU_THRESHOLD", "Models and inference", "NMS IoU threshold", "Intersection-over-union threshold for duplicate detection suppression.", "number", 0, 1),
    _setting("INFERENCE_IMG_SIZE", "Models and inference", "Inference image size", "Input image size used by the PPE model.", "integer", 320, 2048),
    _setting("PHONE_CONFIDENCE_THRESHOLD", "Models and inference", "Phone confidence", "Minimum confidence for phone detections.", "number", 0, 1),
    _setting("FRAME_SKIP", "Models and inference", "Frame skip", "Process one frame for every configured number of frames.", "integer", 1, 120),
    _setting("DETECT_FPS", "Models and inference", "Detection FPS", "Maximum detection frequency per camera.", "number", 0.1, 60),
    _setting("PPE_COMPLIANCE_MODE", "Detection policy", "Compliance mode", "Policy used to interpret positive and explicit-negative PPE signals.", "choice", choices=("explicit_negative", "positive_absence")),
    _setting("VIOLATION_CONFIRM_FRAMES", "Detection policy", "Confirmation frames", "Number of observations required before creating an incident.", "integer", 1, 120),
    _setting("VIOLATION_COOLDOWN_SECONDS", "Detection policy", "Violation cooldown", "Minimum seconds between repeated incidents for the same signal.", "number", 0, 86400),
    _setting("TRACK_HIGH_THRESHOLD", "Detection policy", "Track high threshold", "High-confidence threshold for person tracking.", "number", 0, 1),
    _setting("TRACK_LOW_THRESHOLD", "Detection policy", "Track low threshold", "Low-confidence threshold for person tracking.", "number", 0, 1),
    _setting("TRACK_NEW_THRESHOLD", "Detection policy", "New track threshold", "Confidence threshold for starting a new track.", "number", 0, 1),
    _setting("TRACK_BUFFER", "Detection policy", "Track buffer", "Number of frames to retain a lost track.", "integer", 1, 1000),
    _setting("TRACK_MATCH_THRESHOLD", "Detection policy", "Track match threshold", "Similarity threshold for matching tracked objects.", "number", 0, 1),
    _setting("ALERTS_ENABLED", "Alert delivery", "Enable alerts", "Enable alert rule evaluation and delivery.", "boolean"),
    _setting("ALERT_OUTBOX_POLL_SECONDS", "Alert delivery", "Outbox poll interval", "Seconds between durable alert queue checks.", "number", 0.1, 60),
    _setting("ALERT_OUTBOX_MAX_ATTEMPTS", "Alert delivery", "Maximum delivery attempts", "Retry limit before an alert is marked dead.", "integer", 1, 100),
    _setting("SHIFT_REPORT_POLL_SECONDS", "Alert delivery", "Shift report poll interval", "Seconds between scheduled shift report checks.", "number", 1, 3600),
    _setting("SHIFT_REPORT_CATCHUP_HOURS", "Alert delivery", "Shift report catch-up", "Maximum hours of missed shift reports to process after downtime.", "integer", 1, 8760),
    _setting("SHIFT_REPORT_TIMEZONE", "Alert delivery", "Shift report timezone", "IANA timezone for scheduled shift reports, for example Asia/Kolkata.", "string"),
    _setting("FACE_INCLUDE_IN_EMAIL", "Alert delivery", "Include face-match summary", "Include face-match summary fields in eligible alert messages.", "boolean"),
    _setting("FACE_ENABLED", "Face matching", "Enable face matching", "Enable the optional face-matching worker. Requires deployment-side license and encryption configuration.", "boolean"),
    _setting("FACE_PROVIDER", "Face matching", "Face provider", "Execution provider for the configured face detector.", "choice", choices=("auto", "cpu", "cuda")),
    _setting("FACE_DETECTOR_IMAGE_SIZE", "Face matching", "Face detector image size", "Input image size for face detection.", "integer", 320, 4096),
    _setting("FACE_DETECTOR_CONFIDENCE", "Face matching", "Face detector confidence", "Minimum confidence for face detections.", "number", 0.01, 1),
    _setting("FACE_DETECTOR_CROP_PADDING", "Face matching", "Face crop padding", "Normalized padding around detected face crops.", "number", 0, 1),
    _setting("FACE_MIN_SIZE", "Face matching", "Minimum face size", "Minimum accepted face crop size in pixels.", "integer", 1, 2048),
    _setting("FACE_MIN_SHARPNESS", "Face matching", "Minimum sharpness", "Minimum image sharpness score for a face sample.", "number", 0, 10000),
    _setting("FACE_MIN_DETECTION_SCORE", "Face matching", "Minimum face score", "Minimum detector score for a usable face crop.", "number", 0, 1),
    _setting("FACE_MATCH_THRESHOLD", "Face matching", "Match threshold", "Minimum score for a confirmed candidate suggestion.", "number", -1, 1),
    _setting("FACE_SUGGEST_THRESHOLD", "Face matching", "Suggestion threshold", "Minimum score to show a candidate suggestion.", "number", -1, 1),
    _setting("FACE_MIN_MARGIN", "Face matching", "Minimum candidate margin", "Minimum score difference between the top candidates.", "number", 0, 2),
    _setting("FACE_MATCH_STRATEGY", "Face matching", "Match strategy", "Strategy for aggregating candidate similarity scores.", "choice", choices=("max", "top_k_mean")),
    _setting("FACE_MATCH_TOP_K", "Face matching", "Top candidates", "Number of candidates used by the top-K strategy.", "integer", 1, 100),
    _setting("FACE_IDENTIFY_BEST_N", "Face matching", "Best faces per track", "Maximum quality face samples aggregated per person track.", "integer", 1, 100),
    _setting("FACE_IDENTIFY_MIN_FACES", "Face matching", "Minimum faces per track", "Minimum usable face samples required before matching.", "integer", 1, 100),
    _setting("FACE_IDENTIFY_AGGREGATION", "Face matching", "Embedding aggregation", "Method for combining face embeddings.", "choice", choices=("mean", "weighted_mean", "first")),
    _setting("FACE_BUFFER_FRAMES", "Face matching", "Buffered frames", "Number of frames retained for asynchronous face processing.", "integer", 1, 8),
    _setting("FACE_BUFFER_TTL_SECONDS", "Face matching", "Frame buffer lifetime", "Seconds buffered face frames are retained.", "number", 0.1, 30),
    _setting("FACE_JOB_POLL_SECONDS", "Face matching", "Face job poll interval", "Seconds between checks for face-match jobs.", "number", 0.1, 60),
    _setting("FACE_JOB_QUEUE_MAX", "Face matching", "Maximum queued face jobs", "Maximum pending face-match jobs.", "integer", 1, 10000),
    _setting("FACE_MATCH_RETENTION_DAYS", "Face matching", "Face-match retention days", "Retention period for face-match records; zero disables age-based deletion.", "integer", 0, 36500),
    _setting("PG_HOST", "Database pool", "Database host", "PostgreSQL server hostname.", "string"),
    _setting("PG_PORT", "Database pool", "Database port", "PostgreSQL server port.", "integer", 1, 65535),
    _setting("PG_DB", "Database pool", "Database name", "PostgreSQL database name.", "string"),
    _setting("PG_USER", "Database pool", "Database user", "PostgreSQL username. Passwords remain in .env.", "string"),
    _setting("PG_POOL_MIN", "Database pool", "Minimum connections", "Minimum PostgreSQL pool size.", "integer", 1, 100),
    _setting("PG_POOL_MAX", "Database pool", "Maximum connections", "Maximum PostgreSQL pool size.", "integer", 1, 500),
    _setting("PG_CONNECT_RETRY_SECONDS", "Database pool", "Connection retry interval", "Seconds between PostgreSQL startup retries.", "integer", 1, 3600),
    _setting("SNAPSHOT_DIR", "Storage and retention", "Snapshot directory", "Directory used for incident snapshots.", "string"),
    _setting("FEEDBACK_DATASET_DIR", "Storage and retention", "Feedback dataset directory", "Directory used for reviewed feedback data.", "string"),
    _setting("EVIDENCE_CLIP_DIR", "Storage and retention", "Evidence clip directory", "Directory used for incident evidence clips.", "string"),
    _setting("EVIDENCE_CLIP_PRE_SECONDS", "Storage and retention", "Clip seconds before event", "Video duration retained before a confirmed event.", "number", 0, 60),
    _setting("EVIDENCE_CLIP_POST_SECONDS", "Storage and retention", "Clip seconds after event", "Video duration retained after a confirmed event.", "number", 0, 60),
    _setting("EVIDENCE_CLIP_FPS", "Storage and retention", "Evidence clip FPS", "Frames per second written to evidence clips.", "number", 0.1, 30),
    _setting("EVIDENCE_CLIP_WIDTH", "Storage and retention", "Evidence clip width", "Output width for saved evidence clips.", "integer", 64, 1920),
    _setting("CAMERA_STATUS_HISTORY_DAYS", "Storage and retention", "Camera history retention", "Days of camera-status history to retain (minimum 30).", "integer", 30, 36500),
    _setting("JPEG_QUALITY", "Camera streaming", "JPEG quality", "JPEG compression quality for live frames.", "integer", 20, 100),
    _setting("BROADCAST_FPS", "Camera streaming", "Live wall FPS", "Maximum live-wall frame broadcast frequency.", "integer", 1, 10),
    _setting("LIVE_TILE_WIDTH", "Camera streaming", "Live tile width", "Maximum width of camera wall tiles.", "integer", 64, 1920),
    _setting("FULLSCREEN_WIDTH", "Camera streaming", "Fullscreen width", "Maximum width of fullscreen camera frames.", "integer", 64, 4096),
    _setting("FULLSCREEN_FPS", "Camera streaming", "Fullscreen FPS", "Maximum fullscreen frame broadcast frequency.", "integer", 1, 15),
    _setting("CAMERA_STATUS_UPDATE_INTERVAL", "Camera streaming", "Status update interval", "Seconds between camera-status updates.", "number", 0.1, 120),
    _setting("CAMERA_RECONNECT_INITIAL_SECONDS", "Camera streaming", "Initial reconnect delay", "Initial wait before reconnecting a camera.", "number", 0.1, 300),
    _setting("CAMERA_RECONNECT_MAX_SECONDS", "Camera streaming", "Maximum reconnect delay", "Maximum camera reconnect backoff.", "number", 0.1, 3600),
    _setting("CAMERA_WATCHDOG_INTERVAL_SECONDS", "Camera streaming", "Watchdog interval", "Seconds between camera worker watchdog checks.", "number", 0.1, 120),
    _setting("CAMERA_FRAME_TIMEOUT_SECONDS", "Camera streaming", "Frame timeout", "Seconds before a stream is considered stale.", "number", 0.1, 600),
    _setting("CAMERA_MAX_SIMULTANEOUS_RECONNECTS", "Camera streaming", "Concurrent reconnect limit", "Maximum cameras reconnected at the same time.", "integer", 1, 100),
)

SETTINGS_BY_KEY = {setting.key: setting for setting in SETTINGS}
_SETTINGS_BY_CATEGORY: dict[str, list[Setting]] = {}
for _item in SETTINGS:
    _SETTINGS_BY_CATEGORY.setdefault(_item.category, []).append(_item)


def _validate_type_and_range(setting: Setting, value: Any) -> Any:
    if setting.value_type == "boolean":
        if type(value) is not bool:
            raise ValueError(f"{setting.label} must be a boolean")
        return value
    if setting.value_type == "integer":
        if type(value) is not int:
            raise ValueError(f"{setting.label} must be an integer")
        normalized: int | float = value
    elif setting.value_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{setting.label} must be a number")
        normalized = float(value)
        if not math.isfinite(normalized):
            raise ValueError(f"{setting.label} must be finite")
    elif setting.value_type == "choice":
        if not isinstance(value, str) or value not in setting.choices:
            raise ValueError(f"{setting.label} has an unsupported value")
        return value
    elif setting.value_type == "string":
        if not isinstance(value, str) or len(value) > 500:
            raise ValueError(f"{setting.label} must be a string of at most 500 characters")
        if "\x00" in value or any(ord(character) < 32 for character in value):
            raise ValueError(f"{setting.label} contains unsupported characters")
        if value != value.strip():
            raise ValueError(f"{setting.label} must not start or end with whitespace")
        if not value and setting.key != "SHIFT_REPORT_TIMEZONE":
            raise ValueError(f"{setting.label} must not be empty")
        return value
    else:
        raise ValueError(f"Unsupported setting type for {setting.key}")

    if setting.minimum is not None and normalized < setting.minimum:
        raise ValueError(f"{setting.label} must be at least {setting.minimum}")
    if setting.maximum is not None and normalized > setting.maximum:
        raise ValueError(f"{setting.label} must be at most {setting.maximum}")
    return normalized if setting.value_type == "number" else int(normalized)


def validate_values(
    values: Mapping[str, Any], current: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(values, Mapping) or not values:
        raise ValueError("Provide at least one setting to update")
    unknown = set(values) - SETTINGS_BY_KEY.keys()
    if unknown:
        raise ValueError(f"Unsupported setting key: {min(unknown)}")

    validated = {
        key: _validate_type_and_range(SETTINGS_BY_KEY[key], value)
        for key, value in values.items()
    }
    effective = {
        setting.key: validated.get(setting.key, current[setting.key])
        for setting in SETTINGS
    }
    if effective["TRACK_LOW_THRESHOLD"] > effective["TRACK_HIGH_THRESHOLD"]:
        raise ValueError("Track low threshold must not exceed the high threshold")
    if effective["FACE_SUGGEST_THRESHOLD"] > effective["FACE_MATCH_THRESHOLD"]:
        raise ValueError("Face suggestion threshold must not exceed the match threshold")
    if effective["FACE_IDENTIFY_MIN_FACES"] > effective["FACE_IDENTIFY_BEST_N"]:
        raise ValueError("Minimum faces per track must not exceed the best-N limit")
    if effective["PG_POOL_MIN"] > effective["PG_POOL_MAX"]:
        raise ValueError("Minimum database connections must not exceed the maximum")
    if effective["CAMERA_RECONNECT_INITIAL_SECONDS"] > effective["CAMERA_RECONNECT_MAX_SECONDS"]:
        raise ValueError("Initial camera reconnect delay must not exceed the maximum")
    if effective["FULLSCREEN_WIDTH"] < effective["LIVE_TILE_WIDTH"]:
        raise ValueError("Fullscreen width must not be smaller than live tile width")
    if effective["FACE_ENABLED"] and not (
        current.get("FACE_LICENSE_ACCEPTED") and current.get("FACE_ENCRYPTION_KEY")
    ):
        raise ValueError(
            "Face matching requires license acceptance and an encryption key configured in .env"
        )
    return validated


def _config_values(namespace: Mapping[str, Any]) -> dict[str, Any]:
    return {setting.key: namespace[setting.key] for setting in SETTINGS}


def load_overrides(namespace: dict[str, Any], path: Path = SETTINGS_FILE) -> None:
    if not path.exists():
        return
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Could not read persisted application settings: {error}") from error
    if not isinstance(values, dict):
        raise TypeError("Persisted application settings must be a JSON object")
    try:
        overrides = validate_values(values, namespace)
    except ValueError as error:
        raise RuntimeError(f"Persisted application settings are invalid: {error}") from error
    namespace.update(overrides)


def get_settings(
    namespace: Mapping[str, Any], path: Path = SETTINGS_FILE,
) -> list[dict[str, Any]]:
    effective = _config_values(namespace)
    if path.exists():
        try:
            persisted = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(
                f"Could not read persisted application settings: {error}"
            ) from error
        if not isinstance(persisted, dict):
            raise TypeError("Persisted application settings must be a JSON object")
        effective.update(validate_values(persisted, namespace))

    groups = []
    for category, items in _SETTINGS_BY_CATEGORY.items():
        groups.append({
            "category": category,
            "settings": [
                {
                    **asdict(item),
                    "choices": list(item.choices),
                    "value": effective[item.key],
                }
                for item in items
            ],
        })
    return groups


def save_settings(
    values: Mapping[str, Any], namespace: Mapping[str, Any],
    path: Path = SETTINGS_FILE,
) -> None:
    validated = validate_values(values, namespace)
    existing: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Could not read persisted application settings: {error}") from error
        if not isinstance(loaded, dict):
            raise RuntimeError("Persisted application settings must be a JSON object")
        existing = validate_values(loaded, namespace)
    existing.update(validated)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False,
        ) as temporary:
            temporary_path = temporary.name
            json.dump(existing, temporary, indent=2, sort_keys=True)
            temporary.write("\n")
        os.replace(temporary_path, path)
    finally:
        if temporary_path and os.path.exists(temporary_path):
            os.unlink(temporary_path)


def current_values(namespace: Mapping[str, Any]) -> dict[str, Any]:
    return _config_values(namespace)
