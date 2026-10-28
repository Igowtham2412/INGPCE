"""Asynchronous, post-confirmation face matching for eligible PPE incidents."""
import asyncio
import ctypes
import json
import logging
import os
import time
from collections import Counter, OrderedDict, deque
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

import cv2
import numpy as np

from .. import config, db
from .face_dataset import (
    FaceAnalyzer,
    ImageRejected,
    create_face_analyzer,
    _encryption_key,
    decrypt_embedding,
    encrypt_face_crop,
)

logger = logging.getLogger("ppe.face_recognition")
_MAX_TRACK_BUFFERS = 64
_MAX_PENDING_CROPS = 64
_CAPTURE_QUEUE_SIZE = 4
_JPEG_QUALITY = 78


def violation_requires_face_recognition(violation_type: str) -> bool:
    return violation_type in config.FACE_VIOLATION_TYPES


def rank_person_scores(
        query: np.ndarray, gallery: dict[int, dict[str, Any]],
        strategy: str = "max", top_k: int = 3) -> list[dict[str, Any]]:
    if strategy not in {"max", "top_k_mean"}:
        raise ValueError("Invalid face match strategy")
    if top_k < 1:
        raise ValueError("Face match top-k must be positive")
    vectors: list[np.ndarray] = []
    owners: list[int] = []
    normalized_query = np.asarray(query, dtype=np.float32).reshape(-1)
    if normalized_query.size == 0:
        return []
    for person_id, person in gallery.items():
        for vector in person["embeddings"]:
            normalized = np.asarray(vector, dtype=np.float32).reshape(-1)
            norm = float(np.linalg.norm(normalized))
            if (
                normalized.size == normalized_query.size
                and norm > 1e-8 and np.isfinite(normalized).all()
            ):
                vectors.append(normalized / norm)
                owners.append(person_id)
    if not vectors:
        return []
    matrix = np.stack(vectors)
    query_norm = float(np.linalg.norm(normalized_query))
    if query_norm <= 1e-8 or not np.isfinite(normalized_query).all():
        return []
    normalized_query = normalized_query / query_norm
    try:
        import faiss
    except ImportError:
        scores = matrix @ normalized_query
    else:
        index = faiss.IndexFlatIP(matrix.shape[1])
        index.add(np.ascontiguousarray(matrix))
        scores, _ = index.search(
            np.ascontiguousarray(normalized_query.reshape(1, -1), dtype=np.float32),
            len(vectors),
        )
        scores = scores[0]
    grouped: dict[int, list[float]] = {}
    for person_id, score in zip(owners, scores):
        grouped.setdefault(person_id, []).append(float(score))
    ranked = []
    for person_id, person in gallery.items():
        person_scores = sorted(grouped.get(person_id, ()), reverse=True)
        if not person_scores:
            continue
        score = (
            max(person_scores) if strategy == "max"
            else float(np.mean(person_scores[:top_k]))
        )
        ranked.append({
            "person_id": person_id,
            "employee_code": person["employee_code"],
            "full_name": person["full_name"],
            "department": person["department"],
            "similarity": score,
        })
    return sorted(ranked, key=lambda item: (-item["similarity"], item["person_id"]))


def decide_face_match(
        ranked: Sequence[dict[str, Any]], match_threshold: float,
        suggest_threshold: float, min_margin: float,
) -> tuple[str, int | None, float | None, float | None, list[dict[str, Any]]]:
    if not ranked:
        return "UNKNOWN", None, None, None, []
    best = ranked[0]
    margin = (
        float(best["similarity"] - ranked[1]["similarity"])
        if len(ranked) > 1 else 0.0
    )
    score = float(best["similarity"])
    if score >= match_threshold and (len(ranked) == 1 or margin > min_margin):
        status = "MATCHED"
    elif score >= suggest_threshold:
        status = "NEEDS_REVIEW"
    else:
        return "UNKNOWN", None, score, margin, [
            dict(candidate) for candidate in ranked[:5]
        ]
    return (
        status, int(best["person_id"]), score, margin,
        [dict(candidate) for candidate in ranked[:5]],
    )


def _crop_person(
        frame: np.ndarray, box: Sequence[int],
) -> tuple[np.ndarray | None, list[int] | None]:
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = [int(value) for value in box]
    if x2 <= x1 or y2 <= y1:
        return None, None
    pad_x = max(2, int((x2 - x1) * 0.04))
    pad_y = max(2, int((y2 - y1) * 0.03))
    x1, y1 = max(0, x1 - pad_x), max(0, y1 - pad_y)
    x2, y2 = min(width, x2 + pad_x), min(height, y2 + pad_y)
    crop = frame[y1:y2, x1:x2]
    if crop.size == 0:
        return None, None
    return crop, [x1, y1, x2, y2]


def _encode_person_crops(
        frame: np.ndarray, tracks: Sequence[tuple[int, Sequence[int]]],
) -> list[tuple[int, bytes, list[int]]]:
    encoded = []
    for track_id, box in tracks:
        crop, origin = _crop_person(frame, box)
        if crop is None or origin is None:
            continue
        ok, buffer = cv2.imencode(
            ".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, _JPEG_QUALITY],
        )
        if ok:
            encoded.append((track_id, buffer.tobytes(), origin))
    return encoded


def _read_person_crop(row: dict[str, Any]) -> tuple[np.ndarray | None, list[int] | None]:
    image_path = row.get("clean_snapshot_path") or row.get("snapshot_path")
    if not image_path:
        return None, None
    frame = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if frame is None or frame.size == 0:
        return None, None
    metadata = row.get("detection_metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            return None, None
    if not isinstance(metadata, dict):
        return None, None
    box = metadata.get("person_box")
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None, None
    return _crop_person(frame, box)


def _best_face_from_crops(
        analyzer: FaceAnalyzer,
        crops: Sequence[tuple[np.ndarray, str, list[int] | None]],
        minimum_quality: float = 0.0,
) -> tuple[dict[str, Any] | None, dict[str, int]]:
    best = None
    rejected: Counter[str] = Counter()
    for image, source, origin in crops:
        try:
            embedding, measurements = analyzer.analyze(image)
        except ImageRejected as exc:
            rejected[exc.reason] += 1
            continue
        quality = float(measurements["quality_score"])
        if quality < minimum_quality:
            rejected["below_violation_quality_minimum"] += 1
            continue
        if best is None or quality > best["quality_score"]:
            face_box = measurements.get("bbox")
            absolute_box = None
            if (
                origin is not None and isinstance(face_box, list)
                and len(face_box) == 4
            ):
                absolute_box = [
                    int(face_box[0] + origin[0]),
                    int(face_box[1] + origin[1]),
                    int(face_box[2] + origin[0]),
                    int(face_box[3] + origin[1]),
                ]
            face_crop = None
            if isinstance(face_box, (list, tuple)) and len(face_box) == 4:
                height, width = image.shape[:2]
                x1, y1, x2, y2 = [int(value) for value in face_box]
                pad_x = max(4, int((x2 - x1) * 0.15))
                pad_y = max(4, int((y2 - y1) * 0.15))
                x1, x2 = max(0, x1 - pad_x), min(width, x2 + pad_x)
                y1, y2 = max(0, y1 - pad_y), min(height, y2 + pad_y)
                if x2 > x1 and y2 > y1:
                    face_crop = image[y1:y2, x1:x2].copy()
            best = {
                "embedding": embedding,
                "quality_score": quality,
                "measurements": measurements,
                "source": source,
                "absolute_bbox": absolute_box,
                "face_crop": face_crop,
            }
    return best, dict(rejected)


def _encrypt_recognized_face_crop(
        best_face: dict[str, Any], key: bytes) -> bytes | None:
    face_crop = best_face.get("face_crop")
    if face_crop is None:
        return None
    ok, encoded = cv2.imencode(
        ".jpg", face_crop, [cv2.IMWRITE_JPEG_QUALITY, _JPEG_QUALITY],
    )
    if not ok:
        return None
    return encrypt_face_crop(encoded.tobytes(), key)


def _lower_face_worker_priority() -> bool:
    if os.name != "nt":
        return False
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentThread.restype = wintypes.HANDLE
        kernel32.SetThreadPriority.argtypes = [wintypes.HANDLE, ctypes.c_int]
        lowered = bool(
            kernel32.SetThreadPriority(kernel32.GetCurrentThread(), -1)
        )
    except (AttributeError, OSError):
        lowered = False
    if not lowered:
        logger.warning("Could not lower face-worker thread priority")
    return lowered


class FaceRecognitionWorker:
    def __init__(self, analyzer_factory=create_face_analyzer):
        self._analyzer_factory = analyzer_factory
        self._analyzer: FaceAnalyzer | None = None
        self._capture_queue: asyncio.Queue = asyncio.Queue(
            maxsize=_CAPTURE_QUEUE_SIZE,
        )
        self._frame_buffers: OrderedDict[
            tuple[str, int], deque[tuple[float, bytes, list[int]]]
        ] = OrderedDict()
        self._pending_crops: dict[
            int, list[tuple[bytes, list[int] | None]]
        ] = {}
        self._capture_task: asyncio.Task | None = None
        self._recognition_task: asyncio.Task | None = None
        self._enqueue_tasks = set()
        self._blocking_executor: ThreadPoolExecutor | None = None
        self._running = False

    async def _run_blocking(self, function, *args):
        if self._blocking_executor is None:
            return await asyncio.to_thread(function, *args)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._blocking_executor, partial(function, *args),
        )

    async def start(self) -> None:
        if self._running:
            return
        self._blocking_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="ppe-face-worker",
            initializer=_lower_face_worker_priority,
        )
        self._running = True
        self._capture_task = asyncio.create_task(self._capture_loop())
        self._recognition_task = asyncio.create_task(self._recognition_loop())

    async def stop(self) -> None:
        self._running = False
        for task in (self._capture_task, self._recognition_task):
            if task:
                task.cancel()
        for task in (self._capture_task, self._recognition_task):
            if task:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        if self._enqueue_tasks:
            await asyncio.gather(*self._enqueue_tasks, return_exceptions=True)
        self._capture_task = None
        self._recognition_task = None
        self._pending_crops.clear()
        self._frame_buffers.clear()
        if self._blocking_executor is not None:
            self._blocking_executor.shutdown(wait=False, cancel_futures=True)
            self._blocking_executor = None

    def observe(
            self, camera_id: str, frame: np.ndarray, statuses: Sequence[Any],
    ) -> None:
        if not self._running or not statuses:
            return
        try:
            tracks = tuple(
                (int(status.track_id), tuple(status.box))
                for status in statuses
            )
            self._capture_queue.put_nowait((camera_id, frame, tracks))
        except asyncio.QueueFull:
            return
        except Exception as exc:
            logger.warning("Could not queue tracked-person frame (%s)", type(exc).__name__)

    def enqueue_violation(
            self, incident_id: int, camera_id: str, track_id: int,
            violation_type: str,
    ) -> None:
        if not self._running or not config.FACE_ENABLED:
            return
        if not violation_requires_face_recognition(violation_type):
            return
        if len(self._enqueue_tasks) >= _MAX_PENDING_CROPS:
            logger.warning(
                "Face-recognition enqueue capacity reached; incident %s will be recovered by the queue sweep",
                incident_id,
            )
            return
        key = (camera_id, track_id)
        self._prune_buffers(time.monotonic())
        if len(self._pending_crops) < _MAX_PENDING_CROPS:
            self._pending_crops[incident_id] = [
                (image, origin)
                for _timestamp, image, origin in self._frame_buffers.get(key, ())
            ]
        task = asyncio.create_task(self._enqueue_job(incident_id))
        self._enqueue_tasks.add(task)
        task.add_done_callback(self._enqueue_tasks.discard)

    def enqueue_confirmed_incident(
            self, incident_id: int, camera_id: str, track_id: int,
            violation_type: str,
    ) -> None:
        self.enqueue_violation(
            incident_id, camera_id, track_id, violation_type,
        )

    async def _enqueue_job(self, incident_id: int) -> None:
        try:
            queued = await db.enqueue_face_recognition_job(incident_id)
            if not queued:
                self._pending_crops.pop(incident_id, None)
        except Exception as exc:
            self._pending_crops.pop(incident_id, None)
            logger.error(
                "Could not queue face-recognition job (%s)",
                type(exc).__name__,
            )

    async def _capture_loop(self) -> None:
        while True:
            try:
                camera_id, frame, tracks = await asyncio.wait_for(
                    self._capture_queue.get(),
                    timeout=min(config.FACE_BUFFER_TTL_SECONDS, 1.0),
                )
            except TimeoutError:
                self._prune_buffers(time.monotonic())
                continue
            try:
                encoded = await self._run_blocking(
                    _encode_person_crops, frame, tracks,
                )
            except Exception as exc:
                logger.warning("Could not buffer tracked-person crop (%s)", type(exc).__name__)
                continue
            now = time.monotonic()
            self._prune_buffers(now)
            for track_id, image, origin in encoded:
                key = (camera_id, track_id)
                images = self._frame_buffers.get(key)
                if images is None:
                    images = deque(maxlen=config.FACE_BUFFER_FRAMES)
                    self._frame_buffers[key] = images
                images.append((now, image, origin))
                self._frame_buffers.move_to_end(key)
            while len(self._frame_buffers) > _MAX_TRACK_BUFFERS:
                self._frame_buffers.popitem(last=False)

    def _prune_buffers(self, now: float) -> None:
        expiry = now - config.FACE_BUFFER_TTL_SECONDS
        for key in list(self._frame_buffers):
            images = self._frame_buffers[key]
            while images and images[0][0] < expiry:
                images.popleft()
            if not images:
                del self._frame_buffers[key]

    async def _recognition_loop(self) -> None:
        next_retention_check = time.monotonic()
        next_reconciliation = time.monotonic()
        while True:
            if (
                config.FACE_MATCH_RETENTION_DAYS > 0
                and time.monotonic() >= next_retention_check
            ):
                await self._expire_old_matches()
                next_retention_check = (
                    time.monotonic() + 24 * 60 * 60
                )
            if time.monotonic() >= next_reconciliation:
                try:
                    await db.enqueue_missing_face_recognition_jobs()
                except Exception as exc:
                    logger.error(
                        "Could not reconcile unqueued face-recognition jobs (%s)",
                        type(exc).__name__,
                    )
                next_reconciliation = time.monotonic() + 30
            try:
                job = await db.claim_face_recognition_job()
            except Exception as exc:
                logger.error(
                    "Could not claim face-recognition job (%s)",
                    type(exc).__name__,
                )
                await asyncio.sleep(config.FACE_JOB_POLL_SECONDS)
                continue
            if job is None:
                await asyncio.sleep(config.FACE_JOB_POLL_SECONDS)
                continue
            await self._process_job(job)

    async def _expire_old_matches(self) -> None:
        cutoff = datetime.now(UTC) - timedelta(
            days=config.FACE_MATCH_RETENTION_DAYS,
        )
        try:
            deleted = await db.delete_expired_face_match_records(cutoff)
            if deleted:
                await db.write_audit_log(
                    "face_match_records_expired",
                    target_type="incident_face_matches",
                    details={"records_deleted": deleted},
                )
        except Exception as exc:
            logger.error(
                "Could not apply face-match retention (%s)",
                type(exc).__name__,
            )

    async def _process_job(self, job: dict[str, Any]) -> None:
        incident_id = int(job["incident_id"])
        started = time.perf_counter()
        try:
            if not violation_requires_face_recognition(job["violation_type"]):
                raise ValueError("ineligible_violation")
            analyzer = self._analyzer
            if analyzer is None:
                analyzer = await self._run_blocking(self._analyzer_factory)
                self._analyzer = analyzer
            settings = await db.get_face_match_settings()
            thresholds_used = {
                "high_threshold": float(settings["high_threshold"]),
                "low_threshold": float(settings["low_threshold"]),
                "minimum_margin": float(settings["minimum_margin"]),
                "quality_min_by_violation": settings[
                    "quality_min_by_violation"
                ],
            }

            crops: list[tuple[np.ndarray, str, list[int] | None]] = []
            buffered_crops = self._pending_crops.pop(incident_id, [])
            self._prune_buffers(time.monotonic())
            buffered_crops.extend(
                (encoded, origin)
                for _timestamp, encoded, origin in self._frame_buffers.get(
                    (job["camera_id"], int(job["track_id"])), (),
                )
            )
            seen_crops = set()
            for encoded, origin in buffered_crops:
                if encoded in seen_crops:
                    continue
                seen_crops.add(encoded)
                image = cv2.imdecode(
                    np.frombuffer(encoded, dtype=np.uint8), cv2.IMREAD_COLOR,
                )
                if image is not None and image.size:
                    crops.append((image, "tracked_person_buffer", origin))
            snapshot_crop, snapshot_origin = await self._run_blocking(
                _read_person_crop, job,
            )
            if snapshot_crop is not None:
                crops.append((snapshot_crop, "incident_evidence", snapshot_origin))

            if not crops:
                raise FileNotFoundError("incident evidence image unavailable")
            best_face, rejected_reasons = await self._run_blocking(
                _best_face_from_crops, analyzer, crops,
                thresholds_used["quality_min_by_violation"].get(
                    job["violation_type"], 0.0,
                ),
            )
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            if best_face is None:
                status = (
                    "FACE_NOT_VISIBLE"
                )
                await db.complete_face_recognition_job(
                    incident_id, status, None, None, None,
                    {
                        "rejected_reasons": rejected_reasons,
                        "processing_ms": elapsed_ms,
                    },
                    None, [], analyzer.model_name, analyzer.model_version,
                    source=job.get("source", "live"),
                    thresholds=thresholds_used,
                )
                await self._audit_result(incident_id, status)
                return

            key = _encryption_key()
            gallery = await self._load_gallery(
                key, analyzer.model_name, analyzer.model_version,
            )
            ranked = rank_person_scores(
                best_face["embedding"], gallery,
                config.FACE_MATCH_STRATEGY, config.FACE_MATCH_TOP_K,
            )
            status, matched_person_id, score, margin, candidates = decide_face_match(
                ranked, thresholds_used["high_threshold"],
                thresholds_used["low_threshold"],
                thresholds_used["minimum_margin"],
            )
            face_quality = {
                **best_face["measurements"],
                "quality_score": best_face["quality_score"],
                "crop_source": best_face["source"],
                "runtime_provider": getattr(analyzer, "runtime_provider", "unknown"),
                "processing_ms": elapsed_ms,
            }
            stored_face_crop = None
            if best_face is not None:
                stored_face_crop = await self._run_blocking(
                    _encrypt_recognized_face_crop, best_face, key,
                )
            await db.complete_face_recognition_job(
                incident_id, status, matched_person_id, score, margin,
                face_quality, best_face["absolute_bbox"], candidates,
                analyzer.model_name, analyzer.model_version,
                stored_face_crop,
                source=job.get("source", "live"),
                thresholds=thresholds_used,
            )
            await self._audit_result(incident_id, status)
        except Exception as exc:
            error_code = type(exc).__name__
            logger.warning("Face-recognition job failed (%s)", error_code)
            try:
                await db.fail_face_recognition_job(incident_id, error_code)
            except Exception as store_exc:
                logger.error(
                    "Could not record face-recognition failure (%s)",
                    type(store_exc).__name__,
                )
        finally:
            self._pending_crops.pop(incident_id, None)

    async def _load_gallery(
            self, key: bytes, model_name: str, model_version: str,
    ) -> dict[int, dict[str, Any]]:
        enrollments = {
            int(row["person_id"]): row
            for row in await db.list_face_enrollments()
            if row["active"]
        }
        gallery: dict[int, dict[str, Any]] = {}
        for row in await db.list_face_embeddings():
            person_id = int(row["person_id"])
            person = enrollments.get(person_id)
            if (
                person is None or not row["active"]
                or row["model_name"] != model_name
                or row["model_version"] != model_version
            ):
                continue
            entry = gallery.setdefault(person_id, {
                "employee_code": person["employee_code"],
                "full_name": person["full_name"],
                "department": person["department"],
                "embeddings": [],
            })
            entry["embeddings"].append(decrypt_embedding(row["embedding"], key))
        return gallery

    @staticmethod
    async def _audit_result(incident_id: int, status: str) -> None:
        try:
            await db.write_audit_log(
                "face_match_processed", target_type="incident",
                target_id=str(incident_id), details={"status": status},
            )
        except Exception as exc:
            logger.error("Could not audit face-recognition result (%s)", type(exc).__name__)

    @staticmethod
    async def _audit_reused_result(
            incident_id: int, source_incident_id: int,
    ) -> None:
        try:
            await db.write_audit_log(
                "face_match_reused_from_track",
                target_type="incident", target_id=str(incident_id),
                details={"source_incident_id": source_incident_id},
            )
        except Exception as exc:
            logger.error("Could not audit reused face match (%s)", type(exc).__name__)
