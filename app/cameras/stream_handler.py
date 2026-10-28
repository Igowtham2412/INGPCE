"""
stream_handler.py — threaded RTSP frame grabber with automatic reconnect,
always exposing only the latest frame.
"""
import os

os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
    "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay"
)

import logging
import threading
import time
from collections import deque

import cv2

logger = logging.getLogger("ppe.stream")


class RTSPStream:
    def __init__(self, camera_id: str, rtsp_url: str, reconnect_delay: float = 2,
                 max_reconnect_delay: float = 60, clip_fps: float = 4,
                 clip_buffer_seconds: float = 12, clip_width: int = 640,
                 frame_timeout_seconds: float = 10,
                 reconnect_semaphore=None, fallback_url: str | None = None):
        self.camera_id = camera_id
        self.rtsp_url = rtsp_url
        self.fallback_url = fallback_url if fallback_url != rtsp_url else None
        self._using_fallback = False
        self.reconnect_delay = reconnect_delay
        self.max_reconnect_delay = max_reconnect_delay
        self.frame_timeout_seconds = frame_timeout_seconds
        self._reconnect_semaphore = reconnect_semaphore or threading.BoundedSemaphore(1)
        self.clip_fps = clip_fps
        self.clip_width = clip_width
        self._clip_frames = deque(maxlen=max(1, int(clip_fps * clip_buffer_seconds) + 2))
        self._last_clip_sample = 0.0
        self._cap = None
        self._frame = None
        self._frame_sequence = 0
        self._last_consumed_sequence = 0
        self._lock = threading.Lock()
        self._running = False
        self._stop_event = threading.Event()
        self._reconnect_event = threading.Event()
        self._thread = None
        self.last_frame_time = 0.0
        self.last_frame_monotonic = 0.0
        self.capture_fps = 0.0
        self._fps_window_start = time.monotonic()
        self._fps_window_frames = 0
        self.reconnect_count = 0
        self.connected = False

    def start(self):
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._update_loop, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._running = False
        self._stop_event.set()
        self._reconnect_event.set()
        if self._thread:
            self._thread.join(timeout=2)
        if self._cap:
            self._cap.release()

    def _open(self):
        timeout_ms = max(1, int(self.frame_timeout_seconds * 1000))
        urls = (
            [self.fallback_url]
            if self._using_fallback and self.fallback_url
            else [self.rtsp_url]
        )
        if not self._using_fallback and self.fallback_url:
            urls.append(self.fallback_url)

        for index, url in enumerate(urls):
            cap = cv2.VideoCapture(
                url,
                cv2.CAP_FFMPEG,
                [
                    cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, timeout_ms,
                    cv2.CAP_PROP_READ_TIMEOUT_MSEC, timeout_ms,
                ],
            )
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            if cap.isOpened():
                if index:
                    self._using_fallback = True
                    logger.warning(
                        "[%s] sub-stream unavailable; using configured main stream",
                        self.camera_id,
                    )
                return cap
            cap.release()
            if index == 0 and len(urls) > 1:
                logger.warning(
                    "[%s] sub-stream could not be opened; trying main stream",
                    self.camera_id,
                )
        return None

    def _clear_frame(self):
        with self._lock:
            self._frame = None
            self.last_frame_time = 0.0
            self.last_frame_monotonic = 0.0

    def _update_loop(self):
        retry_delay = self.reconnect_delay
        while self._running:
            if self._cap is None or not self._cap.isOpened():
                logger.info("[%s] connecting to RTSP source", self.camera_id)
                try:
                    with self._reconnect_semaphore:
                        self.reconnect_count += 1
                        self._cap = self._open()
                except Exception as error:
                    logger.warning(
                        "[%s] RTSP open failed (%s); retrying in %.1fs",
                        self.camera_id, type(error).__name__, retry_delay,
                    )
                    self._cap = None
                if self._cap is None or not self._cap.isOpened():
                    self.connected = False
                    if self._cap:
                        self._cap.release()
                    self._cap = None
                    self._clear_frame()
                    logger.warning(
                        "[%s] connection failed, retrying in %.1fs",
                        self.camera_id, retry_delay,
                    )
                    self._stop_event.wait(retry_delay)
                    retry_delay = min(retry_delay * 2, self.max_reconnect_delay)
                    continue
                self.connected = True
                self._reconnect_event.clear()
                logger.info("[%s] connected", self.camera_id)

            try:
                ok, frame = self._cap.read()
            except Exception as error:
                logger.warning(
                    "[%s] frame read raised %s", self.camera_id, type(error).__name__,
                )
                ok, frame = False, None

            if not ok or frame is None or self._reconnect_event.is_set():
                logger.warning("[%s] frame capture stalled or failed; reconnecting", self.camera_id)
                self.connected = False
                self._cap.release()
                self._cap = None
                self._clear_frame()
                if self.fallback_url and not self._using_fallback:
                    self._using_fallback = True
                    logger.warning(
                        "[%s] sub-stream stalled; switching to configured main stream",
                        self.camera_id,
                    )
                self._stop_event.wait(retry_delay)
                retry_delay = min(retry_delay * 2, self.max_reconnect_delay)
                continue

            retry_delay = self.reconnect_delay
            now = time.monotonic()
            with self._lock:
                self._frame = frame
                self._frame_sequence += 1
                self.last_frame_time = time.time()
                self.last_frame_monotonic = now
                self._fps_window_frames += 1
                elapsed = now - self._fps_window_start
                if elapsed >= 1:
                    self.capture_fps = self._fps_window_frames / elapsed
                    self._fps_window_frames = 0
                    self._fps_window_start = now
                sample_clip = now - self._last_clip_sample >= 1 / self.clip_fps
                if sample_clip:
                    self._last_clip_sample = now

            if sample_clip:
                clip_frame = self._resize_clip_frame(frame)
                ok, encoded = cv2.imencode(
                    ".jpg", clip_frame, [cv2.IMWRITE_JPEG_QUALITY, 60])
                if ok:
                    with self._lock:
                        self._clip_frames.append((now, encoded.tobytes()))

    def _resize_clip_frame(self, frame):
        height, width = frame.shape[:2]
        target_width = min(width, self.clip_width)
        target_height = max(1, int(height * target_width / width))
        return cv2.resize(frame, (target_width, target_height), interpolation=cv2.INTER_AREA)

    def read(self):
        with self._lock:
            return None if self._frame is None else self._frame.copy()

    def read_new(self):
        with self._lock:
            if (self._frame is None
                    or self._frame_sequence == self._last_consumed_sequence):
                return None
            self._last_consumed_sequence = self._frame_sequence
            return self._frame.copy()

    def read_with_sequence(self):
        with self._lock:
            if self._frame is None:
                return None, self._frame_sequence
            return self._frame.copy(), self._frame_sequence

    def clip_frames(self, start_time: float, end_time: float):
        with self._lock:
            return [
                (timestamp, jpeg)
                for timestamp, jpeg in self._clip_frames
                if start_time <= timestamp <= end_time
            ]

    def is_stale(self, max_age_seconds: float = None) -> bool:
        max_age = (
            self.frame_timeout_seconds
            if max_age_seconds is None else max_age_seconds
        )
        with self._lock:
            last_frame = self.last_frame_monotonic
        return last_frame == 0 or time.monotonic() - last_frame > max_age

    def frame_age_seconds(self):
        with self._lock:
            last_frame = self.last_frame_monotonic
        if last_frame == 0:
            return None
        return max(0.0, time.monotonic() - last_frame)

    def request_reconnect(self):
        self.connected = False
        self._reconnect_event.set()
