import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.cameras.camera_manager import CameraManager


class CameraManagerRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_refresh_restarts_only_the_requested_camera(self):
        class FakeStream:
            def __init__(self):
                self.stopped = False

            def start(self):
                return self

            def stop(self):
                self.stopped = True

        camera = {
            "camera_id": "CAM01", "enabled": True,
            "rtsp_url": "rtsp://camera.local/live",
            "main_stream_url": "rtsp://camera.local/main",
            "sub_stream_url": "rtsp://camera.local/sub",
        }
        manager = CameraManager(lambda: None, None)

        async def idle_worker(_camera, _stream):
            await asyncio.Event().wait()

        manager._camera_worker = idle_worker
        edited_stream = FakeStream()
        other_stream = FakeStream()
        edited_task = asyncio.create_task(idle_worker(camera, edited_stream))
        other_camera = {"camera_id": "CAM02", "enabled": True}
        other_task = asyncio.create_task(idle_worker(other_camera, other_stream))
        manager._streams.update({
            "CAM01": edited_stream,
            "CAM02": other_stream,
        })
        manager._tasks.update({
            "CAM01": edited_task,
            "CAM02": other_task,
        })
        replacement_stream = FakeStream()

        try:
            with (
                patch(
                    "app.cameras.camera_manager.db.get_camera",
                    new=AsyncMock(return_value=camera),
                ),
                patch(
                    "app.cameras.camera_manager.db.delete_camera_status",
                    new=AsyncMock(),
                ),
                patch(
                    "app.cameras.camera_manager.RTSPStream",
                    return_value=replacement_stream,
                ) as stream_factory,
            ):
                self.assertTrue(await manager.refresh_camera("CAM01"))

            self.assertTrue(edited_stream.stopped)
            self.assertIs(manager._streams["CAM01"], replacement_stream)
            self.assertIs(manager._tasks["CAM02"], other_task)
            self.assertIs(manager._streams["CAM02"], other_stream)
            self.assertFalse(other_stream.stopped)
            stream_factory.assert_called_once()
            self.assertEqual(
                stream_factory.call_args.kwargs["fallback_url"],
                camera["main_stream_url"],
            )
        finally:
            for task in manager._tasks.values():
                task.cancel()
            await asyncio.gather(*manager._tasks.values(), return_exceptions=True)

    async def test_settings_reload_keeps_live_stream_running(self):
        camera = {"camera_id": "CAM01", "enabled": True}
        stream = object()
        manager = CameraManager(lambda: None, None)

        async def idle_worker(_camera, _stream):
            await asyncio.Event().wait()

        manager._camera_worker = idle_worker
        old_task = asyncio.create_task(idle_worker(camera, stream))
        manager._streams["CAM01"] = stream
        manager._tasks["CAM01"] = old_task
        manager._latest_statuses["CAM01"] = ["stale"]
        manager._detection_metrics["CAM01"] = {"detection_fps": 1}

        with patch(
            "app.cameras.camera_manager.db.get_camera",
            new=AsyncMock(return_value=camera),
        ), patch(
            "app.cameras.camera_manager.db.delete_camera_status",
            new=AsyncMock(),
        ) as delete_status:
            self.assertTrue(
                await manager.refresh_camera("CAM01", restart_stream=False),
            )
            new_task = manager._tasks["CAM01"]
            self.assertIsNot(new_task, old_task)
            self.assertIs(manager._streams["CAM01"], stream)
            self.assertEqual(manager._latest_statuses, {})
            self.assertEqual(manager._detection_metrics, {})
            delete_status.assert_not_awaited()

        new_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await new_task


if __name__ == "__main__":
    unittest.main()
