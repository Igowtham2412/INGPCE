"""Consent-gated face dataset import and local face quality checks."""
import asyncio
import csv
import hashlib
import logging
import math
import secrets
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace
from typing import Any, Protocol

import cv2
import numpy as np

from .. import config, db
from .face_manifest import read_face_manifest

logger = logging.getLogger("ppe.face_dataset")
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
_RESERVED_DATASET_DIRS = {
    "reports", "enrolled_photos", "face_test_crops",
    "face_incident_calibration", "feedback", "false_positive",
    "confirmed", "violations", "historian", "evaluation", "eval",
}
_ENCRYPTION_PREFIX = b"AFV1"
_FACE_CROP_PREFIX = b"PPEFCROP1"


class ConsentError(ValueError):
    """The requested import does not have a recorded consent basis."""


class FaceModelUnavailable(RuntimeError):
    """The local face package or its pre-provisioned model is unavailable."""


class ImageRejected(ValueError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class FaceAnalyzer(Protocol):
    model_name: str
    model_version: str

    def analyze(self, image: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
        """Return one quality-approved normalized embedding and measurements."""


@dataclass(frozen=True)
class EmployeeImage:
    employee_code: str
    full_name: str
    department: str | None
    path: Path
    consent_recorded: bool
    consent_basis: str
    consent_date: date | None
    designation: str | None = None


@dataclass
class PersonImport:
    employee_code: str
    full_name: str
    department: str | None
    consent_basis: str
    consent_date: date | None
    designation: str | None = None
    images: list[EmployeeImage] = field(default_factory=list)


@dataclass
class PersonResult:
    employee_code: str
    full_name: str
    department: str | None
    accepted: int = 0
    rejected: int = 0
    duplicates: int = 0
    preexisting: int = 0
    rejection_reasons: dict[str, int] = field(default_factory=dict)
    embeddings: list[np.ndarray] = field(default_factory=list)
    image_hashes: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class FaceEmbedding:
    embedding: np.ndarray
    quality_score: float
    measurements: dict[str, Any]


def _bulk_consent(
        name: str | None, consent_date: str | None,
        reference: str | None) -> tuple[str, date] | None:
    supplied = [name, consent_date, reference]
    if not any(supplied):
        return None
    if not all(supplied):
        raise ConsentError(
            "Bulk consent requires the confirming name, consent date, and reference"
        )
    try:
        parsed_date = date.fromisoformat(consent_date.strip())
    except ValueError as exc:
        raise ConsentError("Bulk consent date must use YYYY-MM-DD") from exc
    basis = f"Bulk consent confirmed by {name.strip()}; reference {reference.strip()}"
    if not name.strip() or not reference.strip():
        raise ConsentError("Bulk consent requires a name and reference")
    return basis, parsed_date


def _parse_consent_date(value: str | None) -> date | None:
    if not value or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ConsentError("CSV consent_date values must use YYYY-MM-DD") from exc


def _inside_root(root: Path, candidate: Path) -> Path:
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise ValueError(
            "Manifest image_path must refer to a file inside the dataset root"
        ) from exc
    if not resolved.is_file() or resolved.suffix.lower() not in _IMAGE_SUFFIXES:
        raise ValueError("CSV image_path must refer to a JPG, JPEG, or PNG image")
    return resolved


def _resolve_dataset_image(root: Path, image_reference: str) -> Path:
    relative = Path(image_reference)
    root = root.resolve(strict=True)
    if (
        ".." in relative.parts
        or ".." in PureWindowsPath(image_reference).parts
    ):
        raise ValueError(
            "Manifest image_path must refer to a file inside the dataset root"
        )
    if (
        PureWindowsPath(image_reference).is_absolute()
        and not relative.is_absolute()
    ):
        raise ValueError(
            "Manifest image_path must use a path valid on this operating system"
        )
    direct_candidate = (
        relative
        if relative.is_absolute() or PureWindowsPath(image_reference).is_absolute()
        else root / relative
    )
    direct_options = [direct_candidate]
    if not relative.suffix:
        direct_options.extend(
            direct_candidate.with_suffix(suffix)
            for suffix in sorted(_IMAGE_SUFFIXES)
        )
    for candidate in direct_options:
        if candidate.is_file():
            return _inside_root(root, candidate)

    reference_name = relative.name.casefold()
    reference_stem = relative.stem.casefold()
    candidates = []
    for candidate in root.rglob("*"):
        if not candidate.is_file() or candidate.suffix.lower() not in _IMAGE_SUFFIXES:
            continue
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        if any(
            component.casefold() in _RESERVED_DATASET_DIRS
            for component in candidate.relative_to(root).parts[:-1]
        ):
            continue
        matches_reference = (
            candidate.name.casefold() == reference_name
            if relative.suffix
            else candidate.stem.casefold() == reference_stem
        )
        if matches_reference:
            candidates.append(candidate)

    if len(candidates) == 1:
        return _inside_root(root, candidates[0])
    if len(candidates) > 1:
        raise ValueError(
            "Manifest image_path matches multiple employee photos; specify "
            "a unique relative path"
        )
    raise ValueError(
        "Manifest image_path does not match an image inside the dataset root"
    )


def _add_person_image(
        people: dict[str, PersonImport], image: EmployeeImage) -> None:
    existing = people.get(image.employee_code)
    if existing is None:
        existing = PersonImport(
            image.employee_code, image.full_name, image.department,
            image.consent_basis, image.consent_date, image.designation,
        )
        people[image.employee_code] = existing
    elif (existing.full_name != image.full_name
          or existing.department != image.department
          or existing.designation != image.designation
          or existing.consent_basis != image.consent_basis
          or existing.consent_date != image.consent_date):
        raise ValueError("Rows for an employee must use consistent identity and consent fields")
    existing.images.append(image)


def discover_dataset(
        dataset_path: Path, consent_name: str | None = None,
        consent_date: str | None = None,
        consent_reference: str | None = None) -> dict[str, PersonImport]:
    """Read employee folders or a CSV/Excel manifest."""
    root = dataset_path.resolve()
    bulk = _bulk_consent(consent_name, consent_date, consent_reference)
    if root.is_file() and root.suffix.lower() in {".csv", ".xlsx"}:
        manifest_path = root
        dataset_root = root.parent.resolve()
    elif root.is_dir():
        manifests = sorted(
            path for path in root.iterdir()
            if path.is_file() and path.suffix.lower() in {".csv", ".xlsx"}
        )
        employee_manifests = [
            path for path in manifests
            if path.name.casefold().startswith(("employee", "roster"))
            and not any(
                marker in path.stem.casefold()
                for marker in ("example", "sample", "template")
            )
        ]
        if len(employee_manifests) == 1:
            manifest_path = employee_manifests[0]
            dataset_root = root
        elif len(employee_manifests) > 1:
            raise ValueError(
                "Dataset root must contain one employee roster manifest"
            )
        elif len(manifests) > 1:
            raise ValueError("Dataset root must contain at most one CSV or Excel manifest")
        elif manifests:
            manifest_path = manifests[0]
            dataset_root = root
        else:
            return _discover_folders(root, bulk)
    else:
        raise ValueError("Dataset path must be a directory or CSV/Excel manifest")

    return _discover_manifest(manifest_path, dataset_root, bulk)


def _discover_folders(
        root: Path, bulk_consent: tuple[str, date] | None) -> dict[str, PersonImport]:
    candidates = [
        folder for folder in root.iterdir()
        if (
            folder.is_dir()
            and folder.name.casefold() not in _RESERVED_DATASET_DIRS
            and not folder.name.startswith(".")
            and "_" in folder.name
        )
    ]
    if not candidates:
        return {}
    if bulk_consent is None:
        raise ConsentError(
            "Folder imports require bulk consent name, date, and reference"
        )
    consent_basis, consent_day = bulk_consent
    people: dict[str, PersonImport] = {}
    for folder in sorted(candidates):
        employee_code, full_name = folder.name.split("_", 1)
        employee_code, full_name = employee_code.strip(), full_name.replace("_", " ").strip()
        if not employee_code or not full_name:
            raise ValueError("Folder names must use <employee_code>_<full_name>")
        person = PersonImport(employee_code, full_name, None, consent_basis, consent_day)
        person.images.extend(
            EmployeeImage(
                employee_code, full_name, None, path, True,
                consent_basis, consent_day,
            )
            for path in sorted(folder.iterdir())
            if path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES
        )
        if employee_code in people:
            raise ValueError("Employee codes must be unique across dataset folders")
        people[employee_code] = person
    return people


def _discover_manifest(
        manifest_path: Path, root: Path,
        bulk_consent: tuple[str, date] | None) -> dict[str, PersonImport]:
    people: dict[str, PersonImport] = {}
    headers, rows = read_face_manifest(manifest_path)
    required = {"employee_code", "full_name", "department", "image_path"}
    if not required.issubset(headers):
        raise ValueError(
            "Manifest must contain employee_code, full_name, department, image_path"
        )
    has_consent_column = "consent_recorded" in headers
    if not has_consent_column and bulk_consent is None:
        raise ConsentError(
            "Manifest needs consent_recorded=true for every employee or "
            "a bulk consent basis"
        )
    for row in rows:
        employee_code = row.get("employee_code", "").strip()
        full_name = row.get("full_name", "").strip()
        department = row.get("department", "").strip() or None
        designation = row.get("designation", "").strip() or None
        image_path = row.get("image_path", "").strip()
        if not employee_code or not full_name or not image_path:
            raise ValueError(
                "Manifest employee_code, full_name, and image_path cannot be empty"
            )

        row_consent = row.get("consent_recorded", "").strip().lower()
        if has_consent_column and row_consent not in {"1", "true", "yes"}:
            raise ConsentError(
                "Manifest consent_recorded must be true for every imported employee"
            )
        row_basis = row.get("consent_basis", "").strip()
        row_date = _parse_consent_date(row.get("consent_date"))
        if bulk_consent:
            consent_basis, consent_day = bulk_consent
        else:
            consent_basis = (
                row_basis
                or row.get("consent_reference", "").strip()
                or "Manifest consent_recorded=true"
            )
            consent_day = row_date
        resolved_path = _resolve_dataset_image(root, image_path)
        _add_person_image(people, EmployeeImage(
            employee_code, full_name, department, resolved_path, True,
            consent_basis, consent_day, designation,
        ))
    return people


def _encryption_key() -> bytes:
    configured = config.FACE_ENCRYPTION_KEY.strip()
    if not configured:
        raise RuntimeError("PPE_FACE_ENCRYPTION_KEY is required to import embeddings")
    import base64
    import binascii

    try:
        key = base64.b64decode(
            configured.encode("ascii"), altchars=b"-_", validate=True,
        )
    except (binascii.Error, UnicodeEncodeError) as exc:
        raise RuntimeError("PPE_FACE_ENCRYPTION_KEY must be URL-safe base64") from exc
    if len(key) != 32:
        raise RuntimeError("PPE_FACE_ENCRYPTION_KEY must decode to exactly 32 bytes")
    return key


def encrypt_embedding(embedding: np.ndarray, key: bytes) -> bytes:
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:
        raise FaceModelUnavailable(
            "Install the pinned cryptography dependency to encrypt face embeddings"
        ) from exc
    nonce = secrets.token_bytes(12)
    encrypted = AESGCM(key).encrypt(
        nonce, np.asarray(embedding, dtype=np.float32).tobytes(), None,
    )
    return _ENCRYPTION_PREFIX + nonce + encrypted


def decrypt_embedding(encrypted: bytes, key: bytes) -> np.ndarray:
    if not encrypted.startswith(_ENCRYPTION_PREFIX):
        raise ValueError("Unsupported encrypted face embedding format")
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce_start = len(_ENCRYPTION_PREFIX)
    nonce = encrypted[nonce_start:nonce_start + 12]
    plaintext = AESGCM(key).decrypt(nonce, encrypted[nonce_start + 12:], None)
    embedding = np.frombuffer(plaintext, dtype=np.float32)
    if embedding.size == 0 or not np.isfinite(embedding).all():
        raise ValueError("Stored face embedding is invalid")
    return embedding.copy()


def encrypt_face_crop(image: bytes, key: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = secrets.token_bytes(12)
    encrypted = AESGCM(key).encrypt(
        nonce, image, b"incident-face-crop-v1",
    )
    return _FACE_CROP_PREFIX + nonce + encrypted


def decrypt_face_crop(encrypted: bytes, key: bytes) -> bytes:
    if not encrypted.startswith(_FACE_CROP_PREFIX):
        raise ValueError("Unsupported encrypted face crop format")
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce_start = len(_FACE_CROP_PREFIX)
    nonce = encrypted[nonce_start:nonce_start + 12]
    try:
        return AESGCM(key).decrypt(
            nonce, encrypted[nonce_start + 12:], b"incident-face-crop-v1",
        )
    except InvalidTag as exc:
        raise ValueError("Stored face crop could not be authenticated") from exc


def read_face_image(path: Path) -> bytes:
    image_bytes = path.read_bytes()
    if image_bytes.startswith(_FACE_CROP_PREFIX):
        return decrypt_face_crop(image_bytes, _encryption_key())
    return image_bytes


def _pose_degrees(face: Any) -> tuple[float, float]:
    pose = getattr(face, "pose", None)
    if pose is not None:
        pose_values = np.asarray(pose, dtype=np.float32).reshape(-1)
        if pose_values.size >= 2 and np.isfinite(pose_values[:2]).all():
            return float(pose_values[0]), float(pose_values[1])

    landmarks = np.asarray(getattr(face, "kps", []), dtype=np.float32)
    if landmarks.shape != (5, 2) or not np.isfinite(landmarks).all():
        raise ImageRejected("pose_unavailable")
    eye_center = landmarks[:2].mean(axis=0)
    eye_distance = float(np.linalg.norm(landmarks[0] - landmarks[1]))
    if eye_distance < 1:
        raise ImageRejected("pose_unavailable")
    mouth_center = landmarks[3:5].mean(axis=0)
    nose = landmarks[2]
    yaw = math.degrees(math.atan2(
        float(nose[0] - eye_center[0]), eye_distance * 0.5,
    ))
    expected_nose_y = eye_center[1] + 0.55 * (mouth_center[1] - eye_center[1])
    pitch = math.degrees(math.atan2(
        float(nose[1] - expected_nose_y), eye_distance * 0.5,
    ))
    return yaw, pitch


def _quality_approved_face(
        face: Any, image: np.ndarray) -> FaceEmbedding:
    height, width = image.shape[:2]
    box = np.asarray(face.bbox, dtype=np.float32).reshape(-1)
    if box.size != 4 or not np.isfinite(box).all():
        raise ImageRejected("invalid_face_box")
    x1, y1, x2, y2 = np.clip(
        np.rint(box).astype(int), [0, 0, 0, 0],
        [width, height, width, height],
    )
    face_width, face_height = x2 - x1, y2 - y1
    if min(face_width, face_height) < config.FACE_MIN_SIZE:
        raise ImageRejected("face_too_small")
    if float(getattr(face, "det_score", 0.0)) < config.FACE_MIN_DETECTION_SCORE:
        raise ImageRejected("low_detection_confidence")
    crop = image[y1:y2, x1:x2]
    if crop.size == 0:
        raise ImageRejected("invalid_face_box")
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    brightness = float(np.mean(gray))
    if not config.FACE_MIN_BRIGHTNESS <= brightness <= config.FACE_MAX_BRIGHTNESS:
        raise ImageRejected("brightness_out_of_range")
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    if sharpness < config.FACE_MIN_SHARPNESS:
        raise ImageRejected("blurred")
    yaw, pitch = _pose_degrees(face)
    if abs(yaw) > config.FACE_MAX_YAW:
        raise ImageRejected("non_frontal_yaw")
    if abs(pitch) > config.FACE_MAX_PITCH:
        raise ImageRejected("non_frontal_pitch")
    embedding = np.asarray(face.normed_embedding, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(embedding))
    if embedding.size == 0 or not np.isfinite(embedding).all() or norm < 1e-8:
        raise ImageRejected("invalid_embedding")
    embedding = embedding / norm
    sharpness_score = min(1.0, sharpness / max(config.FACE_MIN_SHARPNESS * 4, 1))
    size_score = min(1.0, min(face_width, face_height) / (config.FACE_MIN_SIZE * 2))
    pose_score = max(
        0.0,
        1.0 - max(
            abs(yaw) / config.FACE_MAX_YAW,
            abs(pitch) / config.FACE_MAX_PITCH,
        ),
    )
    detection_score = float(face.det_score)
    quality = float(np.clip(
        0.35 * detection_score + 0.25 * sharpness_score
        + 0.20 * size_score + 0.20 * pose_score,
        0.0, 1.0,
    ))
    return FaceEmbedding(embedding, quality, {
        "width": int(face_width),
        "height": int(face_height),
        "sharpness": round(sharpness, 2),
        "brightness": round(brightness, 2),
        "detection_score": round(detection_score, 4),
        "yaw": round(yaw, 2),
        "pitch": round(pitch, 2),
        "bbox": [int(x1), int(y1), int(x2), int(y2)],
    })


class OpenCVZooFaceAnalyzer:
    def __init__(self, provider_preference: str = "auto") -> None:
        if not config.FACE_LICENSE_ACCEPTED:
            logger.warning(
                "Face model loading is blocked; PPE_FACE_LICENSE_ACCEPTED is not true",
            )
            raise FaceModelUnavailable(
                "Set PPE_FACE_LICENSE_ACCEPTED=true only after model-license review and authorization"
            )
        if provider_preference not in {"auto", "cuda", "cpu"}:
            raise ValueError("Face provider preference must be auto, cuda, or cpu")
        if provider_preference == "cuda":
            raise FaceModelUnavailable(
                "The configured OpenCV Zoo face analyzer currently supports CPU inference only"
            )
        model_dir = Path(config.FACE_MODEL_DIR).expanduser().resolve()
        self._yunet_path = model_dir / config.FACE_YUNET_MODEL_FILENAME
        self._sface_path = model_dir / config.FACE_SFACE_MODEL_FILENAME
        if not self._yunet_path.is_file() or not self._sface_path.is_file():
            raise FaceModelUnavailable(
                "The configured YuNet/SFace models are not provisioned locally"
            )
        try:
            self._detector = cv2.FaceDetectorYN.create(
                str(self._yunet_path), "", (320, 320),
                config.FACE_MIN_DETECTION_SCORE, 0.3, 5000,
            )
            self._recognizer = cv2.FaceRecognizerSF.create(
                str(self._sface_path), "",
            )
        except cv2.error as exc:
            raise FaceModelUnavailable(
                "Could not initialize the local OpenCV YuNet/SFace models"
            ) from exc
        self._lock = threading.Lock()
        self.runtime_provider = "OpenCV DNN CPU"
        self.model_name = "opencv_zoo_yunet_sface"
        self.model_version = self._model_version(
            (self._yunet_path, self._sface_path),
        )

    @staticmethod
    def _model_version(model_files: tuple[Path, ...]) -> str:
        digest = hashlib.sha256()
        for model_file in model_files:
            with model_file.open("rb") as model_stream:
                for block in iter(lambda: model_stream.read(1024 * 1024), b""):
                    digest.update(block)
        return digest.hexdigest()

    def analyze(self, image: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
        if image is None or image.ndim != 3 or image.size == 0:
            raise ImageRejected("unreadable")
        height, width = image.shape[:2]
        try:
            with self._lock:
                self._detector.setInputSize((width, height))
                _, faces = self._detector.detect(image)
                if faces is None or len(faces) == 0:
                    raise ImageRejected("no_face")
                if len(faces) != 1:
                    raise ImageRejected("multiple_faces")
                face_row = np.asarray(faces[0], dtype=np.float32).reshape(-1)
                if face_row.size < 15:
                    raise FaceModelUnavailable(
                        "YuNet returned an incomplete face detection"
                    )
                aligned_crop = self._recognizer.alignCrop(image, face_row)
                embedding = self._recognizer.feature(aligned_crop)
        except ImageRejected:
            raise
        except cv2.error as exc:
            raise FaceModelUnavailable("Local face inference failed") from exc
        if face_row.size < 15:
            raise FaceModelUnavailable("YuNet returned an incomplete face detection")
        x, y, box_width, box_height = face_row[:4]
        face = SimpleNamespace(
            bbox=[
                x, y, x + box_width, y + box_height,
            ],
            kps=face_row[4:14].reshape(5, 2),
            det_score=float(face_row[14]),
            normed_embedding=np.asarray(embedding, dtype=np.float32).reshape(-1),
        )
        result = _quality_approved_face(face, image)
        return result.embedding, {
            **result.measurements,
            "quality_score": result.quality_score,
        }


class InsightFaceAnalyzer:
    """Local SCRFD detection and aligned 512-D ArcFace embeddings."""

    def __init__(self, provider_preference: str | None = None) -> None:
        if not config.FACE_LICENSE_ACCEPTED:
            raise FaceModelUnavailable(
                "Set PPE_FACE_LICENSE_ACCEPTED=true only after reviewing "
                "InsightFace and buffalo_l model terms and authorization"
            )
        provider_preference = provider_preference or config.FACE_PROVIDER
        if provider_preference not in {"auto", "cuda", "cpu"}:
            raise ValueError("Face provider preference must be auto, cuda, or cpu")
        try:
            import onnxruntime as ort
            from insightface.app import FaceAnalysis
        except ImportError as exc:
            raise FaceModelUnavailable(
                "Install InsightFace and ONNX Runtime to use buffalo_l"
            ) from exc

        model_directory = (
            Path(config.FACE_MODEL_DIR).expanduser().resolve()
            / "models" / "buffalo_l"
        )
        if not model_directory.is_dir() or not list(model_directory.glob("*.onnx")):
            raise FaceModelUnavailable(
                "Provision the buffalo_l models with scripts/download_insightface_model.py"
            )
        available = set(ort.get_available_providers())
        if provider_preference == "cuda" and "CUDAExecutionProvider" not in available:
            raise FaceModelUnavailable(
                "CUDA was requested but ONNX Runtime CUDAExecutionProvider is unavailable"
            )
        if provider_preference == "cpu":
            providers = ["CPUExecutionProvider"]
        elif "CUDAExecutionProvider" in available:
            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        else:
            providers = ["CPUExecutionProvider"]
        try:
            self._app = FaceAnalysis(
                name="buffalo_l",
                root=config.FACE_MODEL_DIR,
                allowed_modules=["detection", "recognition"],
                providers=providers,
            )
            self._app.prepare(
                ctx_id=0 if providers[0] == "CUDAExecutionProvider" else -1,
                det_size=(640, 640),
            )
        except Exception as exc:
            raise FaceModelUnavailable(
                "Could not initialize the local InsightFace buffalo_l models"
            ) from exc
        self._lock = threading.Lock()
        self.runtime_provider = (
            "ONNX Runtime CUDA"
            if providers[0] == "CUDAExecutionProvider"
            else "ONNX Runtime CPU"
        )
        self.model_name = "insightface_buffalo_l_scrfd_arcface"
        self.model_version = OpenCVZooFaceAnalyzer._model_version(
            tuple(sorted(model_directory.glob("*.onnx"))),
        )

    def analyze(self, image: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
        if image is None or image.ndim != 3 or image.size == 0:
            raise ImageRejected("unreadable")
        try:
            with self._lock:
                faces = self._app.get(image)
        except Exception as exc:
            raise FaceModelUnavailable("InsightFace inference failed") from exc
        if not faces:
            raise ImageRejected("no_face")

        ordered_faces = sorted(
            faces,
            key=lambda face: float(
                max(0, face.bbox[2] - face.bbox[0])
                * max(0, face.bbox[3] - face.bbox[1])
            ),
            reverse=True,
        )
        rejected: ImageRejected | None = None
        for face in ordered_faces:
            try:
                result = _quality_approved_face(face, image)
            except ImageRejected as exc:
                rejected = exc
                continue
            if result.embedding.size != 512:
                raise FaceModelUnavailable(
                    "InsightFace buffalo_l did not return a 512-D ArcFace embedding"
                )
            return result.embedding, {
                **result.measurements,
                "quality_score": result.quality_score,
                "alignment": "InsightFace five-point landmark alignment",
            }
        raise rejected or ImageRejected("no_usable_face")


class YoloFaceAnalyzer:
    """Detect face boxes with YOLO, then align/embed each crop with the gallery model."""

    def __init__(
            self, embedder: FaceAnalyzer, model_path: str | Path | None = None,
            model_loader: Any = None,
    ) -> None:
        weights_path = Path(
            model_path or config.FACE_DETECTOR_MODEL_PATH,
        ).expanduser().resolve()
        if not weights_path.is_file():
            raise FaceModelUnavailable(
                "YOLO face detector weights are unavailable; place "
                f"yolov8n-face.pt at {weights_path} or configure "
                "PPE_FACE_DETECTOR_MODEL_PATH"
            )
        if model_loader is None:
            try:
                from ultralytics import YOLO
            except ImportError as exc:
                raise FaceModelUnavailable(
                    "Install Ultralytics to use the configured YOLO face detector"
                ) from exc
            model_loader = YOLO
        try:
            self._detector = model_loader(str(weights_path), task="detect")
        except Exception as exc:
            raise FaceModelUnavailable(
                "Could not initialize the local YOLO face detector"
            ) from exc
        self._embedder = embedder
        self._lock = threading.Lock()
        self.model_name = embedder.model_name
        self.model_version = embedder.model_version
        self.detector_model_name = weights_path.name
        self.detector_model_version = OpenCVZooFaceAnalyzer._model_version(
            (weights_path,),
        )

    @property
    def runtime_provider(self) -> str:
        return getattr(self._embedder, "runtime_provider", "unknown")

    def analyze(self, image: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
        if image is None or image.ndim != 3 or image.size == 0:
            raise ImageRejected("unreadable")
        try:
            with self._lock:
                results = self._detector.predict(
                    source=image,
                    imgsz=config.FACE_DETECTOR_IMAGE_SIZE,
                    conf=config.FACE_DETECTOR_CONFIDENCE,
                    verbose=False,
                )
        except Exception as exc:
            raise FaceModelUnavailable("YOLO face detection failed") from exc
        if not results or getattr(results[0], "boxes", None) is None:
            raise ImageRejected("no_face")
        boxes = results[0].boxes
        try:
            coordinates = boxes.xyxy.cpu().numpy()
            confidences = boxes.conf.cpu().numpy()
        except (AttributeError, TypeError, ValueError) as exc:
            raise FaceModelUnavailable(
                "YOLO face detector returned malformed boxes",
            ) from exc
        if len(coordinates) == 0:
            raise ImageRejected("no_face")

        height, width = image.shape[:2]
        padding = config.FACE_DETECTOR_CROP_PADDING
        accepted: list[tuple[np.ndarray, dict[str, Any]]] = []
        rejected: list[ImageRejected] = []
        for box, confidence in zip(coordinates, confidences):
            x1, y1, x2, y2 = [float(value) for value in box[:4]]
            box_width, box_height = x2 - x1, y2 - y1
            if box_width <= 0 or box_height <= 0:
                continue
            pad_x, pad_y = box_width * padding, box_height * padding
            crop_x1 = max(0, int(np.floor(x1 - pad_x)))
            crop_y1 = max(0, int(np.floor(y1 - pad_y)))
            crop_x2 = min(width, int(np.ceil(x2 + pad_x)))
            crop_y2 = min(height, int(np.ceil(y2 + pad_y)))
            if crop_x2 <= crop_x1 or crop_y2 <= crop_y1:
                continue
            face_region = image[crop_y1:crop_y2, crop_x1:crop_x2]
            try:
                embedding, measurements = self._embedder.analyze(face_region)
            except ImageRejected as exc:
                rejected.append(exc)
                continue
            face_box = measurements.get("bbox")
            if isinstance(face_box, (list, tuple)) and len(face_box) == 4:
                measurements = {
                    **measurements,
                    "bbox": [
                        int(face_box[0]) + crop_x1,
                        int(face_box[1]) + crop_y1,
                        int(face_box[2]) + crop_x1,
                        int(face_box[3]) + crop_y1,
                    ],
                }
            measurements.update({
                "face_detector": self.detector_model_name,
                "face_detector_version": self.detector_model_version,
                "face_detector_confidence": round(float(confidence), 4),
                "yolo_bbox": [
                    max(0, int(np.floor(x1))),
                    max(0, int(np.floor(y1))),
                    min(width, int(np.ceil(x2))),
                    min(height, int(np.ceil(y2))),
                ],
            })
            accepted.append((embedding, measurements))
        if not accepted:
            if rejected:
                raise rejected[0]
            raise ImageRejected("invalid_face_box")
        return max(
            accepted,
            key=lambda result: float(result[1].get("quality_score", 0.0)),
        )


def create_face_analyzer(provider_preference: str | None = None) -> FaceAnalyzer:
    if config.FACE_MODEL_NAME == "opencv_zoo_yunet_sface":
        embedder: FaceAnalyzer = OpenCVZooFaceAnalyzer(
            provider_preference or "auto",
        )
    elif config.FACE_MODEL_NAME == "insightface_buffalo_l":
        embedder = InsightFaceAnalyzer(provider_preference)
    else:
        raise FaceModelUnavailable(
            f"Unsupported configured face model {config.FACE_MODEL_NAME!r}"
        )
    return YoloFaceAnalyzer(embedder)


def _cosine_similarity(left: np.ndarray, right: np.ndarray) -> float:
    left = np.asarray(left, dtype=np.float32).reshape(-1)
    right = np.asarray(right, dtype=np.float32).reshape(-1)
    if left.shape != right.shape:
        return -1.0
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    if denominator < 1e-8:
        return -1.0
    return float(np.dot(left, right) / denominator)


def _write_report(
        people: dict[str, PersonResult]) -> Path:
    report_dir = Path(config.FACE_REPORT_DIR)
    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report_path = report_dir / f"face-import-{timestamp}-{secrets.token_hex(3)}.csv"
    with report_path.open("w", encoding="utf-8-sig", newline="") as report_file:
        writer = csv.DictWriter(report_file, fieldnames=[
            "employee_code", "full_name", "department", "accepted",
            "rejected", "duplicates", "usable_photos", "weak_enrollment",
            "rejection_reasons", "review_flags", "review_flag_details",
        ])
        writer.writeheader()
        for person in sorted(people.values(), key=lambda item: item.employee_code):
            usable = person.preexisting + person.accepted
            writer.writerow({
                "employee_code": person.employee_code,
                "full_name": person.full_name,
                "department": person.department or "",
                "accepted": person.accepted,
                "rejected": person.rejected,
                "duplicates": person.duplicates,
                "usable_photos": usable,
                "weak_enrollment": "yes" if usable == 1 else "no",
                "rejection_reasons": "; ".join(
                    f"{reason}={count}"
                    for reason, count in sorted(person.rejection_reasons.items())
                ),
                "review_flags": "; ".join(sorted(set(person.flags))),
                "review_flag_details": "; ".join(
                    flag for flag in person.flags
                    if flag.startswith("within_folder_outlier:")
                ),
            })
    return report_path


async def _dry_run_import(
        people: dict[str, PersonImport],
        analyzer: FaceAnalyzer | None) -> dict[str, Any]:
    if not people:
        return {
            "run_id": None, "dry_run": True, "people": 0, "images": 0,
            "accepted": 0, "rejected": 0, "duplicates": 0,
            "zero_usable_employee_codes": [], "single_photo_employee_codes": [],
            "flags": [], "per_person": [],
            "report_path": str(_write_report({})),
        }
    face_analyzer = analyzer or create_face_analyzer()
    results: dict[str, PersonResult] = {}
    vectors: dict[str, list[np.ndarray]] = defaultdict(list)
    seen_hashes: dict[str, str] = {}
    rejected_total = duplicate_total = total_images = 0

    for code, person in people.items():
        result = PersonResult(code, person.full_name, person.department)
        results[code] = result
        for image_record in person.images:
            total_images += 1
            try:
                image_bytes = read_face_image(image_record.path)
            except OSError:
                result.rejected += 1
                result.rejection_reasons["unreadable"] = (
                    result.rejection_reasons.get("unreadable", 0) + 1
                )
                rejected_total += 1
                continue
            image_hash = hashlib.sha256(image_bytes).hexdigest()
            if image_hash in seen_hashes:
                result.duplicates += 1
                duplicate_total += 1
                other_code = seen_hashes[image_hash]
                if other_code != code:
                    result.flags.append(f"exact_image_duplicate_with:{other_code}")
                continue
            seen_hashes[image_hash] = code
            image = cv2.imdecode(
                np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR,
            )
            if image is None or image.size == 0:
                reason = "unreadable"
            else:
                try:
                    embedding, _ = await asyncio.to_thread(
                        face_analyzer.analyze, image,
                    )
                except ImageRejected as exc:
                    reason = exc.reason
                except ValueError as exc:
                    reason = str(exc)
                    if reason not in {
                        "no_face", "multiple_faces", "face_too_small",
                        "low_detection_confidence", "blurred", "non_frontal_yaw",
                        "non_frontal_pitch", "pose_unavailable", "invalid_face_box",
                        "invalid_embedding", "unreadable",
                    }:
                        raise
                else:
                    result.accepted += 1
                    result.embeddings.append(embedding)
                    result.image_hashes.append(image_hash)
                    vectors[code].append(embedding)
                    continue
            result.rejected += 1
            result.rejection_reasons[reason] = (
                result.rejection_reasons.get(reason, 0) + 1
            )
            rejected_total += 1

    flags = []
    for code, result in results.items():
        own_vectors = vectors[code]
        if len(own_vectors) == 1:
            result.flags.append("weak_single_photo")
        elif len(own_vectors) > 1:
            for index, vector in enumerate(own_vectors):
                scores = [
                    _cosine_similarity(vector, other)
                    for other_index, other in enumerate(own_vectors)
                    if other_index != index
                ]
                if scores and float(np.mean(scores)) < config.FACE_OUTLIER_THRESHOLD:
                    result.flags.append("within_folder_outlier")
                    flags.append({
                        "type": "within_folder_outlier",
                        "employee_code": code,
                        "mean_similarity": round(float(np.mean(scores)), 4),
                    })
    report_path = _write_report(results)
    per_person = [
        {
            "employee_code": person.employee_code,
            "full_name": person.full_name,
            "department": person.department,
            "accepted": person.accepted,
            "rejected": person.rejected,
            "duplicates": person.duplicates,
            "usable_photos": len(vectors[person.employee_code]),
            "rejection_reasons": person.rejection_reasons,
            "review_flags": sorted(set(person.flags)),
        }
        for person in sorted(results.values(), key=lambda value: value.employee_code)
    ]
    return {
        "run_id": None,
        "dry_run": True,
        "people": len(results),
        "images": total_images,
        "accepted": sum(person.accepted for person in results.values()),
        "rejected": rejected_total,
        "duplicates": duplicate_total,
        "zero_usable_employee_codes": [
            code for code in results if not vectors[code]
        ],
        "single_photo_employee_codes": [
            code for code in results if len(vectors[code]) == 1
        ],
        "flags": flags,
        "per_person": per_person,
        "report_path": str(report_path),
    }


async def import_face_dataset(
        dataset_path: Path | None = None,
        consent_name: str | None = None,
        consent_date: str | None = None,
        consent_reference: str | None = None,
        actor_user_id: int | None = None,
        actor_email: str | None = None,
        analyzer: FaceAnalyzer | None = None,
        dry_run: bool = False) -> dict[str, Any]:
    """Import a consented folder/CSV dataset with encrypted embeddings."""
    people = discover_dataset(
        Path(dataset_path or config.FACE_DATASET_PATH),
        consent_name, consent_date, consent_reference,
    )
    if dry_run:
        return await _dry_run_import(people, analyzer)

    await db.write_audit_log(
        "face_dataset_import_requested", actor_user_id, actor_email,
        target_type="face_dataset",
        details={
            "bulk_consent_details_supplied": bool(
                consent_name and consent_date and consent_reference
            ),
            "bulk_consent_confirmer": consent_name.strip() if consent_name else None,
            "bulk_consent_date": consent_date,
            "bulk_consent_reference": consent_reference,
        },
    )
    if not people:
        report_path = _write_report({})
        summary = {
            "people": 0, "images": 0, "accepted": 0, "rejected": 0,
            "duplicates": 0, "zero_usable_employee_codes": [],
            "single_photo_employee_codes": [], "flags": [], "per_person": [],
        }
        run_id = await db.create_face_import_run(
            "completed", actor_user_id, summary, str(report_path),
        )
        await db.write_audit_log(
            "face_dataset_imported", actor_user_id, actor_email,
            target_type="face_import_run", target_id=str(run_id),
            details={"people": 0, "images": 0, "accepted": 0, "rejected": 0},
        )
        return {"run_id": run_id, **summary, "report_path": str(report_path)}

    key = _encryption_key()
    face_analyzer = analyzer or create_face_analyzer()

    enrollments = await db.list_face_enrollments()
    code_to_id = {row["employee_code"]: int(row["person_id"]) for row in enrollments}
    existing_codes = set(code_to_id)
    inactive_codes = {
        row["employee_code"] for row in enrollments
        if not row["active"] and row["employee_code"] in people
    }
    if inactive_codes:
        raise ConsentError(
            "A withdrawn enrollment requires explicit manager reactivation before re-import"
        )
    existing_ids = set(code_to_id.values())
    known_vectors: dict[int, list[np.ndarray]] = defaultdict(list)
    known_hashes: dict[int, list[str]] = defaultdict(list)
    for row in await db.list_face_embeddings():
        person_id = int(row["person_id"])
        if person_id not in existing_ids or not row["active"]:
            continue
        if (row["model_name"] != face_analyzer.model_name
                or row["model_version"] != face_analyzer.model_version):
            continue
        known_vectors[person_id].append(decrypt_embedding(row["embedding"], key))
        known_hashes[person_id].append(row["image_hash"])

    person_results: dict[str, PersonResult] = {}
    for code, person in people.items():
        enrollment = await db.upsert_face_enrollment(
            person.employee_code, person.full_name, person.department,
            person.consent_basis, person.consent_date, actor_user_id,
            person.designation,
        )
        code_to_id[code] = int(enrollment["person_id"])
        await db.write_audit_log(
            "face_enrollment_updated" if code in existing_codes else "face_person_enrolled",
            actor_user_id, actor_email,
            target_type="face_enrollment",
            target_id=str(enrollment["person_id"]),
            details={"source": "dataset_import", "consent_recorded": True},
        )
        result = PersonResult(code, person.full_name, person.department)
        result.preexisting = len(known_vectors[code_to_id[code]])
        person_results[code] = result

    rejected_total = 0
    duplicate_total = 0
    total_images = 0
    flags: list[dict[str, Any]] = []
    seen_hashes: dict[str, tuple[str, int]] = {}
    all_vectors: dict[int, list[np.ndarray]] = defaultdict(list)
    for person_id, vectors in known_vectors.items():
        all_vectors[person_id].extend(vectors)
    all_hashes: dict[int, list[str]] = defaultdict(list)
    for person_id, hashes in known_hashes.items():
        all_hashes[person_id].extend(hashes)

    for employee_code, person in people.items():
        result = person_results[employee_code]
        person_id = code_to_id[employee_code]
        for image_record in person.images:
            total_images += 1
            try:
                image_bytes = read_face_image(image_record.path)
            except OSError:
                result.rejected += 1
                result.rejection_reasons["unreadable"] = (
                    result.rejection_reasons.get("unreadable", 0) + 1
                )
                rejected_total += 1
                continue
            image_hash = hashlib.sha256(image_bytes).hexdigest()
            local_duplicate = seen_hashes.get(image_hash)
            stored_duplicate = await db.get_face_embedding_by_hash(image_hash)
            duplicate_person = (
                local_duplicate[1] if local_duplicate else
                int(stored_duplicate["person_id"]) if stored_duplicate else None
            )
            if duplicate_person is not None:
                result.duplicates += 1
                duplicate_total += 1
                if duplicate_person != person_id:
                    other_code = next(
                        (
                            code for code, known_id in code_to_id.items()
                            if known_id == duplicate_person
                        ),
                        "previously_enrolled",
                    )
                    result.flags.append(f"exact_image_duplicate_with:{other_code}")
                    flags.append({
                        "type": "cross_identity_exact_image",
                        "employee_codes": sorted({employee_code, other_code}),
                    })
                continue
            try:
                encoded_image = np.frombuffer(image_bytes, dtype=np.uint8)
                image = cv2.imdecode(encoded_image, cv2.IMREAD_COLOR)
                if image is None or image.size == 0:
                    raise ImageRejected("unreadable")
                embedding, measurements = await asyncio.to_thread(
                    face_analyzer.analyze, image,
                )
                encrypted = encrypt_embedding(embedding, key)
            except ImageRejected as exc:
                result.rejected += 1
                result.rejection_reasons[exc.reason] = (
                    result.rejection_reasons.get(exc.reason, 0) + 1
                )
                rejected_total += 1
                continue
            except ValueError as exc:
                if str(exc) not in {"no_face", "multiple_faces", "face_too_small",
                                    "low_detection_confidence", "blurred",
                                    "non_frontal_yaw", "non_frontal_pitch",
                                    "pose_unavailable", "invalid_face_box",
                                    "invalid_embedding", "unreadable"}:
                    raise
                reason = str(exc)
                result.rejected += 1
                result.rejection_reasons[reason] = (
                    result.rejection_reasons.get(reason, 0) + 1
                )
                rejected_total += 1
                continue

            similarity_conflicts = []
            for other_person_id, vectors in all_vectors.items():
                if other_person_id == person_id:
                    continue
                best_similarity = max(
                    (_cosine_similarity(embedding, vector) for vector in vectors),
                    default=-1.0,
                )
                if best_similarity >= config.FACE_CROSS_ID_DUPLICATE_THRESHOLD:
                    similarity_conflicts.append((other_person_id, best_similarity))
            if similarity_conflicts:
                conflict_codes = sorted({
                    code for code, known_id in code_to_id.items()
                    if known_id in {
                        conflict_id for conflict_id, _ in similarity_conflicts
                    }
                })
                result.flags.append(
                    "cross_identity_embedding_similarity_with:"
                    + ",".join(conflict_codes or ["previously_enrolled"])
                )
                flags.append({
                    "type": "cross_identity_embedding_similarity",
                    "employee_codes": sorted({employee_code, *conflict_codes}),
                    "score": round(max(score for _, score in similarity_conflicts), 4),
                })

            photo_path = None
            photo_created = False
            if config.FACE_KEEP_PHOTOS:
                photo_dir = Path(config.FACE_PHOTO_DIR)
                photo_dir.mkdir(parents=True, exist_ok=True)
                photo_path = str(photo_dir / f"{image_hash}{image_record.path.suffix.lower()}")
                try:
                    with open(photo_path, "xb") as photo_file:
                        photo_file.write(encrypt_face_crop(image_bytes, key))
                    photo_created = True
                except FileExistsError:
                    pass
            try:
                embedding_id = await db.insert_face_embedding(
                    person_id=person_id,
                    embedding=encrypted,
                    quality_score=float(measurements["quality_score"]),
                    image_hash=image_hash,
                    photo_path=photo_path,
                    model_name=face_analyzer.model_name,
                    model_version=face_analyzer.model_version,
                )
            except Exception:
                if photo_created:
                    try:
                        Path(photo_path).unlink(missing_ok=True)
                    except OSError:
                        logger.warning(
                            "Could not clean up an imported source photo after storage failure",
                            exc_info=True,
                        )
                raise
            if embedding_id is None:
                if photo_created:
                    Path(photo_path).unlink(missing_ok=True)
                result.duplicates += 1
                duplicate_total += 1
                continue
            result.accepted += 1
            result.embeddings.append(embedding)
            result.image_hashes.append(image_hash)
            all_vectors[person_id].append(embedding)
            all_hashes[person_id].append(image_hash)
            seen_hashes[image_hash] = (employee_code, person_id)

    for employee_code, person in person_results.items():
        vectors = all_vectors[code_to_id[employee_code]]
        if len(vectors) == 1:
            person.flags.append("weak_single_photo")
        elif len(vectors) > 1:
            for index, vector in enumerate(vectors):
                other_scores = [
                    _cosine_similarity(vector, other)
                    for other_index, other in enumerate(vectors)
                    if other_index != index
                ]
                if other_scores and float(np.mean(other_scores)) < config.FACE_OUTLIER_THRESHOLD:
                    image_hash = (
                        all_hashes[code_to_id[employee_code]][index]
                        if index < len(all_hashes[code_to_id[employee_code]])
                        else "unknown"
                    )
                    person.flags.append(f"within_folder_outlier:{image_hash}")
                    flags.append({
                        "type": "within_folder_outlier",
                        "employee_code": employee_code,
                        "image_hash": image_hash,
                        "mean_similarity": round(float(np.mean(other_scores)), 4),
                    })

    report_path = _write_report(person_results)
    zero_usable = [
        person.employee_code for person in person_results.values()
        if not all_vectors[code_to_id[person.employee_code]]
    ]
    weak_people = [
        person.employee_code for person in person_results.values()
        if len(all_vectors[code_to_id[person.employee_code]]) == 1
    ]
    per_person = [
        {
            "employee_code": person.employee_code,
            "full_name": person.full_name,
            "department": person.department,
            "accepted": person.accepted,
            "rejected": person.rejected,
            "duplicates": person.duplicates,
            "usable_photos": len(all_vectors[code_to_id[person.employee_code]]),
            "rejection_reasons": person.rejection_reasons,
            "review_flags": sorted(set(person.flags)),
        }
        for person in sorted(person_results.values(), key=lambda value: value.employee_code)
    ]
    summary = {
        "people": len(person_results),
        "images": total_images,
        "accepted": sum(person.accepted for person in person_results.values()),
        "rejected": rejected_total,
        "duplicates": duplicate_total,
        "zero_usable_employee_codes": zero_usable,
        "single_photo_employee_codes": weak_people,
        "flags": flags,
        "per_person": per_person,
    }
    run_id = await db.create_face_import_run(
        "completed", actor_user_id, summary, str(report_path),
    )
    await db.write_audit_log(
        "face_dataset_imported", actor_user_id, actor_email,
        target_type="face_import_run", target_id=str(run_id),
        details={
            "people": summary["people"],
            "images": total_images,
            "accepted": summary["accepted"],
            "rejected": rejected_total,
            "duplicates": duplicate_total,
            "flag_count": len(flags),
        },
    )
    return {
        "run_id": run_id,
        **summary,
        "report_path": str(report_path),
    }


def print_import_summary(result: dict[str, Any]) -> None:
    print(
        ("Face dataset dry run: " if result.get("dry_run") else "Face dataset import: ")
        + f"{result['people']} people, {result['images']} images, "
        f"{result['accepted']} accepted, {result['rejected']} rejected, "
        f"{result['duplicates']} exact duplicates, {len(result['flags'])} review flags"
    )
    for person in result["per_person"]:
        print(
            f"{person['employee_code']} — {person['full_name']}: "
            f"{person['accepted']} accepted, {person['rejected']} rejected, "
            f"{person['duplicates']} duplicates"
        )
    print(f"Private CSV report: {result['report_path']}")
