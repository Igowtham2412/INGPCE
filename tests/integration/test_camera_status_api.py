import unittest
from datetime import timezone
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app import config
from app.main import app


class CameraStatusFilterApiTests(unittest.TestCase):
    def setUp(self):
        self.auth_patch = patch.object(config, "AUTH_ENABLED", False)
        self.auth_patch.start()
        self.addCleanup(self.auth_patch.stop)
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_combined_filters_are_forwarded_as_typed_values(self):
        with patch(
            "app.main.db.get_camera_status_history",
            AsyncMock(return_value={
                "cameras": [], "previous": [], "events": [],
                "previous_samples": [], "samples": [],
            }),
        ) as get_history:
            response = self.client.get(
                "/api/camera-status/history?window=24h&area_group_id=12&camera_id=CAM01",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["cameras"], [])
        start, end, group_id, camera_id = get_history.await_args.args
        self.assertEqual(group_id, 12)
        self.assertEqual(camera_id, "CAM01")
        self.assertEqual(start.tzinfo, timezone.utc)
        self.assertEqual(end.tzinfo, timezone.utc)

    def test_all_values_are_forwarded_as_none(self):
        with patch(
            "app.main.db.get_camera_status_history",
            AsyncMock(return_value={
                "cameras": [], "previous": [], "events": [],
                "previous_samples": [], "samples": [],
            }),
        ) as get_history:
            response = self.client.get("/api/camera-status/history?window=1h")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(get_history.await_args.args[2:], (None, None))

    def test_database_error_returns_service_unavailable(self):
        with patch(
            "app.main.db.get_camera_status_history",
            AsyncMock(side_effect=RuntimeError("database details")),
        ):
            response = self.client.get("/api/camera-status/history?window=24h")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["detail"],
            "Could not load camera status history",
        )
        self.assertNotIn("database details", response.text)


if __name__ == "__main__":
    unittest.main()
