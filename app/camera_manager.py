"""
camera_manager.py
------------------
Manages the lifecycle of camera worker tasks dynamically (add/edit/refresh/
delete from the dashboard, no app restart needed).

RELIABILITY: throttled DB status writes (was every loop tick, now every
CAMERA_STATUS_UPDATE_INTERVAL seconds/camera), self-healing loop (a
transient error is logged and the loop continues instead of the camera
task dying permanently), and retry-safe violation logging (a violation is
only marked "already logged" after its DB write succeeds).
"""
import asyncio
import logging
import os
import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import cv2
import numpy as np

from . import config, db
from .compliance_engine import (
    ComplianceEngine,
    cooldown_active,
    prune_expired_cooldowns,
    violation_class_for,
)
from .faces.face_recognition import violation_requires_face_recognition
from .stream_handler import RTSPStream
from .ws_hub import hub

logger = logging.getLogger("ppe.camera_manager")

BOX_COLOR_VIOLATION = (0, 0, 255)
BOX_COLOR_OK = (0, 200, 0)
BOX_COLOR_UNKNOWN = (0, 165, 255)


def _resize_to_output(frame):
    """Keep the legacy full-HD output resize behavior."""
    return cv2.resize(
        frame, (config.OUTPUT_WIDTH, config.OUTPUT_HEIGHT),
        interpolation=cv2.INTER_AREA,
    )


def _resize_to_tile(frame):
    h, w = frame.shape[:2]
    target_w = min(w, config.LIVE_TILE_WIDTH)
    if w == target_w:
        return frame
    target_h = max(1, int(h * target_w / w))
    return cv2.resize(frame, (target_w, target_h), interpolation=cv2.INTER_AREA)


class DetectionScheduler:
    def __init__(self, fps: float, start_time: float = None):
        if fps <= 0:
            raise ValueError("Detection FPS must be positive")
        self.interval = 1.0 / fps
        self.next_run_at = time.monotonic() if start_time is None else start_time

    def delay(self, now: float) -> float:
        return max(0.0, self.next_run_at - now)

    def advance(self, now: float):
        self.next_run_at += self.interval
        if self.next_run_at <= now:
            missed = int((now - self.next_run_at) / self.interval) + 1
            self.next_run_at += missed * self.interval


def _draw_annotations(
    frame, statuses, camera_label: str, scale_x: float = 1, scale_y: float = 1,
):
    for status in statuses:
        x1, y1, x2, y2 = status.box
        x1, x2 = int(x1 * scale_x), int(x2 * scale_x)
        y1, y2 = int(y1 * scale_y), int(y2 * scale_y)
        color = (BOX_COLOR_VIOLATION if status.is_violation else
                 BOX_COLOR_UNKNOWN if status.unknown_ppe else BOX_COLOR_OK)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        details = []
        if status.missing_ppe:
            details.append("MISSING: " + ",".join(sorted(status.missing_ppe)))
        if status.phone_detected:
            details.append("PHONE DETECTED")
        if not details and status.unknown_ppe:
            details.append("UNKNOWN: " + ",".join(sorted(status.unknown_ppe)))
        label = f"ID{status.track_id} " + (" | ".join(details) if details else "OK")
        cv2.putText(frame, label, (x1, max(20, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
        if status.phone_box:
            px1, py1, px2, py2 = status.phone_box
            px1, px2 = int(px1 * scale_x), int(px2 * scale_x)
            py1, py2 = int(py1 * scale_y), int(py2 * scale_y)
            cv2.rectangle(frame, (px1, py1), (px2, py2), BOX_COLOR_UNKNOWN, 2)
            cv2.putText(
                frame, "PHONE", (px1, max(20, py1 - 6)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, BOX_COLOR_UNKNOWN, 2,
            )
    cv2.putText(frame, camera_label, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    cv2.putText(frame, time.strftime("%Y-%m-%d %H:%M:%S"), (10, frame.shape[0] - 12),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    return frame


def _encode_annotated_frame(frame, statuses, camera_label: str):
    height, width = frame.shape[:2]
    tile = _resize_to_tile(frame)
    scale_x = tile.shape[1] / width
    scale_y = tile.shape[0] / height
    annotated = _draw_annotations(
        tile, statuses, camera_label, scale_x=scale_x, scale_y=scale_y,
    )
    ok, buffer = cv2.imencode(
        ".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, config.JPEG_QUALITY])
    return buffer.tobytes() if ok else None


def _encode_fullscreen_frame(frame):
    height, width = frame.shape[:2]
    target_width = min(width, config.FULLSCREEN_WIDTH)
    if width != target_width:
        target_height = max(1, int(height * target_width / width))
        frame = cv2.resize(
            frame, (target_width, target_height), interpolation=cv2.INTER_AREA,
        )
    ok, buffer = cv2.imencode(
        ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, config.JPEG_QUALITY],
    )
    return buffer.tobytes() if ok else None


class CameraManager:
    def __init__(self, detector_getter: Callable[[], object], alert_manager,
                 playback_url_builder=None, playback_capture_opener=None,
                 face_recognition_worker=None):
        self._detector_getter = detector_getter
        self._alert_manager = alert_manager
        self._playback_url_builder = playback_url_builder
        self._playback_capture_opener = playback_capture_opener
        self._face_recognition_worker = face_recognition_worker
        self._tasks: dict[str, asyncio.Task] = {}
        self._streams: dict[str, RTSPStream] = {}
        self._fullscreen_streams: dict[str, RTSPStream] = {}
        self._fullscreen_tasks: dict[str, asyncio.Task] = {}
        self._fullscreen_refs: dict[str, int] = {}
        self._fullscreen_lock = asyncio.Lock()
        self._watchdog_task: asyncio.Task | None = None
        self._evidence_tasks = set()
        self._restart_after: dict[str, float] = {}
        self._restart_attempts: dict[str, int] = {}
        self._reconnect_semaphore = threading.BoundedSemaphore(
            config.CAMERA_MAX_SIMULTANEOUS_RECONNECTS
        )
        self._latest_statuses = {}
        self._detection_metrics = {}
        self._inference_semaphore = asyncio.Semaphore(1)

    async def start_camera(self, camera: dict):
        camera_id = camera["camera_id"]
        if camera_id in self._tasks:
            logger.info(f"[{camera_id}] already running, skipping start")
            return
        sub_stream_url = camera.get("sub_stream_url") or camera["rtsp_url"]
        main_stream_url = camera.get("main_stream_url") or camera["rtsp_url"]
        stream = RTSPStream(
            camera_id, sub_stream_url,
            reconnect_delay=config.CAMERA_RECONNECT_INITIAL_SECONDS,
            max_reconnect_delay=config.CAMERA_RECONNECT_MAX_SECONDS,
            frame_timeout_seconds=config.CAMERA_FRAME_TIMEOUT_SECONDS,
            reconnect_semaphore=self._reconnect_semaphore,
            fallback_url=(
                main_stream_url if main_stream_url != sub_stream_url else None
            ),
            clip_fps=config.EVIDENCE_CLIP_FPS,
            clip_buffer_seconds=(
                config.EVIDENCE_CLIP_PRE_SECONDS
                + config.EVIDENCE_CLIP_POST_SECONDS + 2
            ),
            clip_width=config.EVIDENCE_CLIP_WIDTH,
        ).start()
        self._streams[camera_id] = stream
        task = asyncio.create_task(self._camera_worker(camera, stream))
        self._tasks[camera_id] = task
        self._restart_after.pop(camera_id, None)
        logger.info(f"[{camera_id}] worker started (area_group={camera.get('area_group_name')})")

    async def stop_camera(self, camera_id: str):
        await self._stop_fullscreen_stream(camera_id)
        task = self._tasks.pop(camera_id, None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.warning(f"[{camera_id}] worker task raised on stop: {e}")
        stream = self._streams.pop(camera_id, None)
        if stream:
            stream.stop()
        self._latest_statuses.pop(camera_id, None)
        self._detection_metrics.pop(camera_id, None)
        try:
            await db.delete_camera_status(camera_id)
        except Exception as e:
            logger.warning(f"[{camera_id}] could not clear camera_status row: {e}")
        logger.info(f"[{camera_id}] worker stopped")

    async def refresh_camera(self, camera_id: str, restart_stream: bool = True) -> bool:
        camera = await db.get_camera(camera_id)
        if not camera:
            return False
        stream = self._streams.get(camera_id)
        if not restart_stream and camera["enabled"] and stream is not None:
            task = self._tasks.pop(camera_id, None)
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception as error:
                    logger.warning(
                        "[%s] worker task raised while reloading settings: %s",
                        camera_id, error,
                    )
            self._latest_statuses.pop(camera_id, None)
            self._detection_metrics.pop(camera_id, None)
            self._restart_after.pop(camera_id, None)
            self._restart_attempts.pop(camera_id, None)
            self._tasks[camera_id] = asyncio.create_task(
                self._camera_worker(camera, stream),
            )
            logger.info("[%s] worker settings reloaded without reconnecting stream", camera_id)
            return True
        await self.stop_camera(camera_id)
        if camera["enabled"]:
            await self.start_camera(camera)
        return True

    async def reconcile_all(self):
        """Starts a worker for every ENABLED camera in the DB. If one
        camera fails to start (bad URL, etc.), it's logged and skipped -
        it never blocks the rest from starting."""
        if self._watchdog_task is None or self._watchdog_task.done():
            self._watchdog_task = asyncio.create_task(self._watchdog())
        cameras = await db.list_cameras()
        started = 0
        for cam in cameras:
            if cam["enabled"]:
                try:
                    await self.start_camera(cam)
                    started += 1
                except Exception as e:
                    logger.error(f"[{cam['camera_id']}] failed to start during reconcile: {e}")
        logger.info(f"Reconciled {len(cameras)} camera(s) from DB ({started} started)")

    async def stop_all(self):
        if self._watchdog_task:
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except asyncio.CancelledError:
                pass
            self._watchdog_task = None
        for camera_id in list(self._tasks.keys()):
            await self.stop_camera(camera_id)
        for camera_id in list(self._fullscreen_streams):
            await self._stop_fullscreen_stream(camera_id)
        if self._evidence_tasks:
            await asyncio.gather(*list(self._evidence_tasks), return_exceptions=True)

    async def _watchdog(self):
        while True:
            now = time.monotonic()
            for camera_id, stream in list(self._streams.items()):
                if stream.connected and stream.is_stale(config.CAMERA_FRAME_TIMEOUT_SECONDS):
                    logger.warning(
                        "[%s] no captured frame for over %.1fs; requesting reconnect",
                        camera_id, config.CAMERA_FRAME_TIMEOUT_SECONDS,
                    )
                    stream.request_reconnect()

            for camera_id, task in list(self._tasks.items()):
                if not task.done() or now < self._restart_after.get(camera_id, 0):
                    continue
                error = asyncio.CancelledError() if task.cancelled() else task.exception()
                logger.error("[%s] camera worker exited unexpectedly: %s",
                             camera_id, error or "stopped without an exception")
                self._tasks.pop(camera_id, None)
                stream = self._streams.pop(camera_id, None)
                if stream:
                    stream.stop()
                attempts = self._restart_attempts.get(camera_id, 0) + 1
                self._restart_attempts[camera_id] = attempts
                delay = min(2 ** min(attempts - 1, 6), 60)
                self._restart_after[camera_id] = now + delay
                logger.warning("[%s] scheduling worker restart in %ss (attempt %s)",
                               camera_id, delay, attempts)

            for camera_id, restart_at in list(self._restart_after.items()):
                if now < restart_at or camera_id in self._tasks:
                    continue
                try:
                    camera = await db.get_camera(camera_id)
                    if camera is None or not camera["enabled"]:
                        self._restart_after.pop(camera_id, None)
                        self._restart_attempts.pop(camera_id, None)
                        continue
                    await self.start_camera(camera)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    attempts = self._restart_attempts.get(camera_id, 1)
                    delay = min(2 ** min(attempts, 6), 60)
                    self._restart_after[camera_id] = time.monotonic() + delay
                    logger.exception("[%s] watchdog failed to restart worker; retrying in %ss",
                                     camera_id, delay)

            await asyncio.sleep(config.CAMERA_WATCHDOG_INTERVAL_SECONDS)

    def _write_main_playback_clip(self, playback_url: str, video_path: str):
        capture, frame = self._playback_capture_opener(playback_url)
        writer = None
        try:
            source_fps = capture.get(cv2.CAP_PROP_FPS)
            if not source_fps or source_fps <= 0:
                source_fps = 25
            sample_interval = max(1, round(source_fps / config.EVIDENCE_CLIP_FPS))
            height, width = frame.shape[:2]
            target_width = min(width, config.EVIDENCE_CLIP_WIDTH)
            target_height = max(1, int(height * target_width / width))
            writer = cv2.VideoWriter(
                video_path, cv2.VideoWriter_fourcc(*"mp4v"),
                config.EVIDENCE_CLIP_FPS, (target_width, target_height),
            )
            if not writer.isOpened():
                raise RuntimeError("Video encoder unavailable for main-stream clip")
            frame_index = 0
            while True:
                if frame_index % sample_interval == 0:
                    if width != target_width:
                        frame = cv2.resize(
                            frame, (target_width, target_height),
                            interpolation=cv2.INTER_AREA,
                        )
                    writer.write(frame)
                frame_index += 1
                available, frame = capture.read()
                if not available:
                    break
        finally:
            if writer is not None:
                writer.release()
            capture.release()

    async def _write_evidence_clip(
        self, stream: RTSPStream, camera: dict, violation_id: int,
        start_time: float, end_time: float,
        playback_start: datetime, playback_end: datetime,
        violation_type: str,
    ):
        if end_time > time.monotonic():
            await asyncio.sleep(end_time - time.monotonic())
        clip_directory = config.evidence_directory(
            config.EVIDENCE_CLIP_DIR, violation_type,
        )
        os.makedirs(clip_directory, exist_ok=True)
        video_path = os.path.join(clip_directory, f"violation_{violation_id}.mp4")
        main_clip_written = False
        if self._playback_url_builder and self._playback_capture_opener:
            try:
                main_url = camera.get("main_stream_url") or camera["rtsp_url"]
                playback_url = self._playback_url_builder(
                    main_url, playback_start, playback_end,
                )
                await asyncio.to_thread(
                    self._write_main_playback_clip, playback_url, video_path,
                )
                main_clip_written = True
            except Exception as error:
                logger.warning(
                    "[%s] main-stream playback clip failed (%s); "
                    "falling back to buffered sub-stream evidence",
                    stream.camera_id, type(error).__name__,
                )
                if os.path.exists(video_path):
                    os.remove(video_path)

        if not main_clip_written:
            samples = stream.clip_frames(start_time, end_time)
            if not samples:
                logger.warning("[%s] no buffered frames available for incident %s clip",
                               stream.camera_id, violation_id)
                return
            first_image = cv2.imdecode(
                np.frombuffer(samples[0][1], dtype=np.uint8), cv2.IMREAD_COLOR,
            )
            if first_image is None:
                logger.error("[%s] could not decode buffered frames for incident %s",
                             stream.camera_id, violation_id)
                return
            height, width = first_image.shape[:2]
            writer = cv2.VideoWriter(
                video_path, cv2.VideoWriter_fourcc(*"mp4v"),
                config.EVIDENCE_CLIP_FPS, (width, height),
            )
            if not writer.isOpened():
                logger.error("[%s] video encoder unavailable for incident %s",
                             stream.camera_id, violation_id)
                return
            try:
                timestamps = [timestamp for timestamp, _ in samples]
                sample_index = 0
                latest = first_image
                frame_count = max(
                    1, int((end_time - start_time) * config.EVIDENCE_CLIP_FPS),
                )
                for index in range(frame_count):
                    target_time = start_time + index / config.EVIDENCE_CLIP_FPS
                    while (sample_index + 1 < len(samples)
                           and timestamps[sample_index + 1] <= target_time):
                        sample_index += 1
                        decoded = cv2.imdecode(
                            np.frombuffer(samples[sample_index][1], dtype=np.uint8),
                            cv2.IMREAD_COLOR,
                        )
                        if decoded is not None:
                            latest = decoded
                    writer.write(latest)
            finally:
                writer.release()

        try:
            if not await db.update_violation_video_path(violation_id, video_path):
                os.remove(video_path)
                logger.warning("[%s] incident %s disappeared before its clip path was saved",
                               stream.camera_id, violation_id)
        except Exception:
            try:
                os.remove(video_path)
            except OSError:
                logger.exception("[%s] could not remove unregistered evidence clip %s",
                                 stream.camera_id, video_path)
            raise

    def _schedule_evidence_clip(
        self, stream: RTSPStream, camera: dict, violation_id: int,
        violation_time: float, violation_at: datetime,
        violation_type: str,
    ):
        start_time = violation_time - config.EVIDENCE_CLIP_PRE_SECONDS
        end_time = violation_time + config.EVIDENCE_CLIP_POST_SECONDS
        playback_start = violation_at - timedelta(
            seconds=config.EVIDENCE_CLIP_PRE_SECONDS,
        )
        playback_end = violation_at + timedelta(
            seconds=config.EVIDENCE_CLIP_POST_SECONDS,
        )
        task = asyncio.create_task(
            self._write_evidence_clip(
                stream, camera, violation_id, start_time, end_time,
                playback_start, playback_end, violation_type,
            ),
        )
        self._evidence_tasks.add(task)

        def completed(completed_task):
            self._evidence_tasks.discard(completed_task)
            if completed_task.cancelled():
                return
            error = completed_task.exception()
            if error:
                logger.error("[%s] evidence clip job failed: %s",
                             stream.camera_id, error, exc_info=error)

        task.add_done_callback(completed)

    def is_running(self, camera_id: str) -> bool:
        return camera_id in self._tasks

    def get_stream(self, camera_id: str) -> RTSPStream | None:
        return self._streams.get(camera_id)

    def running_camera_ids(self):
        return list(self._tasks.keys())

    async def acquire_fullscreen_stream(self, camera_id: str) -> bool:
        async with self._fullscreen_lock:
            if camera_id in self._fullscreen_streams:
                self._fullscreen_refs[camera_id] += 1
                return True
            camera = await db.get_camera(camera_id)
            if camera is None or not camera["enabled"]:
                return False
            stream_url = camera.get("main_stream_url") or camera["rtsp_url"]
            stream = RTSPStream(
                camera_id, stream_url,
                reconnect_delay=config.CAMERA_RECONNECT_INITIAL_SECONDS,
                max_reconnect_delay=config.CAMERA_RECONNECT_MAX_SECONDS,
                frame_timeout_seconds=config.CAMERA_FRAME_TIMEOUT_SECONDS,
                reconnect_semaphore=self._reconnect_semaphore,
            ).start()
            self._fullscreen_streams[camera_id] = stream
            self._fullscreen_refs[camera_id] = 1
            self._fullscreen_tasks[camera_id] = asyncio.create_task(
                self._fullscreen_worker(camera_id, stream),
            )
            return True

    async def release_fullscreen_stream(self, camera_id: str):
        async with self._fullscreen_lock:
            refs = self._fullscreen_refs.get(camera_id, 0) - 1
            if refs > 0:
                self._fullscreen_refs[camera_id] = refs
                return
            await self._stop_fullscreen_stream_locked(camera_id)

    async def _stop_fullscreen_stream(self, camera_id: str):
        async with self._fullscreen_lock:
            await self._stop_fullscreen_stream_locked(camera_id)

    async def _stop_fullscreen_stream_locked(self, camera_id: str):
        self._fullscreen_refs.pop(camera_id, None)
        task = self._fullscreen_tasks.pop(camera_id, None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        stream = self._fullscreen_streams.pop(camera_id, None)
        if stream:
            stream.stop()

    async def _fullscreen_worker(self, camera_id: str, stream: RTSPStream):
        broadcast_interval = 1.0 / config.FULLSCREEN_FPS
        last_frame_sequence = 0
        last_broadcast = 0.0
        subscriber_key = f"fullscreen:{camera_id}"
        try:
            while True:
                frame, frame_sequence = stream.read_with_sequence()
                now = time.monotonic()
                if (frame is not None and frame_sequence != last_frame_sequence
                        and now - last_broadcast >= broadcast_interval
                        and hub.has_video_subscribers(subscriber_key)):
                    jpeg_bytes = await asyncio.to_thread(
                        _encode_fullscreen_frame, frame,
                    )
                    if jpeg_bytes is not None:
                        await hub.broadcast_frame(subscriber_key, jpeg_bytes)
                        last_broadcast = time.monotonic()
                if frame is not None:
                    last_frame_sequence = frame_sequence
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            raise

    async def _camera_worker(self, camera: dict, stream: RTSPStream):
        await asyncio.gather(
            self._detection_worker(camera, stream),
            self._display_worker(camera, stream),
        )

    async def _detection_worker(self, camera: dict, stream: RTSPStream):
        camera_id = camera["camera_id"]
        camera_name = camera["name"]
        area_group_id = camera.get("area_group_id")
        area_group_name = camera.get("area_group_name") or "Unassigned"
        required_ppe = camera["required_ppe"]
        zones = await db.list_camera_zones(camera_id)
        camera_rois = [zone["polygon"] for zone in zones] or None

        detector = self._detector_getter()
        if detector is None:
            raise RuntimeError("PPE detector is not initialized")
        engine = ComplianceEngine(camera_id, required_ppe, detector.normalized_classes)
        last_statuses = []
        last_alerted_at = {}
        scheduler = DetectionScheduler(config.DETECT_FPS)
        metrics = self._detection_metrics.setdefault(
            camera_id,
            {"detection_fps": 0.0, "inference_ms": 0.0,
             "window_start": time.monotonic(), "window_frames": 0},
        )

        while True:
            try:
                delay = scheduler.delay(time.monotonic())
                if delay > 0:
                    await asyncio.sleep(delay)
                async with self._inference_semaphore:
                    frame = stream.read_new()
                    if frame is None:
                        scheduler.advance(time.monotonic())
                        continue
                    self._restart_attempts.pop(camera_id, None)

                    inference_started = time.monotonic()
                    detections = await asyncio.to_thread(
                        detector.infer, frame, camera_id, camera_rois)
                last_statuses = engine.evaluate(detections, frame.shape)
                self._latest_statuses[camera_id] = last_statuses
                if self._face_recognition_worker is not None:
                    self._face_recognition_worker.observe(
                        camera_id, frame, last_statuses,
                    )
                completed_at = time.monotonic()
                metrics["inference_ms"] = (completed_at - inference_started) * 1000
                metrics["window_frames"] += 1
                metrics_elapsed = completed_at - metrics["window_start"]
                if metrics_elapsed >= 1:
                    metrics["detection_fps"] = metrics["window_frames"] / metrics_elapsed
                    metrics["window_frames"] = 0
                    metrics["window_start"] = completed_at
                scheduler.advance(completed_at)
                last_alerted_at = prune_expired_cooldowns(
                    last_alerted_at, completed_at,
                    config.VIOLATION_COOLDOWN_SECONDS,
                )

                for status in last_statuses:
                    if not status.is_violation:
                        continue

                    violation_types = list(status.missing_ppe)
                    if status.phone_detected:
                        violation_types.append("phone_detected")

                    for violation_type in violation_types:
                        alert_key = (status.track_id, violation_type)
                        stored_violation_type = violation_class_for(violation_type)
                        if cooldown_active(
                                last_alerted_at, alert_key, completed_at,
                                config.VIOLATION_COOLDOWN_SECONDS):
                            continue

                        try:
                            snapshot_directory = config.evidence_directory(
                                config.SNAPSHOT_DIR, stored_violation_type,
                            )
                            os.makedirs(snapshot_directory, exist_ok=True)
                            evidence_id = uuid.uuid4().hex
                            clean_snapshot_path = os.path.join(
                                snapshot_directory, f"{evidence_id}_clean.jpg")
                            snapshot_path = os.path.join(
                                snapshot_directory, f"{evidence_id}_annotated.jpg")
                            evidence_frame = _draw_annotations(
                                frame.copy(), last_statuses, f"{camera_name} ({area_group_name})")
                            if not cv2.imwrite(clean_snapshot_path, frame):
                                raise OSError(f"Could not write clean snapshot: {clean_snapshot_path}")
                            if not cv2.imwrite(snapshot_path, evidence_frame):
                                raise OSError(f"Could not write annotated snapshot: {snapshot_path}")
                            detection_metadata = {
                                "person_box": list(status.box),
                                "frame_width": int(frame.shape[1]),
                                "frame_height": int(frame.shape[0]),
                                "model_names": {
                                    str(class_id): str(class_name)
                                    for class_id, class_name in detector.names.items()
                                },
                                "detections": [
                                    {
                                        "class_id": detection.class_id,
                                        "class_name": detection.cls_norm,
                                        "box": list(detection.box),
                                        "confidence": detection.confidence,
                                    }
                                    for detection in detections
                                ],
                            }

                            violation_id = await db.log_violation(
                                camera_id, camera_name, area_group_name, status.track_id,
                                stored_violation_type, snapshot_path, area_group_id, area_group_name,
                                clean_snapshot_path, detection_metadata,
                                config.VIOLATION_COOLDOWN_SECONDS,
                            )
                            if violation_id is None:
                                os.remove(snapshot_path)
                                os.remove(clean_snapshot_path)
                                continue
                            violation_time = time.monotonic()
                            self._schedule_evidence_clip(
                                stream, camera, violation_id, violation_time,
                                datetime.now(UTC), stored_violation_type,
                            )
                            if (
                                self._face_recognition_worker is not None
                                and violation_requires_face_recognition(
                                    stored_violation_type,
                                )
                            ):
                                try:
                                    self._face_recognition_worker.enqueue_confirmed_incident(
                                        violation_id, camera_id, status.track_id,
                                        stored_violation_type,
                                    )
                                except Exception as face_exc:
                                    logger.error(
                                        "[%s] could not queue optional face recognition (%s)",
                                        camera_id, type(face_exc).__name__,
                                    )
                            last_alerted_at[alert_key] = time.monotonic()
                            matches = await self._alert_manager.handle_violation(
                                violation_id, camera_id, camera_name, area_group_name,
                                status.track_id, stored_violation_type, snapshot_path,
                            )
                            await hub.broadcast_event({
                                "type": "violation",
                                "violation_id": violation_id,
                                "camera_id": camera_id,
                                "camera_name": camera_name,
                                "zone": area_group_name,
                                "area_group": area_group_name,
                                "violation_type": stored_violation_type,
                                "track_id": status.track_id,
                                "snapshot_url": f"/api/snapshot/{violation_id}",
                                "rules_matched": [m.rule_name for m in matches],
                                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                            })
                        except Exception as e:
                            logger.warning(f"[{camera_id}] failed to log/alert violation "
                                            f"'{violation_type}' (will retry next frame): {e}")

            except asyncio.CancelledError:
                logger.info(f"[{camera_id}] detection worker cancelled")
                raise
            except Exception as e:
                logger.error(f"[{camera_id}] unexpected error in detection loop (continuing): "
                             f"{type(e).__name__}: {e}")
                scheduler.advance(time.monotonic())
                await asyncio.sleep(min(scheduler.interval, 1.0))

    async def _display_worker(self, camera: dict, stream: RTSPStream):
        camera_id = camera["camera_id"]
        camera_name = camera["name"]
        area_group_name = camera.get("area_group_name") or "Unassigned"
        broadcast_interval = 1.0 / max(1, config.BROADCAST_FPS)
        last_broadcast = 0.0
        last_frame_sequence = 0
        last_status_write = 0.0

        try:
            while True:
                frame, frame_sequence = stream.read_with_sequence()
                now = time.monotonic()
                if (frame is not None and frame_sequence != last_frame_sequence
                        and now - last_broadcast >= broadcast_interval
                        and hub.has_video_subscribers(camera_id)):
                    statuses = self._latest_statuses.get(camera_id, [])
                    jpeg_bytes = await asyncio.to_thread(
                        _encode_annotated_frame,
                        frame,
                        statuses,
                        f"{camera_name} ({area_group_name})",
                    )
                    if jpeg_bytes is not None:
                        await hub.broadcast_frame(camera_id, jpeg_bytes)
                        last_broadcast = time.monotonic()
                if frame is not None:
                    last_frame_sequence = frame_sequence

                if now - last_status_write >= config.CAMERA_STATUS_UPDATE_INTERVAL:
                    try:
                        await db.update_camera_status(camera_id, camera_name, area_group_name,
                                                       stream.connected and not stream.is_stale(
                                                           config.CAMERA_FRAME_TIMEOUT_SECONDS
                                                       ),
                                                       stream.last_frame_time)
                    except Exception as e:
                        logger.warning(f"[{camera_id}] status update failed (non-fatal): {e}")
                    last_status_write = time.monotonic()

                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            logger.info("[%s] display worker cancelled", camera_id)
            raise

    def stream_telemetry(self) -> dict[str, dict[str, float | None]]:
        telemetry = {}
        for camera_id, stream in self._streams.items():
            metrics = self._detection_metrics.get(camera_id, {})
            telemetry[camera_id] = {
                "capture_fps": round(stream.capture_fps, 2),
                "detection_fps": round(metrics.get("detection_fps", 0.0), 2),
                "latest_frame_age_seconds": stream.frame_age_seconds(),
                "reconnect_count": stream.reconnect_count,
                "inference_ms": round(metrics.get("inference_ms", 0.0), 2),
            }
        return telemetry
