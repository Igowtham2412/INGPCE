import asyncio
import unittest
from contextlib import asynccontextmanager
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import main
from app.ws_hub import hub


class _CameraManager:
    def running_camera_ids(self):
        return ["TEST-CAM"]


class LiveWallWebSocketTests(unittest.TestCase):
    def test_wall_websocket_delivers_camera_id_and_frame(self):
        @asynccontextmanager
        async def lifespan(_app):
            async def broadcast_frame():
                while not hub.has_video_subscribers("TEST-CAM"):
                    await asyncio.sleep(0.01)
                await hub.broadcast_frame("TEST-CAM", b"jpeg-frame")

            producer = asyncio.create_task(broadcast_frame())
            try:
                yield
            finally:
                await asyncio.gather(producer, return_exceptions=True)

        test_app = FastAPI(lifespan=lifespan)
        test_app.websocket("/ws/video")(main.ws_video_wall)
        previous_manager = main._camera_manager
        main._camera_manager = _CameraManager()
        try:
            with patch.object(main.config, "AUTH_ENABLED", False):
                with TestClient(test_app) as client:
                    with client.websocket_connect(
                        "/ws/video?camera_ids=TEST-CAM",
                    ) as websocket:
                        packet = websocket.receive_bytes()

            camera_id_length = int.from_bytes(packet[:2], "big")
            camera_id_end = 2 + camera_id_length
            self.assertEqual(
                packet[2:camera_id_end].decode("utf-8"), "TEST-CAM",
            )
            self.assertEqual(packet[camera_id_end:], b"jpeg-frame")
        finally:
            main._camera_manager = previous_manager


if __name__ == "__main__":
    unittest.main()
