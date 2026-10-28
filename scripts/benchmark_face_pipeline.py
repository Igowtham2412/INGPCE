"""Measure model-free face queue/crop work and optionally local model latency."""
import argparse
import asyncio
import json
import os
import statistics
import subprocess
import sys
import threading
import time
from functools import partial
from pathlib import Path

import cv2
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import config  # noqa: E402
from app.faces.face_dataset import (  # noqa: E402
    FaceModelUnavailable,
    ImageRejected,
    InsightFaceAnalyzer,
)
from app.faces.face_recognition import _crop_person, rank_person_scores  # noqa: E402


class _ResourceSampler:
    def __init__(self):
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._sample, name="face-benchmark-resource-sampler",
            daemon=True,
        )
        self.peak_ram_bytes = None
        self.peak_vram_mb = None

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join()

    def _sample(self):
        try:
            import psutil

            process = psutil.Process()
        except (ImportError, OSError):
            process = None
        while not self._stop.is_set():
            if process is not None:
                try:
                    resident = process.memory_info().rss
                    self.peak_ram_bytes = max(
                        self.peak_ram_bytes or 0, resident,
                    )
                except (OSError, RuntimeError):
                    process = None
            try:
                result = subprocess.run(
                    [
                        "nvidia-smi", "--query-compute-apps=pid,used_memory",
                        "--format=csv,noheader,nounits",
                    ],
                    check=True, capture_output=True, text=True, timeout=2,
                )
                for line in result.stdout.splitlines():
                    columns = [value.strip() for value in line.split(",")]
                    if len(columns) == 2 and columns[0] == str(os.getpid()):
                        used_mb = int(columns[1])
                        self.peak_vram_mb = max(
                            self.peak_vram_mb or 0, used_mb,
                        )
            except (
                FileNotFoundError, subprocess.CalledProcessError,
                subprocess.TimeoutExpired,
            ):
                pass
            self._stop.wait(0.25)


def _measure(function, iterations: int) -> dict:
    samples = []
    for _ in range(iterations):
        started = time.perf_counter()
        function()
        samples.append((time.perf_counter() - started) * 1000)
    ordered = sorted(samples)
    return {
        "iterations": iterations,
        "median_ms": round(statistics.median(samples), 4),
        "p95_ms": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 4),
    }


def _synthetic_match_benchmarks(iterations: int) -> dict:
    rng = np.random.default_rng(4070)
    query = rng.normal(size=512).astype(np.float32)
    gallery = {}
    results = {}
    for count in (100, 500, 2000):
        gallery = {
            person_id: {
                "employee_code": f"SYNTH-{person_id}",
                "full_name": "synthetic",
                "department": None,
                "embeddings": [
                    rng.normal(size=512).astype(np.float32),
                ],
            }
            for person_id in range(count)
        }
        results[str(count)] = _measure(
            partial(rank_person_scores, query, gallery), iterations,
        )
    return results


def _analyze_and_count(
        analyzer: InsightFaceAnalyzer, image: np.ndarray,
        outcomes: dict[str, int],
) -> None:
    try:
        analyzer.analyze(image)
        outcomes["face_found"] += 1
    except ImageRejected:
        outcomes["face_rejected"] += 1


def _model_benchmarks(image: np.ndarray, iterations: int) -> dict:
    report = {}
    for provider in ("cuda", "cpu"):
        resources = _ResourceSampler()
        resources.start()
        try:
            analyzer = InsightFaceAnalyzer(provider_preference=provider)
        except FaceModelUnavailable as exc:
            resources.stop()
            report[provider] = {
                "available": False,
                "reason": str(exc),
                "peak_process_ram_bytes": resources.peak_ram_bytes,
                "peak_process_vram_mb": resources.peak_vram_mb,
            }
            continue
        outcomes = {"face_found": 0, "face_rejected": 0}
        try:
            report[provider] = {
                "available": True,
                "runtime_provider": analyzer.runtime_provider,
                "detection_and_embedding_ms": _measure(
                    partial(_analyze_and_count, analyzer, image, outcomes),
                    iterations,
                ),
                "outcomes": outcomes,
                "note": (
                    "InsightFace exposes detection and embedding together through "
                    "FaceAnalysis.get; these timings cannot separate the two."
                ),
            }
        finally:
            resources.stop()
        report[provider]["peak_process_ram_bytes"] = resources.peak_ram_bytes
        report[provider]["peak_process_vram_mb"] = resources.peak_vram_mb
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark local face pipeline overhead without network access.",
    )
    parser.add_argument("--iterations", type=int, default=30)
    parser.add_argument("--camera-id", default="synthetic_camera")
    parser.add_argument(
        "--violation-types", nargs="+",
        choices=["no_helmet", "no_vest", "phone_detected"],
        default=["no_helmet", "no_vest", "phone_detected"],
        help="Incident types to label the benchmark groups with",
    )
    parser.add_argument(
        "--sample-image", type=Path,
        help="Optional consented in-house image for model-enabled provider timings",
    )
    parser.add_argument(
        "--consented", action="store_true",
        help="Confirm --sample-image is an authorized, consented local image",
    )
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("--iterations must be positive")
    if not args.camera_id.strip():
        parser.error("--camera-id cannot be blank")
    if args.sample_image and not args.consented:
        parser.error("--sample-image requires --consented")

    frame = np.random.default_rng(4070).integers(
        0, 256, size=(720, 1280, 3), dtype=np.uint8,
    )
    person_box = (320, 90, 870, 710)
    queue = asyncio.Queue(maxsize=4)

    def queue_roundtrip():
        queue.put_nowait(1)
        queue.get_nowait()

    def crop_and_quality():
        crop, _bbox = _crop_person(frame, person_box)
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        cv2.Laplacian(gray, cv2.CV_64F).var()

    synthetic_matching = _synthetic_match_benchmarks(args.iterations)
    report = {
        "scope": "synthetic model-free pipeline microbenchmarks",
        "benchmark_camera_id": args.camera_id,
        "benchmark_violation_types": args.violation_types,
        "model_license_accepted": config.FACE_LICENSE_ACCEPTED,
        "model_timing": "blocked until PPE_FACE_LICENSE_ACCEPTED=true",
        "synthetic_queue_roundtrip": _measure(queue_roundtrip, args.iterations),
        "synthetic_person_crop_and_blur_check": _measure(
            crop_and_quality, args.iterations,
        ),
        "synthetic_matching_ms_by_enrollment_count": synthetic_matching,
        "synthetic_matching_ms_by_violation_and_camera": {
            violation_type: {args.camera_id: synthetic_matching}
            for violation_type in args.violation_types
        },
        "grouping_note": (
            "The synthetic matcher performs identical work for every group; "
            "these labels do not imply type-specific recognition performance."
        ),
        "warning": (
            "These microbenchmarks do not measure camera or production incident "
            "latency and do not establish recognition accuracy."
        ),
    }

    if config.FACE_LICENSE_ACCEPTED:
        if args.sample_image:
            image = cv2.imread(str(args.sample_image), cv2.IMREAD_COLOR)
            if image is None:
                parser.error("--sample-image could not be decoded")
        else:
            image = frame
            report["model_input"] = "synthetic image; face detection may reject it"
        report["model_benchmarks"] = _model_benchmarks(image, args.iterations)
        report["model_benchmarks_by_violation_and_camera"] = {
            violation_type: {args.camera_id: report["model_benchmarks"]}
            for violation_type in args.violation_types
        }
        report["model_timing"] = "attempted only with the explicit license flag"
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
