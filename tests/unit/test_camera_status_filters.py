import unittest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from app.db import get_camera_status_history


class CameraStatusFilterQueryTests(unittest.IsolatedAsyncioTestCase):
    async def test_group_and_string_camera_filters_reach_every_history_query(self):
        connection = AsyncMock()
        connection.execute = AsyncMock()
        connection.fetch = AsyncMock(return_value=[])
        pool = MagicMock()
        pool.acquire.return_value.__aenter__.return_value = connection

        with patch("app.db.get_pool", return_value=pool):
            await get_camera_status_history(
                start=datetime(2026, 10, 10, tzinfo=UTC),
                end=datetime(2026, 10, 11, tzinfo=UTC),
                area_group_id=12,
                camera_id="CAM01",
            )

        camera_query, *camera_params = connection.fetch.await_args_list[0].args
        self.assertIn("camera.area_group_id = $1", camera_query)
        self.assertIn("camera.camera_id = $2", camera_query)
        self.assertEqual(camera_params, [12, "CAM01"])
        previous_query, *previous_params = connection.fetch.await_args_list[1].args
        self.assertIn("camera_id = $3", previous_query)
        self.assertEqual(previous_params, [
            datetime(2026, 10, 10, tzinfo=UTC), 12, "CAM01",
        ])

        events_query, *events_params = connection.fetch.await_args_list[2].args
        self.assertIn("camera_id = $4", events_query)
        self.assertEqual(events_params, [
            datetime(2026, 10, 10, tzinfo=UTC),
            datetime(2026, 10, 11, tzinfo=UTC), 12, "CAM01",
        ])

        previous_samples_query, *previous_samples_params = (
            connection.fetch.await_args_list[3].args
        )
        self.assertIn("camera_id = $3", previous_samples_query)
        self.assertEqual(previous_samples_params, [
            datetime(2026, 10, 10, tzinfo=UTC), 12, "CAM01",
        ])

        samples_query, *samples_params = connection.fetch.await_args_list[4].args
        self.assertIn("camera_id = $4", samples_query)
        self.assertEqual(samples_params, [
            datetime(2026, 10, 10, tzinfo=UTC),
            datetime(2026, 10, 11, tzinfo=UTC), 12, "CAM01",
        ])


if __name__ == "__main__":
    unittest.main()
