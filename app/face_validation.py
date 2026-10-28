"""Model-free checks for face enrollment folders and CSV/Excel manifests."""
import hashlib
from datetime import date
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .face_manifest import read_face_manifest

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
_IGNORED_FOLDERS = {"reports", "enrolled_photos"}
_TRUE_VALUES = {"1", "true", "yes"}


def _issue(level: str, code: str, person_code: str | None = None) -> dict[str, Any]:
    result = {"level": level, "code": code}
    if person_code:
        result["employee_code"] = person_code
    return result


def _folder_layout(
        root: Path, consent_name: str | None, consent_date: str | None,
        consent_reference: str | None, report: dict[str, Any],
) -> dict[str, list]:
    folders: dict[str, list] = {}
    supplied = (consent_name, consent_date, consent_reference)
    if not all(supplied):
        report["issues"].append(_issue("error", "bulk_consent_details_missing"))
    elif not consent_name.strip() or not consent_reference.strip():
        report["issues"].append(_issue("error", "bulk_consent_details_invalid"))
    else:
        try:
            date.fromisoformat(consent_date.strip())
        except ValueError:
            report["issues"].append(_issue("error", "bulk_consent_date_invalid"))

    for folder in sorted(root.iterdir()):
        if not folder.is_dir() or folder.name in _IGNORED_FOLDERS:
            continue
        if "_" not in folder.name:
            report["issues"].append(_issue("error", "folder_name_missing_employee_code_separator"))
            continue
        code, full_name = folder.name.split("_", 1)
        code, full_name = code.strip(), full_name.replace("_", " ").strip()
        if not code or not full_name:
            report["issues"].append(_issue("error", "folder_name_invalid"))
            continue
        if code in folders:
            report["issues"].append(_issue("error", "duplicate_employee_code", code))
            continue
        folders[code] = [
            path for path in sorted(folder.iterdir()) if path.is_file()
        ]
    return folders


def _manifest_layout(
        manifest_path: Path, report: dict[str, Any],
        bulk_consent: bool = False,
) -> dict[str, list]:
    people: dict[str, list] = {}
    try:
        headers, rows = read_face_manifest(manifest_path)
    except (OSError, RuntimeError, ValueError):
        report["issues"].append(_issue("error", "manifest_unreadable"))
        return people

    required = {"employee_code", "full_name", "department", "image_path"}
    missing = sorted(required - headers)
    if missing:
        report["issues"].append(_issue(
            "error", "required_columns_missing:" + ",".join(missing),
        ))
        return people
    missing_consent = sorted({"consent_recorded", "consent_reference"} - headers)
    if bulk_consent:
        missing_consent = []
    if missing_consent:
        report["issues"].append(_issue(
            "error", "consent_columns_missing:" + ",".join(missing_consent),
        ))

    root = manifest_path.parent.resolve()
    for row in rows:
        code = row.get("employee_code", "").strip()
        full_name = row.get("full_name", "").strip()
        image_path = row.get("image_path", "").strip()
        if not code or not full_name or not image_path:
            report["issues"].append(_issue("error", "manifest_identity_or_image_path_empty"))
            continue
        if code not in people:
            people[code] = []
        if "consent_recorded" in headers and (
            row.get("consent_recorded", "").strip().lower() not in _TRUE_VALUES
        ):
            report["issues"].append(_issue("error", "consent_not_recorded", code))
        if not bulk_consent and "consent_reference" in headers and not row.get(
            "consent_reference", "",
        ).strip():
            report["issues"].append(_issue("error", "consent_reference_missing", code))
        candidate = (root / image_path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            report["issues"].append(_issue("error", "image_path_outside_dataset", code))
            continue
        people[code].append(candidate)
    if not rows:
        report["issues"].append(_issue("error", "manifest_has_no_employee_records"))
    return people


def validate_face_dataset(
        dataset_path: Path, min_photos: int = 5,
        consent_name: str | None = None,
        consent_date: str | None = None,
        consent_reference: str | None = None) -> dict[str, Any]:
    """Validate layout, consent markers, image decoding, and duplicate hashes."""
    if min_photos < 1:
        raise ValueError("Minimum photo count must be positive")
    report: dict[str, Any] = {
        "dataset": Path(dataset_path).name,
        "layout": "unknown",
        "valid": False,
        "minimum_photos_recommended": min_photos,
        "people": [],
        "total_images": 0,
        "readable_images": 0,
        "duplicate_images": 0,
        "issues": [],
        "warnings": [],
    }
    source = Path(dataset_path).expanduser()
    if not source.exists():
        report["issues"].append(_issue("error", "dataset_path_missing"))
        return report

    if source.is_file() and source.suffix.lower() in {".csv", ".xlsx"}:
        report["layout"] = "manifest"
        bulk_consent = _valid_bulk_consent(
            consent_name, consent_date, consent_reference,
        )
        people = _manifest_layout(source.resolve(), report, bulk_consent)
    elif source.is_dir():
        manifests = sorted(
            path for path in source.iterdir()
            if path.is_file() and path.suffix.lower() in {".csv", ".xlsx"}
        )
        if len(manifests) > 1:
            report["issues"].append(_issue("error", "multiple_manifests"))
            return report
        if manifests:
            report["layout"] = "manifest"
            bulk_consent = _valid_bulk_consent(
                consent_name, consent_date, consent_reference,
            )
            people = _manifest_layout(manifests[0], report, bulk_consent)
        else:
            report["layout"] = "employee_folders"
            people = _folder_layout(
                source, consent_name, consent_date, consent_reference, report,
            )
    else:
        report["issues"].append(_issue("error", "dataset_path_not_directory_or_csv"))
        return report

    seen_hashes: dict[str, str] = {}
    for code, paths in sorted(people.items()):
        readable = 0
        duplicates = 0
        for path in paths:
            report["total_images"] += 1
            if path.suffix.lower() not in _IMAGE_SUFFIXES:
                report["issues"].append(_issue("error", "unsupported_image_type", code))
                continue
            try:
                image_bytes = path.read_bytes()
            except OSError:
                report["issues"].append(_issue("error", "image_unreadable", code))
                continue
            image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
            if image is None or image.size == 0:
                report["issues"].append(_issue("error", "image_unreadable", code))
                continue
            readable += 1
            report["readable_images"] += 1
            digest = hashlib.sha256(image_bytes).hexdigest()
            other_code = seen_hashes.get(digest)
            if other_code is not None:
                duplicates += 1
                report["duplicate_images"] += 1
                report["warnings"].append(_issue(
                    "warning", "duplicate_image_hash", code,
                ) | {"duplicate_with_employee_code": other_code})
            else:
                seen_hashes[digest] = code

        person_report = {
            "employee_code": code,
            "photos_found": len(paths),
            "readable_photos": readable,
            "duplicate_photos": duplicates,
        }
        if readable < min_photos:
            person_report["photos_below_recommended_minimum"] = True
            report["warnings"].append(_issue("warning", "too_few_photos", code))
        report["people"].append(person_report)

    if not people:
        report["warnings"].append(_issue("warning", "no_employee_records_found"))
    report["valid"] = not any(
        issue["level"] == "error" for issue in report["issues"]
    )
    return report


def _valid_bulk_consent(
        consent_name: str | None, consent_date: str | None,
        consent_reference: str | None) -> bool:
    if not consent_name or not consent_name.strip():
        return False
    if not consent_reference or not consent_reference.strip():
        return False
    if not consent_date:
        return False
    try:
        date.fromisoformat(consent_date.strip())
    except ValueError:
        return False
    return True
