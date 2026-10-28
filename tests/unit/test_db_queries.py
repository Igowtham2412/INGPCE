import unittest
from datetime import UTC
from unittest.mock import AsyncMock, MagicMock, patch

from app.db import get_camera_status_history, get_recent_violations


class RecentViolationQueryTests(unittest.IsolatedAsyncioTestCase):
    async def test_cutoff_and_filters_are_parameterized(self):
        connection = AsyncMock()
        connection.fetch = AsyncMock(return_value=[])
        pool = MagicMock()
        pool.acquire.return_value.__aenter__.return_value = connection

        with patch("app.db.get_pool", return_value=pool):
            result = await get_recent_violations(
                limit=20,
                camera_id="CAM01",
                violation_type="no_helmet",
                area_group_id=4,
                since_epoch=1_800_000_000,
            )

        self.assertEqual(result, [])
        query, *params = connection.fetch.await_args.args
        self.assertIn("epoch_time >=", query)
        self.assertIn("camera_id =", query)
        self.assertIn("violation_type = ANY", query)
        self.assertIn("area_group_id =", query)
        self.assertEqual(
            params,
            [1_800_000_000, "CAM01", ["no_helmet", "helmet"], 4, 20, 0],
        )
        self.assertNotIn("CAM01", query)


class CameraStatusHistoryQueryTests(unittest.IsolatedAsyncioTestCase):
    async def test_unfiltered_history_queries_bind_only_referenced_parameters(self):
        connection = AsyncMock()
        connection.fetch = AsyncMock(return_value=[])
        connection.execute = AsyncMock()
        pool = MagicMock()
        pool.acquire.return_value.__aenter__.return_value = connection

        from datetime import datetime
        start = datetime(2026, 10, 14, tzinfo=UTC)
        end = datetime(2026, 10, 15, tzinfo=UTC)
        with patch("app.db.get_pool", return_value=pool):
            result = await get_camera_status_history(start, end)

        self.assertEqual(result, {
            "cameras": [], "previous": [], "events": [],
            "previous_samples": [], "samples": [],
        })
        previous_query, *previous_params = connection.fetch.await_args_list[1].args
        previous_samples_query, *sample_params = connection.fetch.await_args_list[3].args
        self.assertIn("observed_at <= $1", previous_query)
        self.assertIn("$2::bigint", previous_query)
        self.assertIn("$3::text", previous_query)
        self.assertEqual(previous_params, [start, None, None])
        self.assertIn("observed_at <= $1", previous_samples_query)
        self.assertEqual(sample_params, [start, None, None])


if __name__ == "__main__":
    unittest.main()