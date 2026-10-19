"""
compliance_engine.py — matches PPE detections to persons, tracks violation
streaks so momentary occlusion/bad angle doesn't trigger false alerts.
"""
import logging
from dataclasses import dataclass, field
from collections import deque
from types import SimpleNamespace
from typing import List, Dict, Set, Tuple

import numpy as np
from ultralytics.engine.results import Boxes
from ultralytics.trackers.byte_tracker import BYTETracker

from . import config
from .detector import Detection

logger = logging.getLogger("ppe.compliance")


def _overlap_fraction_of_b(box_a, box_b) -> float:
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    area_b = max(1, (bx2 - bx1) * (by2 - by1))
    return inter / area_b

def _in_person_region(person_box, item_box, ppe_type: str) -> bool:
    y1, y2 = config.PPE_ASSOCIATION_REGIONS.get(ppe_type, [0.0, 1.0])
    person_height = max(1, person_box[3] - person_box[1])
    item_center_y = (item_box[1] + item_box[3]) / 2
    relative_y = (item_center_y - person_box[1]) / person_height
    return y1 <= relative_y <= y2

def cooldown_active(last_alerted_at: Dict[Tuple[int, str], float],
                    alert_key: Tuple[int, str], now: float, cooldown_seconds: float) -> bool:
    last_alert = last_alerted_at.get(alert_key)
    return last_alert is not None and now - last_alert < cooldown_seconds

def prune_expired_cooldowns(last_alerted_at: Dict[Tuple[int, str], float],
                            now: float, cooldown_seconds: float) -> Dict[Tuple[int, str], float]:
    return {
        key: alerted_at for key, alerted_at in last_alerted_at.items()
        if now - alerted_at < cooldown_seconds
    }

def violation_class_for(ppe_type: str) -> str:
    return config.NEGATIVE_PPE_CLASSES.get(ppe_type, ppe_type)


@dataclass
class PersonStatus:
    track_id: int
    box: Tuple[int, int, int, int]
    present_ppe: Set[str] = field(default_factory=set)
    missing_ppe: Set[str] = field(default_factory=set)
    unknown_ppe: Set[str] = field(default_factory=set)
    is_violation: bool = False


class ComplianceEngine:
    def __init__(self, camera_id: str, required_ppe: List[str],
                 model_classes: Set[str] = None):
        self.camera_id = camera_id
        self.required_ppe = required_ppe
        self._unsupported_negative_classes = {
            config.NEGATIVE_PPE_CLASSES[ppe_type]
            for ppe_type in required_ppe
            if ppe_type in config.NEGATIVE_PPE_CLASSES
            and model_classes is not None
            and config.NEGATIVE_PPE_CLASSES[ppe_type] not in model_classes
        }
        if self._unsupported_negative_classes and config.PPE_COMPLIANCE_MODE == "explicit_negative":
            logger.warning(
                "[%s] model is missing explicit violation classes %s; "
                "affected PPE status will be unknown until the model/config is corrected",
                camera_id, sorted(self._unsupported_negative_classes),
            )
        self.tracker = BYTETracker(SimpleNamespace(
            track_high_thresh=config.TRACK_HIGH_THRESHOLD,
            track_low_thresh=config.TRACK_LOW_THRESHOLD,
            new_track_thresh=config.TRACK_NEW_THRESHOLD,
            track_buffer=config.TRACK_BUFFER,
            match_thresh=config.TRACK_MATCH_THRESHOLD,
            fuse_score=True,
        ))
        self._observations: Dict[int, Dict[str, deque]] = {}

    @property
    def active_track_ids(self) -> Set[int]:
        return {
            track.track_id
            for track in self.tracker.tracked_stracks + self.tracker.lost_stracks
        }

    def _confirm_observation(self, track_id: int, ppe_type: str, is_missing: bool) -> bool:
        settings = config.TEMPORAL_CONFIRMATION.get(
            ppe_type,
            {"required": config.VIOLATION_CONFIRM_FRAMES,
             "window": max(config.VIOLATION_CONFIRM_FRAMES, 8)},
        )
        track_observations = self._observations.setdefault(track_id, {})
        history = track_observations.get(ppe_type)
        if history is None or history.maxlen != settings["window"]:
            history = deque(history or (), maxlen=settings["window"])
            track_observations[ppe_type] = history
        history.append(is_missing)
        return sum(history) >= settings["required"]

    def evaluate(self, detections: List[Detection], frame_shape) -> List[PersonStatus]:
        persons = [d for d in detections if d.cls_norm == "person"]
        ppe_items = [d for d in detections if d.cls_norm != "person"]
        tracker_input = np.asarray(
            [[p.x1, p.y1, p.x2, p.y2, p.confidence, 0] for p in persons],
            dtype=np.float32,
        ).reshape((-1, 6))
        tracker_boxes = Boxes(tracker_input, tuple(frame_shape[:2]))
        tracked_rows = self.tracker.update(tracker_boxes)
        person_confidence = config.CLASS_CONFIDENCE_THRESHOLDS.get(
            "person", config.CONFIDENCE_THRESHOLD
        )
        tracked = {
            int(row[4]): (tuple(int(value) for value in row[:4]), int(row[7]))
            for row in tracked_rows
            if row[5] >= person_confidence
        }
        active_track_ids = self.active_track_ids
        for track_id in list(self._observations):
            if track_id not in active_track_ids:
                del self._observations[track_id]

        results = []
        for track_id, (box, _) in tracked.items():
            present, missing, unknown = set(), set(), set()
            for req in self.required_ppe:
                neg_cls = config.NEGATIVE_PPE_CLASSES.get(req)
                explicit_negative = any(
                    item.cls_norm == neg_cls
                    and _overlap_fraction_of_b(box, item.box) >= config.MATCH_OVERLAP_THRESHOLD
                    and _in_person_region(box, item.box, req)
                    for item in ppe_items
                ) if neg_cls else False
                has_positive = any(
                    item.cls_norm == req
                    and _overlap_fraction_of_b(box, item.box) >= config.MATCH_OVERLAP_THRESHOLD
                    and _in_person_region(box, item.box, req)
                    for item in ppe_items
                )
                if config.PPE_COMPLIANCE_MODE == "explicit_negative":
                    if neg_cls in self._unsupported_negative_classes:
                        if has_positive:
                            present.add(req)
                        else:
                            unknown.add(req)
                        continue
                    if explicit_negative:
                        missing.add(req)
                    elif has_positive:
                        present.add(req)
                    else:
                        unknown.add(req)
                else:
                    if explicit_negative or not has_positive:
                        missing.add(req)
                    else:
                        present.add(req)

            confirmed_missing = set()
            for ppe_type in self.required_ppe:
                if (ppe_type in missing
                        and self._confirm_observation(track_id, ppe_type, True)):
                    confirmed_missing.add(ppe_type)
                elif ppe_type not in missing:
                    self._confirm_observation(track_id, ppe_type, False)

            results.append(PersonStatus(
                track_id=track_id, box=box, present_ppe=present,
                missing_ppe=confirmed_missing, unknown_ppe=unknown,
                is_violation=len(confirmed_missing) > 0,
            ))
        return results
