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
import os
import cv2
import time
import asyncio
import logging
import uuid
import numpy as np
from typing import Dict, Optional, Callable

from . import config, db
from .stream_handler import RTSPStream
from .compliance_engine import (
    ComplianceEngine,
    cooldown_active,
    prune_expired_cooldowns,
    violation_class_for,
)
from .ws_hub import hub

logger = logging.getLogger("ppe.camera_manager")

BOX_COLOR_VIOLATION = (0, 0, 255)
BOX_COLOR_OK = (0, 200, 0)
BOX_COLOR_UNKNOWN = (0, 165, 255)


def _resize_to_output(frame):
    """Resize any frame to the configured output resolution (default 1920x1080)."""
    h, w = frame.shape[:2]
    target_w, target_h = config.OUTPUT_WIDTH, config.OUTPUT_HEIGHT
    if w == target_w and h == target_h:
        return frame
    return cv2.resize(frame, (target_w, target_h), interpolation=cv2.INTER_LINEAR)


def _draw_annotations(frame, statuses, camera_label: str):
    for status in statuses:
        x1, y1, x2, y2 = status.box
        color = (BOX_COLOR_VIOLATION if status.is_violation else
                 BOX_COLOR_UNKNOWN if status.unknown_ppe else BOX_COLOR_OK)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        label = f"ID{status.track_id} " + (
            "VIOLATION: " + ",".join(sorted(status.missing_ppe)) if status.is_violation else
            "UNKNOWN: " + ",".join(sorted(status.unknown_ppe)) if status.unknown_ppe else "OK"
        )
        cv2.putText(frame, label, (x1, max(20, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
    cv2.putText(frame, camera_label, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    cv2.putText(frame, time.strftime("%Y-%m-%d %H:%M:%S"), (10, frame.shape[0] - 12),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    return frame


class CameraManager:
    def __init__(self, detector_getter: Callable[[], object], alert_manager):
        self._detector_getter = detector_getter
        self._alert_manager = alert_manager
        self._tasks: Dict[str, asyncio.Task] = {}
        self._streams: Dict[str, RTSPStream] = {}
        self._watchdog_task: Optional[asyncio.Task] = None
        self._evidence_tasks = set()
        self._restart_after: Dict[str, float] = {}
        self._restart_attempts: Dict[str, int] = {}

    async def start_camera(self, camera: dict):
        camera_id = camera["camera_id"]
        if camera_id in self._tasks:
            logger.info(f"[{camera_id}] already running, skipping start")
            return
        stream = RTSPStream(
            camera_id, camera["rtsp_url"],
            reconnect_delay=config.CAMERA_RECONNECT_INITIAL_SECONDS,
            max_reconnect_delay=config.CAMERA_RECONNECT_MAX_SECONDS,
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
        try:
            await db.delete_camera_status(camera_id)
        except Exception as e:
            logger.warning(f"[{camera_id}] could not clear camera_status row: {e}")
        logger.info(f"[{camera_id}] worker stopped")

    async def refresh_camera(self, camera_id: str) -> bool:
        camera = await db.get_camera(camera_id)
        if not camera:
            return False
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
        if self._evidence_tasks:
            await asyncio.gather(*list(self._evidence_tasks), return_exceptions=True)

    async def _watchdog(self):
        while True:
            now = time.monotonic()
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

    async def _write_evidence_clip(self, stream: RTSPStream, violation_id: int,
                                   start_time: float, end_time: float):
        if end_time > time.monotonic():
            await asyncio.sleep(end_time - time.monotonic())
        samples = stream.clip_frames(start_time, end_time)
        if not samples:
            logger.warning("[%s] no buffered frames available for incident %s clip",
                           stream.camera_id, violation_id)
            return
        first_image = cv2.imdecode(np.frombuffer(samples[0][1], dtype=np.uint8), cv2.IMREAD_COLOR)
        if first_image is None:
            logger.error("[%s] could not decode buffered frames for incident %s",
                         stream.camera_id, violation_id)
            return
        os.makedirs(config.EVIDENCE_CLIP_DIR, exist_ok=True)
        video_path = os.path.join(config.EVIDENCE_CLIP_DIR, f"violation_{violation_id}.mp4")
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
            frame_count = max(1, int((end_time - start_time) * config.EVIDENCE_CLIP_FPS))
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

    def _schedule_evidence_clip(self, stream: RTSPStream, violation_id: int,
                                violation_time: float):
        start_time = violation_time - config.EVIDENCE_CLIP_PRE_SECONDS
        end_time = violation_time + config.EVIDENCE_CLIP_POST_SECONDS
        task = asyncio.create_task(
            self._write_evidence_clip(stream, violation_id, start_time, end_time))
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

    def get_stream(self, camera_id: str) -> Optional[RTSPStream]:
        return self._streams.get(camera_id)

    def running_camera_ids(self):
        return list(self._tasks.keys())

    async def _camera_worker(self, camera: dict, stream: RTSPStream):
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
        frame_count = 0
        last_statuses = []
        last_alerted_at = {}
        broadcast_interval = 1.0 / max(1, config.BROADCAST_FPS)
        last_broadcast = 0.0
        last_status_write = 0.0

        while True:
            try:
                frame = stream.read()
                if frame is None:
                    await asyncio.sleep(0.2)
                    continue
                self._restart_attempts.pop(camera_id, None)

                frame_count += 1
                if frame_count % config.FRAME_SKIP == 0:
                    detections = await asyncio.to_thread(
                        detector.infer, frame, camera_id, camera_rois)
                    last_statuses = engine.evaluate(detections, frame.shape)
                    last_alerted_at = prune_expired_cooldowns(
                        last_alerted_at, time.monotonic(),
                        config.VIOLATION_COOLDOWN_SECONDS,
                    )

                    for status in last_statuses:
                        if not status.is_violation:
                            continue

                        for violation_type in status.missing_ppe:
                            alert_key = (status.track_id, violation_type)
                            stored_violation_type = violation_class_for(violation_type)
                            if cooldown_active(
                                    last_alerted_at, alert_key, time.monotonic(),
                                    config.VIOLATION_COOLDOWN_SECONDS):
                                continue

                            try:
                                os.makedirs(config.SNAPSHOT_DIR, exist_ok=True)
                                evidence_id = uuid.uuid4().hex
                                clean_snapshot_path = os.path.join(
                                    config.SNAPSHOT_DIR, f"{evidence_id}_clean.jpg")
                                snapshot_path = os.path.join(
                                    config.SNAPSHOT_DIR, f"{evidence_id}_annotated.jpg")
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
                                self._schedule_evidence_clip(
                                    stream, violation_id, time.monotonic(),
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

                now = time.time()
                if now - last_broadcast >= broadcast_interval:
                    annotated = _draw_annotations(frame.copy(), last_statuses, f"{camera_name} ({area_group_name})")
                    annotated = _resize_to_output(annotated)
                    ok, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, config.JPEG_QUALITY])
                    if ok:
                        await hub.broadcast_frame(camera_id, buf.tobytes())
                    last_broadcast = now

                if now - last_status_write >= config.CAMERA_STATUS_UPDATE_INTERVAL:
                    try:
                        await db.update_camera_status(camera_id, camera_name, area_group_name,
                                                       stream.connected, stream.last_frame_time)
                    except Exception as e:
                        logger.warning(f"[{camera_id}] status update failed (non-fatal): {e}")
                    last_status_write = now

                await asyncio.sleep(0.01)

            except asyncio.CancelledError:
                logger.info(f"[{camera_id}] worker cancelled (stop/refresh requested)")
                raise
            except Exception as e:
                logger.error(f"[{camera_id}] unexpected error in worker loop (continuing): "
                             f"{type(e).__name__}: {e}")
                await asyncio.sleep(1.0)
