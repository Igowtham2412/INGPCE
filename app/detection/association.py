"""
association.py — pure-numpy greedy IoU multi-object tracker.

The live app tracks persons with Ultralytics ByteTrack inside
ComplianceEngine (app/detection/compliance_engine.py). This module provides the
same behaviour with zero heavy dependencies so the demo/evaluation pipeline
and the unit tests can run anywhere (including CI and the Databricks
workspace compute). Box overlap association follows the same principle the
live engine uses for PPE/phone -> person matching (IoU / overlap fraction).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

Box = tuple[int, int, int, int]


def iou(box_a: Sequence[float], box_b: Sequence[float]) -> float:
    """Intersection-over-union between two xyxy boxes."""
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    intersection = iw * ih
    if intersection <= 0:
        return 0.0
    area_a = max(0.0, (ax2 - ax1)) * max(0.0, (ay2 - ay1))
    area_b = max(0.0, (bx2 - bx1)) * max(0.0, (by2 - by1))
    union = area_a + area_b - intersection
    return intersection / union if union > 0 else 0.0


def overlap_fraction_of_b(box_a: Sequence[float], box_b: Sequence[float]) -> float:
    """Fraction of box_b covered by box_a (live engine's PPE/phone rule)."""
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    area_b = max(1.0, (bx2 - bx1) * (by2 - by1))
    return (iw * ih) / area_b


@dataclass
class Track:
    track_id: int
    box: Box
    hits: int = 1
    misses: int = 0


class IoUTracker:
    """Greedy IoU tracker: stable ids across frames, new ids for new people.

    ``update(boxes)`` returns {track_id: box} for tracks matched this frame
    (a track survives up to ``max_misses`` frames without a detection before
    its id is retired, so brief occlusions do not renumber people).
    """

    def __init__(
        self,
        iou_threshold: float = 0.3,
        max_misses: int = 8,
    ) -> None:
        self.iou_threshold = float(iou_threshold)
        self.max_misses = int(max_misses)
        self._tracks: list[Track] = []
        self._next_id = 1

    @property
    def active_tracks(self) -> list[Track]:
        return list(self._tracks)

    def update(self, boxes: Sequence[Box]) -> dict[int, Box]:
        unmatched_detections: list[Box] = list(boxes)
        # Greedy matching: highest-IoU pair first.
        pairs: list[tuple[float, int, int]] = []
        for t_index, track in enumerate(self._tracks):
            for d_index, detection in enumerate(unmatched_detections):
                score = iou(track.box, detection)
                if score >= self.iou_threshold:
                    pairs.append((score, t_index, d_index))
        matched_track_indices: set[int] = set()
        matched_detection_indices: set[int] = set()
        for score, t_index, d_index in sorted(pairs, key=lambda p: -p[0]):
            if t_index in matched_track_indices or d_index in matched_detection_indices:
                continue
            matched_track_indices.add(t_index)
            matched_detection_indices.add(d_index)
            self._tracks[t_index].box = unmatched_detections[d_index]
            self._tracks[t_index].hits += 1
            self._tracks[t_index].misses = 0
        # Unmatched tracks age; retired ones disappear.
        survivors: list[Track] = []
        for t_index, track in enumerate(self._tracks):
            if t_index in matched_track_indices:
                survivors.append(track)
                continue
            track.misses += 1
            if track.misses <= self.max_misses:
                survivors.append(track)
        self._tracks = survivors
        # Unmatched detections become new tracks.
        for d_index, detection in enumerate(unmatched_detections):
            if d_index in matched_detection_indices:
                continue
            self._tracks.append(Track(track_id=self._next_id, box=detection))
            self._next_id += 1
        return {
            track.track_id: track.box
            for track in self._tracks
            if track.misses == 0
        }
