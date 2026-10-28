import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit

from app import main


class CameraEditTests(unittest.IsolatedAsyncioTestCase):
    async def test_editing_ppe_preserves_redacted_stream_url_and_credentials(self):
        rtsp_url = (
            "rtsp://operator:secret@192.168.1.20:554/"
            "Streaming/Channels/101?transport=tcp"
        )
        existing = {
            "camera_id": "CAM01",
            "name": "Camera 1",
            "rtsp_url": rtsp_url,
            "main_stream_url": rtsp_url,
            "sub_stream_url": rtsp_url.replace("101?", "102?"),
            "area_group_id": 1,
            "required_ppe": ["helmet"],
            "enabled": True,
        }
        updated = {
            **existing,
            "required_ppe": ["helmet", "vest"],
        }
        manager = SimpleNamespace(
            refresh_camera=AsyncMock(return_value=True),
            stop_camera=AsyncMock(),
        )

        with (
            patch.object(main.db, "get_camera", new=AsyncMock(
                side_effect=[existing, updated],
            )),
            patch.object(main.db, "update_camera", new=AsyncMock()) as update_camera,
            patch.object(main, "_camera_manager", manager),
        ):
            result = await main.edit_camera(
                "CAM01",
                main.CameraUpdate(
                    rtsp_url=main._redact_rtsp_credentials(rtsp_url),
                    required_ppe=["helmet", "vest"],
                ),
            )

        update_camera.assert_awaited_once_with(
            "CAM01", required_ppe=["helmet", "vest"],
        )
        manager.refresh_camera.assert_awaited_once_with(
            "CAM01", restart_stream=False,
        )
        self.assertEqual(result["rtsp_url"], main._redact_rtsp_credentials(rtsp_url))
        self.assertEqual(
            main._preserve_rtsp_credentials(
                "rtsp://192.168.1.21:554/Streaming/Channels/101",
                rtsp_url,
            ),
            "rtsp://operator:secret@192.168.1.21:554/"
            "Streaming/Channels/101?transport=tcp",
        )

    def test_blank_rtsp_password_keeps_existing_stored_password(self):
        stored_url = "rtsp://operator:retained-password@camera.local/live?tcp=1"
        submitted_url = "rtsp://operator:@camera-new.local/live"

        updated_url = main._preserve_rtsp_credentials(
            submitted_url, stored_url,
        )

        self.assertEqual(urlsplit(updated_url).password, "retained-password")
        self.assertEqual(urlsplit(updated_url).hostname, "camera-new.local")
        self.assertEqual(urlsplit(updated_url).query, "tcp=1")

    def test_invalid_rtsp_urls_are_rejected_before_save(self):
        for value in ("http://camera.local/live", "rtsp:///live", "rtsp://camera.local"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                main._validate_rtsp_url(value)

    def test_rtsp_url_with_whitespace_is_rejected(self):
        with self.assertRaises(ValueError):
            main._validate_rtsp_url(" rtsp://camera.local/live ")


if __name__ == "__main__":
    unittest.main()
