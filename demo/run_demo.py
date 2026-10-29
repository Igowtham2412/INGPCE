#!/usr/bin/env python3
"""
run_demo.py — end-to-end proof of concept: violation -> named employee.

Runs the full pipeline (detection -> tracking -> violation debounce -> face
identification -> evidence + report) against a configurable source:

  --source simulate   fully synthetic scene (default; runs anywhere, no
                      cameras/models/DB needed — clearly labeled SIMULATED)
  --source <file>     video file via the real YOLO + face stack (on-prem box)
  --source webcam     device 0 (on-prem box)
  --source rtsp://..  a single RTSP stream (on-prem box; NEVER hardcode
                      credentials — use the env var PPE_DEMO_RTSP_URL)
  --force-violation  compose frames from enrolled employees so the
                      identification path is exercised even without footage

Outputs: annotated evidence images + results.json + results.csv under
--evidence-dir (default ./evidence), plus a per-incident console report like:
  NO_HELMET -> Anita Sharma (EMP002) score 0.68 cam sim-1 [SIMULATED]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np

from app.detection.association import IoUTracker
from app.detection.debounce import ViolationDebouncer
from app.faces.track_identity import (
    FaceObservation,
    IdentityThresholds,
    TrackIdentityResolver,
    STATUS_FACE_NOT_VISIBLE,
    STATUS_MATCHED,
    STATUS_NEEDS_REVIEW,
    STATUS_UNKNOWN,
    normalize_embedding,
)

SIMULATED_TAG = "[SIMULATED]"
VIOLATION_LABELS = {"no_helmet": "NO_HELMET", "phone_usage": "PHONE"}


# --------------------------------------------------------------------------- #
# Simulation components (numpy + PIL only — run anywhere)                      #
# --------------------------------------------------------------------------- #

class SimulatedFaceRejected(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass
class SimulatedPerson:
    label: str            # identity used for the synthetic embedding
    employee_code: str
    name: str
    department: str
    color: tuple[int, int, int]
    origin: tuple[int, int]
    velocity: tuple[int, int]
    violation: str        # "no_helmet" | "phone_usage" | "none"
    face_profile: str     # "good" | "late" (phone tilt) | "never"
    width: int = 44
    height: int = 150
    frames: int = 90      # number of frames the person is on scene

    def box_at(self, frame_idx: int) -> tuple[int, int, int, int]:
        x = self.origin[0] + self.velocity[0] * frame_idx
        y = self.origin[1] + self.velocity[1] * frame_idx
        return (x, y, x + self.width, y + self.height)

    def face_quality(self, frame_idx: int) -> float:
        """0..1 face usability for this frame (drives rejection/aggregation)."""
        if self.face_profile == "never":
            return 0.0
        if self.face_profile == "late" and frame_idx < self.frames * 0.6:
            return 0.2  # head tilted down at the phone -> rejected
        return 0.85


class SimulatedAnalyzer:
    """FaceAnalyzer-protocol stand-in: deterministic 512-d embeddings.

    The embedding for an identity is a stable pseudo-random unit vector seeded
    by the identity label; per-observation noise shrinks as quality rises, so
    multi-frame aggregation measurably improves the score — the same property
    the real ArcFace pipeline relies on.
    """

    EMBED_DIM = 512

    def __init__(self) -> None:
        self.model_name = "simulated-embedding"
        self.model_version = "1"
        self._bases: dict[str, np.ndarray] = {}

    def _base_for(self, label: str) -> np.ndarray:
        if label not in self._bases:
            seed = int.from_bytes(
                hashlib.sha256(label.encode("utf-8")).digest()[:8], "big",
            )
            rng = np.random.default_rng(seed)
            self._bases[label] = normalize_embedding(rng.normal(size=self.EMBED_DIM))
        return self._bases[label]

    def analyze(self, face_image):
        """face_image is (person, frame_idx) from the simulated scene."""
        person, frame_idx = face_image
        quality = person.face_quality(frame_idx)
        if quality < 0.35:
            raise SimulatedFaceRejected("face_too_blurry_or_tilted")
        noise = 0.25 * (1.0 - quality)
        seed = int.from_bytes(hashlib.sha256(
            f"{person.label}:{frame_idx}".encode("utf-8")).digest()[:8], "big",
        )
        rng = np.random.default_rng(seed)
        embedding = self._base_for(person.label) + noise * rng.normal(
            size=self.EMBED_DIM,
        )
        measurements = {
            "quality_score": quality,
            "bbox": [12, 6, 34, 34],
        }
        return normalize_embedding(embedding), measurements


def simulation_personas() -> list[SimulatedPerson]:
    """The scripted scene: 3 violators + 1 compliant passer-by."""
    return [
        SimulatedPerson(
            label="emp:EMP001", employee_code="EMP001", name="Ravi Kumar",
            department="Electrical", color=(200, 60, 60), origin=(60, 180),
            velocity=(3, 0), violation="none", face_profile="good",
        ),
        SimulatedPerson(
            label="emp:EMP002", employee_code="EMP002", name="Anita Sharma",
            department="Mechanical", color=(60, 120, 200), origin=(200, 200),
            velocity=(-2, 0), violation="no_helmet", face_profile="good",
        ),
        SimulatedPerson(
            label="emp:EMP003", employee_code="EMP003", name="John Mathew",
            department="Civil", color=(70, 160, 70), origin=(560, 190),
            velocity=(-3, 0), violation="phone_usage", face_profile="late",
        ),
        SimulatedPerson(
            label="intruder:UNKNOWN01", employee_code="", name="Unknown Person",
            department="", color=(160, 90, 170), origin=(420, 210),
            velocity=(1, 0), violation="no_helmet", face_profile="good",
        ),
    ]


def build_simulation_gallery(seed: dict) -> dict[int, dict]:
    """Gallery from the seed file (enrolled employees only)."""
    analyzer = SimulatedAnalyzer()
    gallery: dict[int, dict] = {}
    for index, employee in enumerate(seed.get("employees", []), start=1):
        label = f"emp:{employee['employee_code']}"
        gallery[index] = {
            "employee_code": employee["employee_code"],
            "full_name": employee["name"],
            "department": employee.get("department", ""),
            "embeddings": [analyzer._base_for(label)],
        }
    return gallery


def draw_simulation_frame(
        frame_idx: int, persons: list[SimulatedPerson],
        boxes: dict[int, tuple[int, int, int, int]],
        person_by_track: dict[int, SimulatedPerson],
        decisions: dict[int, str], size: tuple[int, int] = (960, 480),
):
    """Render an annotated RGB ndarray; PIL if available, else None."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    image = Image.new("RGB", size, (24, 26, 30))
    draw = ImageDraw.Draw(image)
    draw.text((8, 6), f"CAM sim-1  frame {frame_idx}  {SIMULATED_TAG}", fill=(240, 240, 240))
    for track_id, box in boxes.items():
        person = person_by_track.get(track_id)
        if person is None:
            continue
        color = person.color
        draw.rectangle(box, outline=color, width=2)
        # head region (face)
        head = (box[0] + 10, box[1] + 6, box[0] + 34, box[1] + 34)
        draw.rectangle(head, outline=(255, 220, 120), width=1)
        label = decisions.get(track_id, VIOLATION_LABELS.get(person.violation, ""))
        if person.violation != "none":
            draw.text((box[0], max(0, box[1] - 12)), label[:40], fill=color)
    return np.asarray(image)


def run_simulation(
        seed: dict,
        evidence_dir: Path,
        frames: int,
        thresholds: IdentityThresholds,
        camera_id: str = "sim-1",
) -> list[dict]:
    print("=== [SIMULATED] SIMULATION MODE — synthetic identities; no real ===")
    print("=== cameras, model weights, or biometric data are used.       ===")
    persons = simulation_personas()
    for person in persons:
        person.frames = frames
    gallery = build_simulation_gallery(seed)
    analyzer = SimulatedAnalyzer()
    tracker = IoUTracker(iou_threshold=0.2, max_misses=3)
    debouncer = ViolationDebouncer(confirm_frames=3, window=8, cooldown_seconds=9999)
    resolver = TrackIdentityResolver(
        thresholds, gallery, best_n=5, min_faces=3,
        aggregation_mode="mean", max_wait_seconds=frames,
        clock=lambda: 0.0,
    )
    records: list[dict] = []
    decided_labels: dict[int, str] = {}
    # Confirmed-but-unidentified violations wait for a better face frame
    # within the track lifetime (phone-tilt case) before being recorded.
    pending: dict[tuple[int, str], SimulatedPerson] = {}

    def record_incident(track_id: int, violation: str, decision) -> None:
        decision_status = (
            decision.status
            if decision is not None and decision.observation_count
            else STATUS_FACE_NOT_VISIBLE
        )
        employee = None
        if decision is not None and decision.person_id in gallery:
            employee = gallery[decision.person_id]
        decided_labels[track_id] = (
            f"{VIOLATION_LABELS[violation]} | "
            + (f"{employee['full_name']} ({decision.score:.2f})"
               if decision and decision.status == STATUS_MATCHED and employee
               else decision_status)
            + f" {SIMULATED_TAG}"
        )
        records.append({
            "camera_id": camera_id,
            "track_id": track_id,
            "violation_type": violation,
            "detected_at": datetime.now(timezone.utc).isoformat(),
            "frame_idx": frame_idx,
            "status": decision_status,
            "employee_code": employee["employee_code"] if employee else None,
            "employee_name": employee["full_name"] if employee else None,
            "department": employee["department"] if employee else None,
            "match_score": round(decision.score, 4) if decision else None,
            "second_best_margin": round(decision.margin, 4) if decision else None,
            "faces_aggregated": decision.observation_count if decision else 0,
            "simulated": True,
        })
        print(
            f"{VIOLATION_LABELS[violation]} -> "
            + (f"{records[-1]['employee_name']} ({records[-1]['employee_code']}) "
               if records[-1]["employee_code"] else records[-1]["status"])
            + f" score {records[-1]['match_score']} cam {camera_id} "
            + SIMULATED_TAG
        )

    for frame_idx in range(frames):
        visible = [p for p in persons if p.box_at(frame_idx)[0] < 900]
        boxes = tracker.update([p.box_at(frame_idx) for p in visible])
        person_by_track: dict[int, SimulatedPerson] = {}
        for track_id, box in boxes.items():
            for person in visible:
                pbox = person.box_at(frame_idx)
                if abs(pbox[0] - box[0]) <= 2 and abs(pbox[1] - box[1]) <= 2:
                    person_by_track[track_id] = person
        for track_id, person in person_by_track.items():
            if person.violation == "none":
                continue
            # Face pipeline runs ONLY on violating tracks (spec requirement).
            try:
                embedding, measurements = analyzer.analyze((person, frame_idx))
                resolver.add_observation(
                    camera_id, track_id,
                    FaceObservation(
                        embedding=embedding,
                        quality=measurements["quality_score"],
                        source="tracked_person_buffer",
                        measurements=measurements,
                    ),
                )
            except SimulatedFaceRejected:
                pass  # keep waiting for a better frame within the track
            if debouncer.observe(camera_id, track_id, person.violation, True):
                if debouncer.should_log(camera_id, track_id, person.violation):
                    pending[(track_id, person.violation)] = person
        # Settle pending incidents once identity is decided (or track lost).
        for (track_id, violation), person in list(pending.items()):
            decision = resolver.try_decide(camera_id, track_id)
            still_tracked = person_by_track.get(track_id) is person
            if decision is not None:
                record_incident(track_id, violation, decision)
                del pending[(track_id, violation)]
            elif not still_tracked:
                # Track ended without a usable face -> finalize as-is.
                decision = resolver.finalize(camera_id, track_id)
                record_incident(track_id, violation, decision)
                del pending[(track_id, violation)]
        # Save one annotated evidence frame per newly recorded incident.
        if any(r["frame_idx"] == frame_idx for r in records):
            frame = draw_simulation_frame(
                frame_idx, visible, boxes, person_by_track, decided_labels,
            )
            if frame is not None:
                from PIL import Image
                incident_ids = [
                    r["track_id"] for r in records if r["frame_idx"] == frame_idx
                ]
                for track_id in incident_ids:
                    path = evidence_dir / (
                        f"sim_{camera_id}_t{track_id}_f{frame_idx}.png"
                    )
                    evidence_dir.mkdir(parents=True, exist_ok=True)
                    Image.fromarray(frame).save(path)
                    for record in records:
                        if record["frame_idx"] == frame_idx:
                            record.setdefault("evidence_frame_path", str(path))
    return records


# --------------------------------------------------------------------------- #
# Real mode (video file / webcam / RTSP — requires the on-prem stack)         #
# --------------------------------------------------------------------------- #

def run_real(source: str, seed: dict, evidence_dir: Path, frames: int,
             thresholds: IdentityThresholds, camera_id: str,
             force_violation: bool) -> list[dict]:
    try:
        import cv2  # noqa: F401
        from app.detection.detector import PPEDetector
    except ImportError as exc:
        raise SystemExit(
            "Real mode needs the full stack (opencv/ultralytics/torch) on the "
            f"on-prem box: {exc}\nUse --source simulate for the dependency-free "
            "proof of concept.",
        )
    from app.faces.face_dataset import create_face_analyzer

    detector = PPEDetector()
    analyzer = create_face_analyzer()
    gallery: dict[int, dict] = {}
    for index, employee in enumerate(seed.get("employees", []), start=1):
        embeddings = employee.get("embeddings") or []
        if embeddings:
            gallery[index] = {
                "employee_code": employee["employee_code"],
                "full_name": employee["name"],
                "department": employee.get("department", ""),
                "embeddings": [np.asarray(v, dtype=np.float32) for v in embeddings],
            }
    if not gallery:
        print("WARNING: seed has no embeddings; everyone will be UNKNOWN. "
              "Run demo/seed_employees.py on this box first.")

    capture = cv2.VideoCapture(source if source != "webcam" else 0)
    if not capture.isOpened():
        raise SystemExit(f"Cannot open source: {source}")
    tracker = IoUTracker()
    debouncer = ViolationDebouncer(confirm_frames=3, window=8, cooldown_seconds=60)
    resolver = TrackIdentityResolver(
        thresholds, gallery, best_n=5, min_faces=2, aggregation_mode="mean",
    )
    records: list[dict] = []
    frame_idx = 0
    while frame_idx < frames or frames == 0:
        ok, frame = capture.read()
        if not ok:
            if source.startswith("rtsp"):
                print("[demo] stream dropped; reconnecting...")
                time.sleep(2)
                capture.release()
                capture = cv2.VideoCapture(source)
                continue
            break
        try:
            detections = detector.infer(frame)
        except Exception as exc:  # resilience: never crash the demo loop
            print(f"[demo] inference error ignored: {type(exc).__name__}")
            continue
        persons = [d for d in detections if d.cls_norm == "person"]
        helmets = [d for d in detections if d.cls_norm == "helmet"]
        phones = [d for d in detections if d.cls_norm == "phone"]
        boxes = tracker.update([d.box for d in persons])
        for track_id, box in boxes.items():
            has_helmet = any(
                max(0, min(box[2], h.x2) - max(box[0], h.x1))
                * max(0, min(box[3], h.y2) - max(box[1], h.y1)) > 0
                for h in helmets
            )
            violations: list[str] = []
            if not has_helmet or force_violation:
                violations.append("no_helmet")
            if any(
                p.x1 >= box[0] - 40 and p.x2 <= box[2] + 40
                and p.y1 >= box[1] - 20 and p.y2 <= box[3] + 20
                for p in phones
            ):
                violations.append("phone_usage")
            for violation in violations:
                # Face pipeline only on violating tracks.
                try:
                    crop = frame[
                        max(0, box[1]):box[3], max(0, box[0]):box[2]
                    ]
                    if crop.size:
                        embedding, measurements = analyzer.analyze(crop)
                        resolver.add_observation(
                            camera_id, track_id,
                            FaceObservation(
                                embedding=embedding,
                                quality=measurements["quality_score"],
                                measurements=measurements,
                            ),
                        )
                except Exception:
                    pass
                if debouncer.observe(camera_id, track_id, violation, True):
                    if not debouncer.should_log(camera_id, track_id, violation):
                        continue
                    decision = resolver.try_decide(camera_id, track_id)
                    status = decision.status if decision else STATUS_FACE_NOT_VISIBLE
                    employee = gallery.get(decision.person_id) if decision else None
                    path = evidence_dir / (
                        f"{camera_id}_t{track_id}_{violation}_f{frame_idx}.jpg"
                    )
                    evidence_dir.mkdir(parents=True, exist_ok=True)
                    cv2.imwrite(str(path), frame)
                    records.append({
                        "camera_id": camera_id,
                        "track_id": track_id,
                        "violation_type": violation,
                        "detected_at": datetime.now(timezone.utc).isoformat(),
                        "frame_idx": frame_idx,
                        "status": status,
                        "employee_code": employee["employee_code"] if employee else None,
                        "employee_name": employee["full_name"] if employee else None,
                        "match_score": round(decision.score, 4) if decision else None,
                        "evidence_frame_path": str(path),
                        "simulated": False,
                    })
                    print(
                        f"{VIOLATION_LABELS[violation]} -> "
                        + (f"{employee['full_name']} ({employee['employee_code']})"
                           if employee else status)
                        + f" score {records[-1]['match_score']} cam {camera_id}"
                    )
        frame_idx += 1
    capture.release()
    return records


# --------------------------------------------------------------------------- #

def write_outputs(records: list[dict], evidence_dir: Path) -> None:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / "results.json").write_text(json.dumps(records, indent=2))
    if records:
        with open(evidence_dir / "results.csv", "w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(records[0].keys()))
            writer.writeheader()
            writer.writerows(records)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="simulate",
                        help="simulate | webcam | rtsp://... | /path/video.mp4")
    parser.add_argument("--camera-id", default="demo-cam-1")
    parser.add_argument("--frames", type=int, default=90)
    parser.add_argument("--force-violation", action="store_true",
                        help="treat every tracked person as violating (test mode)")
    parser.add_argument("--evidence-dir", default="./evidence")
    parser.add_argument("--seed", default=str(Path(__file__).parent / "seed_employees.json"))
    parser.add_argument("--match-threshold", type=float, default=0.60)
    parser.add_argument("--suggest-threshold", type=float, default=0.45)
    parser.add_argument("--min-margin", type=float, default=0.05)
    args = parser.parse_args()

    seed_path = Path(args.seed)
    seed = json.loads(seed_path.read_text()) if seed_path.exists() else {
        "employees": [
            {"employee_code": "EMP001", "name": "Ravi Kumar", "department": "Electrical"},
            {"employee_code": "EMP002", "name": "Anita Sharma", "department": "Mechanical"},
            {"employee_code": "EMP003", "name": "John Mathew", "department": "Civil"},
            {"employee_code": "EMP004", "name": "Priya Nair", "department": "Electrical"},
            {"employee_code": "EMP005", "name": "Imran Khan", "department": "Operations"},
        ]
    }
    if seed_path.exists():
        print(f"[demo] loaded seed {seed_path} "
              f"({len(seed.get('employees', []))} employees)")
    else:
        print("[demo] seed file missing; using built-in demo identities")

    thresholds = IdentityThresholds(
        match_threshold=args.match_threshold,
        suggest_threshold=args.suggest_threshold,
        min_margin=args.min_margin,
    )
    evidence_dir = Path(args.evidence_dir)
    if args.source == "simulate":
        records = run_simulation(seed, evidence_dir, args.frames, thresholds)
    else:
        source = os.environ.get("PPE_DEMO_RTSP_URL", args.source) \
            if args.source.startswith("rtsp") else args.source
        records = run_real(
            source, seed, evidence_dir, args.frames, thresholds,
            args.camera_id, args.force_violation,
        )
    write_outputs(records, evidence_dir)
    matched = sum(1 for r in records if r["status"] == STATUS_MATCHED)
    print(
        f"\n[demo] {len(records)} violation(s) recorded, "
        f"{matched} identified -> {evidence_dir.resolve()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
