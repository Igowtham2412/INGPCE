"""Non-invasive checks for optional local face-recognition readiness."""
import importlib.metadata
from pathlib import Path
from typing import Any

from . import config

_SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


def get_face_readiness(active_enrolled_people: int | None) -> dict[str, Any]:
    packages = {}
    for distribution in (
        "insightface", "onnxruntime", "cryptography", "openpyxl",
    ):
        try:
            packages[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            packages[distribution] = None
    try:
        packages["onnxruntime-gpu"] = importlib.metadata.version("onnxruntime-gpu")
    except importlib.metadata.PackageNotFoundError:
        packages["onnxruntime-gpu"] = None

    providers: list[str] = []
    provider_error = None
    try:
        import onnxruntime

        providers = list(onnxruntime.get_available_providers())
    except ImportError:
        provider_error = "onnxruntime_not_installed"
    except (OSError, RuntimeError) as exc:
        provider_error = type(exc).__name__

    model_dir = Path(config.FACE_MODEL_DIR).expanduser() / config.FACE_MODEL_NAME
    model_present = model_dir.is_dir() and any(model_dir.glob("*.onnx"))
    dataset_dir = Path(config.FACE_DATASET_PATH).expanduser()
    dataset_present = _has_dataset_inputs(dataset_dir)
    excel_manifest_present = _has_excel_manifest(dataset_dir)
    missing = []
    if not packages["insightface"]:
        missing.append("Install the optional InsightFace package")
    if not packages["onnxruntime"]:
        missing.append("Install an ONNX Runtime package")
    if not packages["cryptography"]:
        missing.append("Install cryptography for encrypted biometric data")
    if excel_manifest_present and not packages["openpyxl"]:
        missing.append("Install openpyxl to read the Excel face manifest")
    if provider_error:
        missing.append("ONNX Runtime provider discovery failed")
    if not config.FACE_LICENSE_ACCEPTED:
        missing.append("Review model terms and set PPE_FACE_LICENSE_ACCEPTED=true after authorization")
    if not model_present:
        missing.append("Provision the configured, licensed ONNX model pack")
    if not dataset_present:
        missing.append("Upload a validated consented face dataset")
    if not active_enrolled_people:
        missing.append("Import at least one active consented enrollment")
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
        "active_enrolled_people": active_enrolled_people,
        "feature_enabled": config.FACE_ENABLED,
        "missing": missing,
    }


def _has_dataset_inputs(path: Path) -> bool:
    if path.is_file():
        return path.suffix.lower() in {".csv", ".xlsx"}
    if not path.is_dir():
        return False
    return any(
        item.is_file()
        and (
            item.suffix.lower() == ".csv"
            or item.suffix.lower() == ".xlsx"
            or item.suffix.lower() in _SUPPORTED_IMAGE_SUFFIXES
        )
        for item in path.rglob("*")
        if ".gitkeep" not in item.name and "reports" not in item.parts
    )


def _has_excel_manifest(path: Path) -> bool:
    if path.is_file():
        return path.suffix.lower() == ".xlsx"
    if not path.is_dir():
        return False
    return any(
        item.is_file() and item.suffix.lower() == ".xlsx"
        for item in path.iterdir()
    )
