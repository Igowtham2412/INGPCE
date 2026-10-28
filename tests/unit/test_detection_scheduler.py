import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.cameras.camera_manager import CameraManager, DetectionScheduler


class DetectionSchedulerTests(unittest.TestCase):
    def test_schedules_at_configured_rate(self):
        scheduler = DetectionScheduler(5, start_time=100)

        self.assertEqual(scheduler.delay(100), 0)
        scheduler.advance(100.05)

        self.assertAlmostEqual(scheduler.delay(100.1), 0.1)

    def test_skips_missed_slots_instead_of_catching_up(self):
        scheduler = DetectionScheduler(5, start_time=100)

        scheduler.advance(101.1)

        self.assertGreater(scheduler.next_run_at, 101.1)
        self.assertAlmostEqual(scheduler.delay(101.1), 0.1)

    def test_rejects_non_positive_fps(self):
        with self.assertRaises(ValueError):
            DetectionScheduler(0)


class CameraWatchdogTests(unittest.IsolatedAsyncioTestCase):
    async def test_stale_capture_requests_reconnect_without_worker_failure(self):
        class StaleStream:
            connected = True

            def __init__(self):
                self.requested = False

            def is_stale(self, _max_age):
                return True

            def request_reconnect(self):
                self.connected = False
                self.requested = True

        manager = CameraManager(lambda: None, None)
        stream = StaleStream()
        manager._streams["CAM01"] = stream

        with patch(
            "app.cameras.camera_manager.asyncio.sleep",
            new=AsyncMock(side_effect=asyncio.CancelledError),
        ), self.assertRaises(asyncio.CancelledError):
            await manager._watchdog()

        self.assertTrue(stream.requested)
        self.assertFalse(stream.connected)


if __name__ == "__main__":
    unittest.main()
