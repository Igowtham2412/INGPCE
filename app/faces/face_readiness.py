"""Non-invasive checks for optional local face-recognition readiness."""
import importlib.metadata
from pathlib import Path
from typing import Any

from .. import config

_SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


def get_face_readiness(active_enrolled_people: int | None) -> dict[str, Any]:
    packages = {}
    for distribution in ("opencv-python", "cryptography", "openpyxl"):
        try:
            packages[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            packages[distribution] = None
    providers = ["OpenCV DNN CPU"] if packages["opencv-python"] else []
    provider_error = None if providers else "opencv_not_installed"
    model_dir = Path(config.FACE_MODEL_DIR).expanduser()
    model_files = (
        model_dir / config.FACE_YUNET_MODEL_FILENAME,
        model_dir / config.FACE_SFACE_MODEL_FILENAME,
    )
    model_present = all(path.is_file() and path.stat().st_size > 0 for path in model_files)
    dataset_dir = Path(config.FACE_DATASET_PATH).expanduser()
    dataset_present = _has_dataset_inputs(dataset_dir)
    excel_manifest_present = _has_excel_manifest(dataset_dir)
    encryption_key_configured = _has_valid_encryption_key()
    missing = []
    if not packages["opencv-python"]:
        missing.append("Install OpenCV with YuNet and SFace support")
    if not packages["cryptography"]:
        missing.append("Install cryptography for encrypted biometric data")
    if excel_manifest_present and not packages["openpyxl"]:
        missing.append("Install openpyxl to read the Excel face manifest")
    if provider_error:
        missing.append("OpenCV DNN is unavailable")
    if not config.FACE_LICENSE_ACCEPTED:
        missing.append("Review model terms and set PPE_FACE_LICENSE_ACCEPTED=true after authorization")
    if not model_present:
        missing.append("Install the checksum-verified local YuNet and SFace models")
    if not dataset_present:
        missing.append("Upload a validated consented face dataset")
    if not active_enrolled_people:
        missing.append("Import at least one active consented enrollment")
    if not encryption_key_configured:
        missing.append("Configure PPE_FACE_ENCRYPTION_KEY in the local secret store")
    if config.FACE_ENABLED is False:
        missing.append("Enable PPE_FACE_ENABLED only after approval and calibration")

    return {
        "ready_for_recognition": not missing,
        "packages_installed": packages,
        "providers_available": providers,
        "provider_error": provider_error,
        "license_flag_accepted": config.FACE_LICENSE_ACCEPTED,
        "model_name": config.FACE_MODEL_NAME,
        "model_directory_configured": config.FACE_MODEL_DIR,
        "model_present": model_present,
        "dataset_directory_configured": config.FACE_DATASET_PATH,
        "dataset_present": dataset_present,
        "excel_manifest_present": excel_manifest_present,
        "excel_manifest_supported": bool(packages["openpyxl"]),
        "face_model_files": [path.name for path in model_files],
        "encryption_key_configured": encryption_key_configured,
        "active_enrolled_people": active_enrolled_people,
        "feature_enabled": config.FACE_ENABLED,
        "missing": missing,
    }


def _has_dataset_inputs(path: Path) -> bool:
    if path.is_file():
        return (
            path.suffix.lower() in {".csv", ".xlsx"}
            and _manifest_has_enrollments(path)
        )
    if not path.is_dir():
        return False
    manifests = sorted(
        item for item in path.iterdir()
        if item.is_file() and item.suffix.lower() in {".csv", ".xlsx"}
    )
    if manifests:
        return len(manifests) == 1 and _manifest_has_enrollments(manifests[0])
    return any(
        folder.is_dir()
        and "_" in folder.name
        and folder.name not in {"reports", "enrolled_photos"}
        and any(
            image.is_file() and image.suffix.lower() in _SUPPORTED_IMAGE_SUFFIXES
            for image in folder.rglob("*")
        )
        for folder in path.iterdir()
    )


def _manifest_has_enrollments(path: Path) -> bool:
    try:
        from .face_dataset import discover_dataset

        people = discover_dataset(path)
    except (ImportError, OSError, RuntimeError, ValueError):
        return False
    return any(person.images for person in people.values())


def _has_valid_encryption_key() -> bool:
    try:
        from .face_dataset import _encryption_key

        _encryption_key()
    except (ImportError, RuntimeError):
        return False
    return True


def _has_excel_manifest(path: Path) -> bool:
    if path.is_file():
        return path.suffix.lower() == ".xlsx"
    if not path.is_dir():
        return False
    return any(
        item.is_file() and item.suffix.lower() == ".xlsx"
        for item in path.iterdir()
    )
