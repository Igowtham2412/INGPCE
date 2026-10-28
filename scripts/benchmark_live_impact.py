"""Compare PPE detector throughput with bounded simulated face-job bursts."""
import argparse
import json
import queue
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import config  # noqa: E402
from app.camera_manager import _encode_annotated_frame  # noqa: E402
from app.detector import PPEDetector  # noqa: E402
from app.face_recognition import (  # noqa: E402
    _crop_person,
    _lower_face_worker_priority,
)


def _run_trial(
        detector: PPEDetector, rate_per_minute: int, duration: float,
        input_fps: float, input_video: Path = None,
        camera_id: str = "SIMULATED_CAM",
        violation_types: list[str] | None = None,
) -> dict:
    violation_types = violation_types or [
        "no_helmet", "no_vest", "phone_detected",
    ]
    capture_queue = queue.Queue(maxsize=1)
    face_queue = queue.Queue(maxsize=4)
    stop_capture = threading.Event()
    stop_face_worker = threading.Event()
    counters = {
        "captured": 0,
        "capture_queue_dropped": 0,
        "detected": 0,
        "face_jobs_enqueued": 0,
        "face_jobs_dropped": 0,
        "face_jobs_processed": 0,
        "face_worker_priority_lowered": False,
    }
    per_type_counts = {
        violation_type: {"enqueued": 0, "dropped": 0, "processed": 0}
        for violation_type in violation_types
    }
    counter_lock = threading.Lock()
    frame = np.random.default_rng(118).integers(
        0, 256, size=(720, 1280, 3), dtype=np.uint8,
    )
    capture = cv2.VideoCapture(str(input_video)) if input_video else None
    if capture is not None and not capture.isOpened():
        raise ValueError("Could not open the requested recorded video")

    def submit_face_jobs():
        started = time.monotonic()
        time_scale = min(1.0, duration / 60)
        interval = max(0.001, 60 / rate_per_minute * time_scale)
        next_job = started
        job_index = 0
        while not stop_capture.is_set():
            now = time.monotonic()
            if now >= started + duration:
                break
            if now >= next_job:
                violation_type = violation_types[job_index % len(violation_types)]
                job_index += 1
                try:
                    face_queue.put_nowait((frame, violation_type))
                    with counter_lock:
                        counters["face_jobs_enqueued"] += 1
                        per_type_counts[violation_type]["enqueued"] += 1
                except queue.Full:
                    with counter_lock:
                        counters["face_jobs_dropped"] += 1
                        per_type_counts[violation_type]["dropped"] += 1
                next_job += interval
                continue
            stop_capture.wait(min(next_job - now, 0.05))

    def capture_loop():
        started = time.monotonic()
        next_capture = started
        while not stop_capture.is_set():
            now = time.monotonic()
            if now >= started + duration:
                break
            if capture is not None:
                ok, source_frame = capture.read()
                if not ok:
                    capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    ok, source_frame = capture.read()
                image = source_frame if ok else frame
            else:
                image = frame
            item = (image, time.monotonic())
            with counter_lock:
                counters["captured"] += 1
            try:
                capture_queue.put_nowait(item)
            except queue.Full:
                try:
                    capture_queue.get_nowait()
                    capture_queue.task_done()
                except queue.Empty:
                    pass
                with counter_lock:
                    counters["capture_queue_dropped"] += 1
                capture_queue.put_nowait(item)

            next_capture += 1 / input_fps
            delay = next_capture - time.monotonic()
            if delay > 0:
                stop_capture.wait(delay)
            else:
                next_capture = time.monotonic()

    def face_worker():
        lowered = _lower_face_worker_priority()
        with counter_lock:
            counters["face_worker_priority_lowered"] = lowered
        while not stop_face_worker.is_set() or not face_queue.empty():
            try:
                image, violation_type = face_queue.get(timeout=0.05)
            except queue.Empty:
                continue
            try:
                crop, _bbox = _crop_person(
                    image, (320, 90, 870, min(710, image.shape[0])),
                )
                if crop is not None:
                    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
                    cv2.Laplacian(gray, cv2.CV_64F).var()
                time.sleep(0.15)
                with counter_lock:
                    counters["face_jobs_processed"] += 1
                    per_type_counts[violation_type]["processed"] += 1
            finally:
                face_queue.task_done()

    capture_thread = threading.Thread(
        target=capture_loop, name="simulated-camera-capture", daemon=True,
    )
    face_thread = threading.Thread(
        target=face_worker, name="ppe-face-worker-simulation", daemon=True,
    )
    face_submitter = threading.Thread(
        target=submit_face_jobs, name="simulated-face-job-producer", daemon=True,
    )
    started = time.monotonic()
    capture_thread.start()
    face_thread.start()
    if rate_per_minute:
        face_submitter.start()
    latencies = []
    tile_ready_latencies = []
    wall_tiles_ready = 0
    while capture_thread.is_alive() or not capture_queue.empty():
        try:
            image, captured_at = capture_queue.get(timeout=0.05)
        except queue.Empty:
            continue
        try:
            detector.infer(image)
            detection_ready = time.monotonic()
            latencies.append((detection_ready - captured_at) * 1000)
            if _encode_annotated_frame(image, [], camera_id) is not None:
                wall_tiles_ready += 1
                tile_ready_latencies.append(
                    (time.monotonic() - captured_at) * 1000,
                )
            with counter_lock:
                counters["detected"] += 1
        finally:
            capture_queue.task_done()
    capture_thread.join()
    stop_face_worker.set()
    face_thread.join()
    if face_submitter.is_alive():
        face_submitter.join()
    if capture is not None:
        capture.release()
    elapsed = max(time.monotonic() - started, 0.001)
    ordered = sorted(latencies)
    return {
        "camera_id": camera_id,
        "configured_face_jobs_per_minute": rate_per_minute,
        "duration_seconds": round(elapsed, 2),
        "capture_fps": round(counters["captured"] / elapsed, 2),
        "detection_fps": round(counters["detected"] / elapsed, 2),
        "capture_to_detection_latency_ms_p50": round(
            float(np.median(latencies)), 2,
        ) if latencies else None,
        "capture_to_detection_latency_ms_p95": round(
            ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 2,
        ) if ordered else None,
        "capture_to_wall_tile_ready_latency_ms_p50": round(
            float(np.median(tile_ready_latencies)), 2,
        ) if tile_ready_latencies else None,
        "capture_to_wall_tile_ready_latency_ms_p95": round(
            sorted(tile_ready_latencies)[
                min(len(tile_ready_latencies) - 1,
                    int(len(tile_ready_latencies) * 0.95))
            ], 2,
        ) if tile_ready_latencies else None,
        "wall_tiles_ready": wall_tiles_ready,
        "capture_frames_dropped": counters["capture_queue_dropped"],
        "face_jobs_enqueued": counters["face_jobs_enqueued"],
        "face_jobs_dropped_at_bounded_queue": counters["face_jobs_dropped"],
        "face_jobs_processed": counters["face_jobs_processed"],
        "face_job_counts_by_violation_type": per_type_counts,
        "face_job_rate_is_accelerated": duration < 60,
        "face_worker_priority_lowered": counters["face_worker_priority_lowered"],
        "arrival_schedule_accelerated": duration < 60,
        "face_queue_capacity": face_queue.maxsize,
        "capture_queue_capacity": capture_queue.maxsize,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Compare detector behavior against synthetic/recorded input while "
            "a separate, low-priority face worker receives bounded simulated jobs."
        ),
    )
    parser.add_argument(
        "--input-video", type=Path,
        help="Optional local recorded video; otherwise a synthetic frame is paced as camera input",
    )
    parser.add_argument("--duration-seconds", type=float, default=60)
    parser.add_argument("--input-fps", type=float, default=5)
    parser.add_argument("--camera-id", default="SIMULATED_CAM")
    parser.add_argument(
        "--violation-types", default="no_helmet,no_vest,phone_detected",
        help="Comma-separated eligible incident types to distribute simulated jobs across",
    )
    parser.add_argument(
        "--face-rates", default="1,5,20",
        help="Simulated face-job arrival rates per minute (default: 1,5,20)",
    )
    args = parser.parse_args()
    if args.duration_seconds <= 0 or args.input_fps <= 0:
        parser.error("duration and input fps must be positive")
    if not args.camera_id.strip():
        parser.error("--camera-id cannot be blank")
    violation_types = [value.strip() for value in args.violation_types.split(",")]
    allowed_violation_types = {"no_helmet", "no_vest", "phone_detected"}
    if not violation_types or any(
        violation_type not in allowed_violation_types
        for violation_type in violation_types
    ):
        parser.error(
            "--violation-types must contain only no_helmet, no_vest, "
            "phone_detected",
        )
    try:
        rates = [int(value) for value in args.face_rates.split(",")]
    except ValueError:
        parser.error("--face-rates must be comma-separated integers")
    if any(rate < 0 for rate in rates):
        parser.error("--face-rates cannot contain negative values")

    try:
        detector = PPEDetector()
        detector.infer(np.zeros((720, 1280, 3), dtype=np.uint8))
        scenarios = [0, *rates]
        results = [
            _run_trial(
                detector, rate, args.duration_seconds, args.input_fps,
                args.input_video, args.camera_id, violation_types,
            )
            for rate in scenarios
        ]
    except (OSError, RuntimeError, ValueError) as exc:
        print(
            f"Live-impact benchmark could not run ({type(exc).__name__}); "
            "check local detector weights and video input.",
            file=sys.stderr,
        )
        return 2

    print(json.dumps({
        "input_type": "recorded local video" if args.input_video else "synthetic paced frames",
        "camera_id": args.camera_id,
        "simulated_violation_types": violation_types,
        "real_camera_test": False,
        "detector_device": detector.device,
        "recognition_model_loaded": False,
        "face_worker": "separate low-priority thread; simulated crop/quality jobs",
        "persistent_face_job_queue_limit": config.FACE_JOB_QUEUE_MAX,
        "results": results,
        "results_by_camera": {args.camera_id: results},
        "warning": (
            "This is a local simulation, not a live-camera measurement. It "
            "measures synthetic/recorded capture, PPE detector throughput, and "
            "annotated-tile encoding (without WebSocket delivery); face inference "
            "is intentionally simulated and does not load a model."
        ),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
