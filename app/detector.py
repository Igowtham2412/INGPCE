"""
detector.py — wraps Ultralytics YOLO, normalizes class names via config.CLASS_MAP.
"""
import logging
from dataclasses import dataclass
from typing import List

from ultralytics import YOLO

from . import config

logger = logging.getLogger("ppe.detector")


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

def filter_detections(detections: List[Detection], frame_shape, camera_id: str = None,
                      camera_rois=None) -> List[Detection]:
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
        logger.info(f"Loading PPE model from {model_path} on device={device}")
        self.model = YOLO(model_path)
        self.device = device
        self.names = self.model.names
        self.normalized_classes = {
            config.CLASS_MAP.get(raw_name, raw_name.lower())
            for raw_name in self.names.values()
        }

    def infer(self, frame, camera_id: str = None, camera_rois=None) -> List[Detection]:
        confidence = min(
            config.CONFIDENCE_THRESHOLD,
            config.TRACK_LOW_THRESHOLD,
            *config.CLASS_CONFIDENCE_THRESHOLDS.values(),
        )
        results = self.model.predict(
            source=frame, conf=confidence, iou=config.IOU_THRESHOLD,
            imgsz=config.INFERENCE_IMG_SIZE, device=self.device, verbose=False,
        )
        detections: List[Detection] = []
        if not results:
            return filter_detections(detections, frame.shape, camera_id, camera_rois)
        r = results[0]
        if r.boxes is None:
            return filter_detections(detections, frame.shape, camera_id, camera_rois)
        for box in r.boxes:
            cls_id = int(box.cls[0].item())
            raw_name = self.names.get(cls_id, str(cls_id))
            norm_name = config.CLASS_MAP.get(raw_name, raw_name.lower())
            conf = float(box.conf[0].item())
            x1, y1, x2, y2 = [int(v) for v in box.xyxy[0].tolist()]
            detections.append(Detection(
                cls_raw=raw_name, cls_norm=norm_name, confidence=conf,
                x1=x1, y1=y1, x2=x2, y2=y2, class_id=cls_id,
            ))
        return filter_detections(detections, frame.shape, camera_id, camera_rois)
