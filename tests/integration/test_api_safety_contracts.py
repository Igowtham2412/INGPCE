import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app import config
from app.main import app


class ApiSafetyContractsTests(unittest.TestCase):
    def setUp(self):
        self.auth_patch = patch.object(config, "AUTH_ENABLED", False)
        self.auth_patch.start()
        self.addCleanup(self.auth_patch.stop)
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_health_and_root_include_expected_security_headers(self):
        health = self.client.get("/api/health")
        root = self.client.get("/")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["status"], "ok")
        self.assertEqual(root.status_code, 200)
        self.assertEqual(root.headers["x-content-type-options"], "nosniff")
        self.assertEqual(root.headers["x-frame-options"], "DENY")
        self.assertEqual(root.headers["referrer-policy"], "same-origin")

    def test_database_failure_is_logged_but_not_reflected_to_client(self):
        with (
            patch(
                "app.main.db.list_cameras",
                AsyncMock(side_effect=RuntimeError("password=should-not-leak")),
            ),
            patch("app.main.db.get_camera_statuses", AsyncMock(return_value=[])),
        ):
            response = self.client.get("/api/cameras")

        self.assertEqual(response.status_code, 503)
        self.assertNotIn("should-not-leak", response.text)
        self.assertNotIn("Traceback", response.text)

    def test_invalid_values_and_unbounded_limits_are_rejected(self):
        zone = self.client.post(
            "/api/cameras/CAM01/zones",
            json={"name": "entry", "polygon": [[0, 0], [1, 1]]},
        )
        status = self.client.get("/api/camera-status/history?window=forever")
        violations = self.client.get("/api/violations?limit=1000000")
        stats = self.client.get("/api/stats?window_hours=1000000")
        self.assertEqual(zone.status_code, 422)
        self.assertEqual(status.status_code, 422)
        self.assertEqual(violations.status_code, 422)
        self.assertEqual(stats.status_code, 422)

    def test_violation_filter_is_bound_and_missing_evidence_is_not_found(self):
        with (
            patch(
                "app.main.db.get_recent_violations",
                AsyncMock(return_value=[]),
            ) as get_recent,
            patch("app.main.db.get_violation_by_id", AsyncMock(return_value=None)),
        ):
            incidents = self.client.get(
                "/api/violations?since_epoch=1800000000&camera_id=CAM01&limit=20",
            )
            evidence = self.client.get("/api/violations/123/evidence/clean")

        self.assertEqual(incidents.status_code, 200)
        get_recent.assert_awaited_once_with(
            20, "CAM01", None, None, 1800000000.0,
        )
        self.assertEqual(evidence.status_code, 404)

    def test_protected_delete_roles_are_not_open_for_viewers(self):
        from app.main import _minimum_role
        self.assertEqual(_minimum_role("/api/violations/cleanup", "POST"), "admin")
        self.assertEqual(_minimum_role("/api/auth/users/4", "DELETE"), "admin")


if __name__ == "__main__":
    unittest.main()