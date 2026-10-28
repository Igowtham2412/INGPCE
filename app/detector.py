"""
detector.py — wraps Ultralytics YOLO, normalizes class names via config.CLASS_MAP.
"""
import logging
import threading
from dataclasses import dataclass
from pathlib import Path

import torch
from ultralytics import YOLO

from . import config

logger = logging.getLogger("ppe.detector")
_FALLBACK_INFERENCE_LOCK = threading.Lock()


def _resolve_device(device: str) -> str:
    requested = device.strip().lower()
    if requested == "auto":
        return "0" if torch.cuda.is_available() else "cpu"
    if requested == "cuda":
        if torch.cuda.is_available():
            return "0"
        logger.warning("PPE_DEVICE=cuda requested but CUDA is unavailable; falling back to CPU")
        return "cpu"
    return device


@dataclass
class Detection:
    cls_raw: str
    cls_norm: str
    confidence: float
    x1: int
    y1: int
    x2: int
    y2: int
    class_id: int = -1

    @property
    def box(self):
        return (self.x1, self.y1, self.x2, self.y2)

def _point_in_polygon(point, polygon) -> bool:
    x, y = point
    inside = False
    previous = polygon[-1]
    for current in polygon:
        x1, y1 = previous
        x2, y2 = current
        if (y1 > y) != (y2 > y):
            crossing_x = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < crossing_x:
                inside = not inside
        previous = current
    return inside

def filter_detections(detections: list[Detection], frame_shape, camera_id: str = None,
                      camera_rois=None) -> list[Detection]:
    frame_height, frame_width = frame_shape[:2]
    frame_area = frame_width * frame_height
    roi_polygons = camera_rois
    if roi_polygons is None:
        configured_polygon = config.CAMERA_ROIS.get(camera_id)
        roi_polygons = [configured_polygon] if configured_polygon else []
    elif roi_polygons and isinstance(roi_polygons[0][0], (int, float)):
        roi_polygons = [roi_polygons]
    filtered = []
    for detection in detections:
        threshold = config.CLASS_CONFIDENCE_THRESHOLDS.get(
            detection.cls_norm, config.CONFIDENCE_THRESHOLD
        )
        if detection.cls_norm == "person":
            threshold = min(threshold, config.TRACK_LOW_THRESHOLD)
        if detection.confidence < threshold:
            continue

        width = detection.x2 - detection.x1
        height = detection.y2 - detection.y1
        if width <= 0 or height <= 0:
            continue

        box_filter = config.CLASS_BOX_FILTERS.get(detection.cls_norm, {})
        if width * height / max(frame_area, 1) < box_filter.get("min_area_fraction", 0):
            continue
        aspect_ratio = width / height
        if aspect_ratio < box_filter.get("min_aspect_ratio", 0):
            continue
        if aspect_ratio > box_filter.get("max_aspect_ratio", float("inf")):
            continue

        if roi_polygons:
            center = ((detection.x1 + detection.x2) / (2 * frame_width),
                      (detection.y1 + detection.y2) / (2 * frame_height))
            if not any(_point_in_polygon(center, polygon) for polygon in roi_polygons):
                continue
        filtered.append(detection)
    return filtered


class PPEDetector:
    def __init__(self, model_path: str = config.MODEL_PATH, device: str = config.DEVICE):
        self.device = _resolve_device(device)
        self.half = self.device != "cpu"
        self._inference_lock = threading.Lock()
        if self.device == "cpu":
            torch.set_num_threads(config.CPU_INFERENCE_THREADS)
        logger.info(
            "Loading PPE model from %s on device=%s half=%s",
            model_path, self.device, self.half,
        )
        self.model = YOLO(model_path)
        self.names = self.model.names
        phone_model_path = Path(config.PHONE_MODEL_PATH)
        if not phone_model_path.is_file():
            raise FileNotFoundError(
                f"Phone detection model is missing: {phone_model_path}. "
                "Run .\\.venv\\Scripts\\python.exe scripts\\download_phone_model.py"
            )
        logger.info(
            "Loading phone detection model from %s on device=%s half=%s",
            phone_model_path, self.device, self.half,
        )
        self.phone_model = YOLO(str(phone_model_path))
        self.phone_names = self.phone_model.names
        self.phone_class_ids = [
            int(class_id)
            for class_id, name in self.phone_names.items()
            if config.CLASS_MAP.get(name, name.lower()) == "phone"
        ]
        if not self.phone_class_ids:
            raise ValueError(
                f"Phone model {phone_model_path} has no recognized cell phone class"
            )
        self.normalized_classes = {
            config.CLASS_MAP.get(raw_name, raw_name.lower())
            for raw_name in self.names.values()
        }
        self.normalized_classes.add("phone")

    def _predict(self, model, **options):
        options["device"] = self.device
        half = getattr(self, "half", self.device != "cpu")
        options["half"] = half
        try:
            return model.predict(**options)
        except RuntimeError:
            if self.device == "cpu":
                raise
            logger.warning(
                "CUDA inference failed for %s; retrying on CPU",
                type(model).__name__,
                exc_info=True,
            )
            self.device = "cpu"
            self.half = False
            torch.set_num_threads(config.CPU_INFERENCE_THREADS)
            options["device"] = self.device
            options["half"] = self.half
            return model.predict(**options)

    def infer(self, frame, camera_id: str = None, camera_rois=None) -> list[Detection]:
        with getattr(self, "_inference_lock", _FALLBACK_INFERENCE_LOCK):
            return self._infer_locked(frame, camera_id, camera_rois)

    def _infer_locked(self, frame, camera_id: str = None,
                      camera_rois=None) -> list[Detection]:
        confidence = min(
            config.CONFIDENCE_THRESHOLD,
            config.TRACK_LOW_THRESHOLD,
            *config.CLASS_CONFIDENCE_THRESHOLDS.values(),
        )
        detections: list[Detection] = []
        model_predictions = [
            (
                self.model,
                self.names,
                {
                    "conf": confidence,
                    "imgsz": config.INFERENCE_IMG_SIZE,
                },
            ),
        ]
        phone_model = getattr(self, "phone_model", None)
        if phone_model is None:
            logger.warning("Phone model is not initialized; running PPE inference only")
        else:
            model_predictions.append((
                phone_model,
                self.phone_names,
                {
                    "conf": config.PHONE_CONFIDENCE_THRESHOLD,
                    "classes": self.phone_class_ids,
                    "imgsz": config.INFERENCE_IMG_SIZE,
                },
            ))
        for model, names, options in model_predictions:
            results = self._predict(
                model, source=frame, iou=config.IOU_THRESHOLD,
                verbose=False, **options,
            )
            if not results or results[0].boxes is None:
                continue
            for box in results[0].boxes:
                cls_id = int(box.cls[0].item())
                raw_name = names.get(cls_id, str(cls_id))
                norm_name = config.CLASS_MAP.get(raw_name, raw_name.lower())
                conf = float(box.conf[0].item())
                x1, y1, x2, y2 = [int(v) for v in box.xyxy[0].tolist()]
                detections.append(Detection(
                    cls_raw=raw_name, cls_norm=norm_name, confidence=conf,
                    x1=x1, y1=y1, x2=x2, y2=y2, class_id=cls_id,
                ))
        return filter_detections(detections, frame.shape, camera_id, camera_rois)
