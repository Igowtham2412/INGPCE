import unittest
from unittest.mock import patch

import numpy as np

from app.cameras.stream_handler import RTSPStream


class _FailedCapture:
    def __init__(self):
        self.released = False

    def set(self, _property, _value):
        return True

    def isOpened(self):
        return False

    def release(self):
        self.released = True


class _OpenedCapture(_FailedCapture):
    def __init__(self, opened):
        super().__init__()
        self.opened = opened

    def isOpened(self):
        return self.opened


class StreamHandlerReconnectTests(unittest.TestCase):
    def test_failed_substream_open_falls_back_to_main_stream(self):
        stream = RTSPStream(
            "CAM16",
            "rtsp://nvr/Streaming/Channels/202",
            fallback_url="rtsp://nvr/Streaming/Channels/201",
        )
        substream = _OpenedCapture(False)
        main_stream = _OpenedCapture(True)

        with patch(
            "app.cameras.stream_handler.cv2.VideoCapture",
            side_effect=[substream, main_stream],
        ) as video_capture:
            capture = stream._open()

        self.assertIs(capture, main_stream)
        self.assertTrue(substream.released)
        self.assertTrue(stream._using_fallback)
        self.assertEqual(video_capture.call_args_list[0].args[0], stream.rtsp_url)
        self.assertEqual(video_capture.call_args_list[1].args[0], stream.fallback_url)

    def test_read_new_returns_each_latest_frame_only_once(self):
        stream = RTSPStream("CAM01", "rtsp://example.invalid/stream")
        stream._frame = np.array([[1]], dtype=np.uint8)
        stream._frame_sequence = 1

        first = stream.read_new()

        self.assertEqual(first[0, 0], 1)
        self.assertIsNone(stream.read_new())

        stream._frame = np.array([[2]], dtype=np.uint8)
        stream._frame_sequence = 2
        latest = stream.read_new()
        self.assertEqual(latest[0, 0], 2)
        self.assertIsNone(stream.read_new())

    def test_read_returns_a_copy_of_the_most_recent_frame(self):
        stream = RTSPStream("CAM01", "rtsp://example.invalid/stream")
        stream._frame = np.array([[1]], dtype=np.uint8)
        stream._frame_sequence = 1

        first = stream.read()
        first[0, 0] = 9
        stream._frame = np.array([[2]], dtype=np.uint8)
        stream._frame_sequence = 2

        latest = stream.read()

        self.assertEqual(latest[0, 0], 2)
        self.assertEqual(stream._frame[0, 0], 2)

    def test_frame_age_controls_stale_status(self):
        stream = RTSPStream(
            "CAM01", "rtsp://example.invalid/stream", frame_timeout_seconds=10,
        )
        stream.last_frame_monotonic = 100

        with patch("app.cameras.stream_handler.time.monotonic", return_value=109):
            self.assertFalse(stream.is_stale())
        with patch("app.cameras.stream_handler.time.monotonic", return_value=110.1):
            self.assertTrue(stream.is_stale())

    def test_frame_age_reports_age_and_missing_frame(self):
        stream = RTSPStream("CAM01", "rtsp://example.invalid/stream")
        self.assertIsNone(stream.frame_age_seconds())

        stream.last_frame_monotonic = 100
        with patch("app.cameras.stream_handler.time.monotonic", return_value=103.5):
            self.assertEqual(stream.frame_age_seconds(), 3.5)

    def test_reconnect_request_marks_stream_offline(self):
        stream = RTSPStream("CAM01", "rtsp://example.invalid/stream")
        stream.connected = True

        stream.request_reconnect()

        self.assertFalse(stream.connected)
        self.assertTrue(stream._reconnect_event.is_set())

    def test_failed_open_releases_capture_before_retry(self):
        stream = RTSPStream("CAM01", "rtsp://example.invalid/stream", reconnect_delay=0)
        capture = _FailedCapture()
        stream._running = True
        stream._frame = object()
        stream.last_frame_time = 123
        stream._stop_event.set()

        def failed_open(*_args):
            stream._running = False
            return capture

        with patch("app.cameras.stream_handler.cv2.VideoCapture", side_effect=failed_open):
            stream._update_loop()

        self.assertTrue(capture.released)
        self.assertIsNone(stream._cap)
        self.assertFalse(stream.connected)
        self.assertIsNone(stream.read())
        self.assertEqual(stream.last_frame_time, 0.0)

    def test_failed_read_clears_stale_frame_and_releases_capture(self):
        stream = RTSPStream("CAM01", "rtsp://example.invalid/stream", reconnect_delay=0)
        stream._running = True
        stream._frame = object()
        stream.last_frame_time = 123
        stream.connected = True
        stream._stop_event.set()

        class ReadFailureCapture(_FailedCapture):
            def isOpened(self):
                return True

            def read(self):
                return False, None

            def release(self):
                super().release()
                stream._running = False

        capture = ReadFailureCapture()
        with patch("app.cameras.stream_handler.cv2.VideoCapture", return_value=capture):
            stream._update_loop()

        self.assertTrue(capture.released)
        self.assertIsNone(stream._cap)
        self.assertFalse(stream.connected)
        self.assertIsNone(stream.read())
        self.assertEqual(stream.last_frame_time, 0.0)

    def test_failed_substream_read_switches_to_main_stream(self):
        stream = RTSPStream(
            "CAM16", "rtsp://nvr/Streaming/Channels/202",
            fallback_url="rtsp://nvr/Streaming/Channels/201",
            reconnect_delay=0,
        )
        stream._running = True
        stream.connected = True

        class StalledCapture(_FailedCapture):
            def isOpened(self):
                return True

            def read(self):
                return False, None

            def release(self):
                super().release()
                stream._running = False

        capture = StalledCapture()
        with patch("app.cameras.stream_handler.cv2.VideoCapture", return_value=capture):
            stream._update_loop()

        self.assertTrue(capture.released)
        self.assertTrue(stream._using_fallback)


if __name__ == "__main__":
    unittest.main()
