"""Non-invasive checks for optional local face-recognition readiness."""
import importlib.metadata
from pathlib import Path
from typing import Any

from .. import config


def get_face_readiness(active_enrolled_people: int | None) -> dict[str, Any]:
    packages = {}
    for distribution in (
            "ultralytics", "insightface", "onnxruntime",
            "cryptography", "openpyxl"):
        try:
            packages[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            packages[distribution] = None
    providers = []
    if packages["onnxruntime"]:
        providers.append("ONNX Runtime CPU")
    provider_error = None if providers else "onnxruntime_not_installed"
    model_dir = Path(config.FACE_MODEL_DIR).expanduser()
    if config.FACE_MODEL_NAME == "insightface_buffalo_l":
        embedding_model_dir = model_dir / "models" / "buffalo_l"
        embedding_model_files = tuple(embedding_model_dir.glob("*.onnx"))
    else:
        embedding_model_files = (
            model_dir / config.FACE_YUNET_MODEL_FILENAME,
            model_dir / config.FACE_SFACE_MODEL_FILENAME,
        )
    detector_model_path = Path(
        config.FACE_DETECTOR_MODEL_PATH,
    ).expanduser()
    detector_model_present = (
        detector_model_path.is_file() and detector_model_path.stat().st_size > 0
    )
    embedding_models_present = bool(embedding_model_files) and all(
        path.is_file() and path.stat().st_size > 0
        for path in embedding_model_files
    )
    model_present = detector_model_present and embedding_models_present
    dataset_dir = Path(config.FACE_DATASET_PATH).expanduser()
    dataset_present = _has_dataset_inputs(dataset_dir)
    excel_manifest_present = _has_excel_manifest(dataset_dir)
    encryption_key_configured = _has_valid_encryption_key()
    missing = []
    if not packages["ultralytics"]:
        missing.append("Install Ultralytics for YOLO face detection")
    if not packages["insightface"]:
        missing.append("Install InsightFace for buffalo_l ArcFace embeddings")
    if not packages["onnxruntime"]:
        missing.append("Install ONNX Runtime for local face embedding inference")
    if not packages["cryptography"]:
        missing.append("Install cryptography for encrypted biometric data")
    if excel_manifest_present and not packages["openpyxl"]:
        missing.append("Install openpyxl to read the Excel face manifest")
    if provider_error:
        missing.append("ONNX Runtime is unavailable")
    if not config.FACE_LICENSE_ACCEPTED:
        missing.append("Review model terms and set PPE_FACE_LICENSE_ACCEPTED=true after authorization")
    if not model_present:
        if not detector_model_present:
            missing.append(
                "Install the local YOLO face detector weights configured by "
                "PPE_FACE_DETECTOR_MODEL_PATH"
            )
        if not embedding_models_present:
            missing.append(
                "Install the configured buffalo_l or YuNet/SFace embedding models"
            )
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
        "detector_model_path_configured": str(detector_model_path),
        "detector_model_present": detector_model_present,
        "embedding_model_present": embedding_models_present,
        "dataset_directory_configured": config.FACE_DATASET_PATH,
        "dataset_present": dataset_present,
        "excel_manifest_present": excel_manifest_present,
        "excel_manifest_supported": bool(packages["openpyxl"]),
        "face_model_files": [
            detector_model_path.name,
            *(path.name for path in embedding_model_files),
        ],
        "encryption_key_configured": encryption_key_configured,
        "active_enrolled_people": active_enrolled_people,
        "feature_enabled": config.FACE_ENABLED,
        "missing": missing,
    }


def _has_dataset_inputs(path: Path) -> bool:
    if not path.is_file() and not path.is_dir():
        return False
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
