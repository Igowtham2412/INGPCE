"""Estimate vehicle speed from a calibrated pair of image-space gate lines."""
from dataclasses import dataclass, replace
from types import SimpleNamespace
import time

import numpy as np
from ultralytics.engine.results import Boxes
from ultralytics.trackers.byte_tracker import BYTETracker

from .. import config
from .detector import Detection


@dataclass(frozen=True)
class SpeedReading:
    track_id: int
    vehicle_type: str
    box: tuple[int, int, int, int]
    speed_kmh: float
    speed_limit_kmh: float
    speeding: bool
    direction: str
    measured_at: float


@dataclass
class _TrackState:
    previous_anchor: tuple[float, float]
    previous_seen: float
    last_seen: float
    last_gate: str | None = None
    last_gate_at: float | None = None
    latest_reading: SpeedReading | None = None


def _cross(a, b, point) -> float:
    return (b[0] - a[0]) * (point[1] - a[1]) - (b[1] - a[1]) * (point[0] - a[0])


def gates_intersect(
    gate_a: list[list[float]], gate_b: list[list[float]],
) -> bool:
    tolerance = 1e-9

    def orientation(a, b, c):
        value = _cross(a, b, c)
        if value > tolerance:
            return 1
        if value < -tolerance:
            return -1
        return 0

    def on_segment(a, b, point):
        return (
            min(a[0], b[0]) - tolerance <= point[0] <= max(a[0], b[0]) + tolerance
            and min(a[1], b[1]) - tolerance <= point[1] <= max(a[1], b[1]) + tolerance
        )

    a, b = gate_a
    c, d = gate_b
    o1, o2 = orientation(a, b, c), orientation(a, b, d)
    o3, o4 = orientation(c, d, a), orientation(c, d, b)
    if o1 * o2 < 0 and o3 * o4 < 0:
        return True
    return (
        (o1 == 0 and on_segment(a, b, c))
        or (o2 == 0 and on_segment(a, b, d))
        or (o3 == 0 and on_segment(c, d, a))
        or (o4 == 0 and on_segment(c, d, b))
    )


def validate_gate_pair(
    gate_a: list[list[float]], gate_b: list[list[float]],
) -> None:
    if gates_intersect(gate_a, gate_b):
        raise ValueError(
            "Speed gates A and B cross or touch. Place them on separate, "
            "non-intersecting roadway markings."
        )


def _segment_crossing_fraction(
    movement_start: tuple[float, float],
    movement_end: tuple[float, float],
    gate: list[list[float]],
) -> float | None:
    gate_start, gate_end = gate
    start_side = _cross(gate_start, gate_end, movement_start)
    end_side = _cross(gate_start, gate_end, movement_end)
    if start_side == 0 or end_side == 0 or (start_side > 0) == (end_side > 0):
        return None

    fraction = start_side / (start_side - end_side)
    crossing = (
        movement_start[0] + fraction * (movement_end[0] - movement_start[0]),
        movement_start[1] + fraction * (movement_end[1] - movement_start[1]),
    )
    within_gate = (
        min(gate_start[0], gate_end[0]) <= crossing[0] <= max(gate_start[0], gate_end[0])
        and min(gate_start[1], gate_end[1]) <= crossing[1] <= max(gate_start[1], gate_end[1])
    )
    return fraction if within_gate else None


class VehicleSpeedTracker:
    def __init__(
        self,
        gate_a: list[list[float]],
        gate_b: list[list[float]],
        distance_m: float,
        speed_limit_kmh: float,
    ):
        self.gates = {"A": gate_a, "B": gate_b}
        self.distance_m = distance_m
        self.speed_limit_kmh = speed_limit_kmh
        self._tracks: dict[int, _TrackState] = {}
        self._tracker = BYTETracker(SimpleNamespace(
            track_high_thresh=config.TRACK_HIGH_THRESHOLD,
            track_low_thresh=config.TRACK_LOW_THRESHOLD,
            new_track_thresh=config.TRACK_NEW_THRESHOLD,
            track_buffer=config.TRACK_BUFFER,
            match_thresh=config.TRACK_MATCH_THRESHOLD,
            fuse_score=True,
        ))

    def update(
        self,
        detections: list[Detection],
        frame_shape,
        now: float,
    ) -> list[SpeedReading]:
        tracked = [
            detection for detection in detections
            if detection.cls_norm in {
                "bicycle", "vehicle", "car", "motorcycle",
                "bus", "truck", "train",
            }
        ]
        tracker_input = np.asarray(
            [[*d.box, d.confidence, d.class_id] for d in tracked],
            dtype=np.float32,
        ).reshape((-1, 6))
        rows = self._tracker.update(
            Boxes(tracker_input, tuple(frame_shape[:2])),
        )
        class_names = {
            detection.class_id: detection.cls_norm for detection in tracked
        }
        return self.update_tracks(
            [
                (
                    int(row[4]),
                    tuple(int(value) for value in row[:4]),
                    class_names.get(int(row[7]), "vehicle"),
                )
                for row in rows
            ],
            now,
            frame_shape,
        )

    def update_tracks(
        self, tracks, now: float, frame_shape=(100, 100),
    ) -> list[SpeedReading]:
        events = []
        frame_height, frame_width = frame_shape[:2]
        for track_id, box, vehicle_type in tracks:
            anchor = (
                (box[0] + box[2]) / (2 * frame_width),
                box[3] / frame_height,
            )
            state = self._tracks.get(track_id)
            if state is None:
                self._tracks[track_id] = _TrackState(anchor, now, now)
                continue

            crossings = sorted(
                (fraction, name)
                for name, gate in self.gates.items()
                if (fraction := _segment_crossing_fraction(
                    state.previous_anchor, anchor, gate,
                )) is not None
            )
            previous_seen = state.previous_seen
            state.previous_anchor = anchor
            state.previous_seen = now
            state.last_seen = now
            if state.latest_reading is not None:
                state.latest_reading = replace(
                    state.latest_reading, box=box,
                )
            for fraction, crossed_gate in crossings:
                crossed_at = previous_seen + fraction * (now - previous_seen)
                if state.last_gate and crossed_gate != state.last_gate:
                    previous_gate_time = (
                        state.last_gate_at
                        if state.last_gate_at is not None else crossed_at
                    )
                    elapsed = crossed_at - previous_gate_time
                    if 0 < elapsed <= 30:
                        speed_kmh = self.distance_m / elapsed * 3.6
                        reading = SpeedReading(
                            track_id=track_id,
                            vehicle_type=vehicle_type,
                            box=box,
                            speed_kmh=round(speed_kmh, 1),
                            speed_limit_kmh=self.speed_limit_kmh,
                            speeding=speed_kmh > self.speed_limit_kmh,
                            direction=f"{state.last_gate}-to-{crossed_gate}",
                            measured_at=time.time(),
                        )
                        state.latest_reading = reading
                        events.append(reading)
                        state.last_gate = None
                        state.last_gate_at = None
                        continue

                state.last_gate = crossed_gate
                state.last_gate_at = crossed_at

        for track_id, state in list(self._tracks.items()):
            if now - state.last_seen > 10:
                del self._tracks[track_id]
        return events

    def current_readings(self, now: float, max_age: float = 10) -> list[SpeedReading]:
        return [
            state.latest_reading
            for state in self._tracks.values()
            if state.latest_reading is not None
            and now - state.last_seen <= max_age
        ]
