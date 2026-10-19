"""
stream_handler.py — threaded RTSP frame grabber with automatic reconnect,
always exposing only the latest frame.
"""
import cv2
import time
import threading
import logging
from collections import deque

logger = logging.getLogger("ppe.stream")


class RTSPStream:
    def __init__(self, camera_id: str, rtsp_url: str, reconnect_delay: float = 2,
                 max_reconnect_delay: float = 60, clip_fps: float = 4,
                 clip_buffer_seconds: float = 12, clip_width: int = 640):
        self.camera_id = camera_id
        self.rtsp_url = rtsp_url
        self.reconnect_delay = reconnect_delay
        self.max_reconnect_delay = max_reconnect_delay
        self.clip_fps = clip_fps
        self.clip_width = clip_width
        self._clip_frames = deque(maxlen=max(1, int(clip_fps * clip_buffer_seconds) + 2))
        self._last_clip_sample = 0.0
        self._cap = None
        self._frame = None
        self._lock = threading.Lock()
        self._running = False
        self._stop_event = threading.Event()
        self._thread = None
        self.last_frame_time = 0.0
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
        if self._thread:
            self._thread.join(timeout=2)
        if self._cap:
            self._cap.release()

    def _open(self):
        cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def _update_loop(self):
        retry_delay = self.reconnect_delay
        while self._running:
            if self._cap is None or not self._cap.isOpened():
                logger.info(f"[{self.camera_id}] connecting to RTSP source")
                self._cap = self._open()
                if not self._cap.isOpened():
                    self.connected = False
                    logger.warning(f"[{self.camera_id}] connection failed, retrying in {retry_delay:.1f}s")
                    self._stop_event.wait(retry_delay)
                    retry_delay = min(retry_delay * 2, self.max_reconnect_delay)
                    continue
                self.connected = True
                logger.info(f"[{self.camera_id}] connected")

            try:
                ok, frame = self._cap.read()
            except Exception as e:
                logger.warning(f"[{self.camera_id}] frame read raised {type(e).__name__}: {e}")
                ok, frame = False, None

            if not ok or frame is None:
                logger.warning(f"[{self.camera_id}] frame read failed, reconnecting")
                self.connected = False
                self._cap.release()
                self._cap = None
                self._stop_event.wait(retry_delay)
                retry_delay = min(retry_delay * 2, self.max_reconnect_delay)
                continue

            retry_delay = self.reconnect_delay
            now = time.monotonic()
            with self._lock:
                self._frame = frame
                self.last_frame_time = time.time()
                if now - self._last_clip_sample >= 1 / self.clip_fps:
                    clip_frame = self._resize_clip_frame(frame)
                    ok, encoded = cv2.imencode(
                        ".jpg", clip_frame, [cv2.IMWRITE_JPEG_QUALITY, 60])
                    if ok:
                        self._clip_frames.append((now, encoded.tobytes()))
                        self._last_clip_sample = now

    def _resize_clip_frame(self, frame):
        height, width = frame.shape[:2]
        target_width = min(width, self.clip_width)
        target_height = max(1, int(height * target_width / width))
        return cv2.resize(frame, (target_width, target_height), interpolation=cv2.INTER_AREA)

    def read(self):
        with self._lock:
            return None if self._frame is None else self._frame.copy()

    def clip_frames(self, start_time: float, end_time: float):
        with self._lock:
            return [
                (timestamp, jpeg)
                for timestamp, jpeg in self._clip_frames
                if start_time <= timestamp <= end_time
            ]

    def is_stale(self, max_age_seconds: float = 10.0) -> bool:
        if self.last_frame_time == 0:
            return True
        return (time.time() - self.last_frame_time) > max_age_seconds
