"""Export reviewed incident crops and model-assisted YOLO labels."""
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import cv2

from . import config


def _valid_box(value: Any) -> tuple[int, int, int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError("Detection metadata contains an invalid box")
    box = tuple(int(coordinate) for coordinate in value)
    if box[2] <= box[0] or box[3] <= box[1]:
        raise ValueError("Detection metadata contains an empty box")
    return box


def _crop_and_labels(image, metadata: dict[str, Any], violation_type: str,
                     review_status: str, correction_type: str = None,
                     corrected_class: str = None,
                     corrected_box: list[int] = None) -> tuple[Any, list[str]]:
    height, width = image.shape[:2]
    person = _valid_box(metadata.get("person_box"))
    x1, y1, x2, y2 = person
    padding_x = max(1, int((x2 - x1) * 0.1))
    padding_y = max(1, int((y2 - y1) * 0.1))
    crop_x1, crop_y1 = max(0, x1 - padding_x), max(0, y1 - padding_y)
    crop_x2, crop_y2 = min(width, x2 + padding_x), min(height, y2 + padding_y)
    if crop_x2 <= crop_x1 or crop_y2 <= crop_y1:
        raise ValueError("Person box is outside the clean evidence image")
    crop = image[crop_y1:crop_y2, crop_x1:crop_x2].copy()
    crop_height, crop_width = crop.shape[:2]
    target_class = config.NEGATIVE_PPE_CLASSES.get(violation_type, violation_type)
    if correction_type == "false_person":
        return crop, []

    if correction_type == "ppe_corrected":
        if corrected_class not in config.POSITIVE_PPE_CLASSES:
            raise ValueError("Corrected PPE class is not supported")
        if not isinstance(corrected_box, (list, tuple)) or len(corrected_box) != 4:
            raise ValueError("Draw a box around the correctly visible PPE")
        corrected_box = _valid_box(corrected_box)
        if (corrected_box[0] < 0 or corrected_box[1] < 0
                or corrected_box[2] > width or corrected_box[3] > height):
            raise ValueError("Corrected PPE box is outside the evidence image")
        class_ids = {
            int(class_id): config.CLASS_MAP.get(class_name, class_name.lower())
            for class_id, class_name in metadata.get("model_names", {}).items()
        }
        corrected_class_id = next(
            (class_id for class_id, name in class_ids.items() if name == corrected_class),
            None,
        )
        if corrected_class_id is None:
            raise ValueError(f"The active model has no {corrected_class} class")

    labels = []
    detections = metadata.get("detections", [])
    if not isinstance(detections, list):
        raise ValueError("Detection metadata must contain a list")

    for detection in detections:
        if not isinstance(detection, dict):
            raise ValueError("Detection metadata contains an invalid detection")
        class_id = int(detection.get("class_id", -1))
        if class_id < 0:
            continue
        box = _valid_box(detection.get("box"))
        dx1, dy1, dx2, dy2 = box
        ix1, iy1 = max(dx1, crop_x1), max(dy1, crop_y1)
        ix2, iy2 = min(dx2, crop_x2), min(dy2, crop_y2)
        if ix2 <= ix1 or iy2 <= iy1:
            continue
        normalized_name = detection.get("class_name", "")
        if review_status == "false_positive" and normalized_name == target_class:
            continue
        if (correction_type == "ppe_corrected"
                and normalized_name in {target_class, corrected_class}):
            continue
        center_x = ((ix1 + ix2) / 2 - crop_x1) / crop_width
        center_y = ((iy1 + iy2) / 2 - crop_y1) / crop_height
        box_width = (ix2 - ix1) / crop_width
        box_height = (iy2 - iy1) / crop_height
        if not (0 <= center_x <= 1 and 0 <= center_y <= 1):
            continue
        labels.append(
            f"{class_id} {center_x:.6f} {center_y:.6f} {box_width:.6f} {box_height:.6f}"
        )
    if correction_type == "ppe_corrected":
        dx1, dy1, dx2, dy2 = corrected_box
        ix1, iy1 = max(dx1, crop_x1), max(dy1, crop_y1)
        ix2, iy2 = min(dx2, crop_x2), min(dy2, crop_y2)
        if ix2 <= ix1 or iy2 <= iy1:
            raise ValueError("Corrected PPE box must overlap the person's training crop")
        labels.append(
            f"{corrected_class_id} "
            f"{((ix1 + ix2) / 2 - crop_x1) / crop_width:.6f} "
            f"{((iy1 + iy2) / 2 - crop_y1) / crop_height:.6f} "
            f"{(ix2 - ix1) / crop_width:.6f} "
            f"{(iy2 - iy1) / crop_height:.6f}"
        )
    return crop, labels


def export_reviewed_violation(violation: dict[str, Any], review_status: str,
                              dataset_dir: str, correction_type: str = None,
                              corrected_class: str = None,
                              corrected_box: list[int] = None) -> tuple[str, str]:
    if review_status not in {"confirmed", "false_positive"}:
        raise ValueError("review_status must be confirmed or false_positive")
    clean_path = violation.get("clean_snapshot_path")
    if not clean_path or not os.path.isfile(clean_path):
        raise FileNotFoundError("Clean evidence image is not available for this violation")
    metadata = violation.get("detection_metadata")
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    if not isinstance(metadata, dict):
        raise ValueError("Violation has no usable detection metadata")
    image = cv2.imread(clean_path)
    if image is None:
        raise ValueError("Clean evidence image could not be decoded")
    crop, labels = _crop_and_labels(
        image, metadata, violation["violation_type"], review_status,
        correction_type, corrected_class, corrected_box,
    )

    images_dir = Path(dataset_dir) / review_status / "images"
    labels_dir = Path(dataset_dir) / review_status / "labels"
    images_dir.mkdir(parents=True, exist_ok=True)
    labels_dir.mkdir(parents=True, exist_ok=True)
    stem = f"violation_{violation['id']}_{uuid4().hex[:8]}"
    image_path = images_dir / f"{stem}.jpg"
    label_path = labels_dir / f"{stem}.txt"
    if not cv2.imwrite(str(image_path), crop):
        raise OSError(f"Could not write review image: {image_path}")
    try:
        label_path.write_text("\n".join(labels) + ("\n" if labels else ""), encoding="utf-8")
    except OSError:
        image_path.unlink(missing_ok=True)
        raise

    names = metadata.get("model_names", {})
    names_path = Path(dataset_dir) / "classes.json"
    names_path.write_text(json.dumps(names, indent=2), encoding="utf-8")
    return str(image_path), str(label_path)
