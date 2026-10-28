import unittest
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

import numpy as np
from fastapi.testclient import TestClient

from app import config
from app.main import _hikvision_playback_url, app

CAMERA = {
    "camera_id": "CAM01",
    "rtsp_url": "rtsp://viewer:password@192.168.1.20:554/Streaming/Channels/101",
}


class FakeCapture:
    def __init__(self, has_frame=True):
        self.frames = [np.zeros((2, 2, 3), dtype=np.uint8)] if has_frame else []
        self.released = False

    def isOpened(self):
        return True

    def read(self):
        if self.frames:
            return True, self.frames.pop()
        return False, None

    def release(self):
        self.released = True


class CameraPlaybackApiTests(unittest.TestCase):
    def setUp(self):
        self.auth_patch = patch.object(config, "AUTH_ENABLED", False)
        self.auth_patch.start()
        self.addCleanup(self.auth_patch.stop)
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_hikvision_playback_url_uses_track_and_selected_interval(self):
        local_start = datetime(
            2025, 1, 2, 3, 4, 5,
            tzinfo=timezone(timedelta(hours=5, minutes=30)),
        )
        url = _hikvision_playback_url(
            CAMERA["rtsp_url"], local_start, local_start + timedelta(minutes=10),
        )
        parsed = urlsplit(url)

        self.assertEqual(parsed.hostname, "192.168.1.20")
        self.assertEqual(parsed.path, "/Streaming/tracks/101")
        self.assertEqual(parse_qs(parsed.query), {
            "starttime": ["20250101t213405z"],
            "endtime": ["20250101t214405z"],
        })
        self.assertNotIn("password", parsed.query)

    def test_playback_streams_frames_without_exposing_rtsp_url(self):
        capture = FakeCapture()
        with (
            patch("app.main.db.get_camera", AsyncMock(return_value=CAMERA)),
            patch("app.main.cv2.VideoCapture", return_value=capture) as open_capture,
        ):
            response = self.client.get(
                "/api/cameras/CAM01/playback"
                "?start=2025-01-02T03%3A04%3A05&duration_minutes=10",
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith(
            "multipart/x-mixed-replace",
        ))
        self.assertIn(b"Content-Type: image/jpeg", response.content)
        self.assertNotIn(b"password", response.content)
        self.assertTrue(capture.released)
        opened_url = urlsplit(open_capture.call_args.args[0])
        self.assertEqual(opened_url.path, "/Streaming/tracks/101")
        self.assertEqual(
            parse_qs(opened_url.query)["endtime"],
            [(
                datetime.fromisoformat("2025-01-02T03:14:05")
                .astimezone(UTC)
                .strftime("%Y%m%dt%H%M%Sz")
                .lower()
            )],
        )

    def test_missing_recording_is_reported_as_gateway_error(self):
        capture = FakeCapture(has_frame=False)
        with (
            patch("app.main.db.get_camera", AsyncMock(return_value=CAMERA)),
            patch("app.main.cv2.VideoCapture", return_value=capture),
        ):
            response = self.client.get(
                "/api/cameras/CAM01/playback?start=2025-01-02T03%3A04%3A05",
            )

        self.assertEqual(response.status_code, 502)
        self.assertIn("Check the selected time", response.json()["detail"])
        self.assertTrue(capture.released)

    def test_unknown_camera_and_unsupported_rtsp_path_are_rejected(self):
        with patch("app.main.db.get_camera", AsyncMock(return_value=None)):
            missing = self.client.get(
                "/api/cameras/UNKNOWN/playback?start=2025-01-02T03%3A04%3A05",
            )
        self.assertEqual(missing.status_code, 404)

        from datetime import datetime

        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as error:
            _hikvision_playback_url(
                "rtsp://192.168.1.20/live",
                datetime(2025, 1, 2),
                datetime(2025, 1, 2, 0, 10),
            )
        self.assertEqual(error.exception.status_code, 422)

    def test_playback_duration_is_bounded(self):
        response = self.client.get(
            "/api/cameras/CAM01/playback"
            "?start=2025-01-02T03%3A04%3A05&duration_minutes=121",
        )
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
