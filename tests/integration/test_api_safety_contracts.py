import unittest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock, patch

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

    def test_rules_database_datetimes_are_json_serialized(self):
        created_at = datetime(2026, 1, 1, tzinfo=UTC)
        with patch(
            "app.main.db.list_rules",
            AsyncMock(return_value=[{"id": 7, "created_at": created_at}]),
        ):
            response = self.client.get("/api/rules")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            datetime.fromisoformat(response.json()[0]["created_at"]),
            created_at,
        )

    def test_rule_create_and_update_serialize_database_datetimes(self):
        created_at = datetime(2026, 1, 1, tzinfo=UTC)
        rule = {"id": 7, "name": "Helmet rule", "created_at": created_at}
        alert_manager = Mock()
        alert_manager.rules_engine.force_refresh = AsyncMock()

        with (
            patch("app.main.db.create_rule", AsyncMock(return_value=rule)),
            patch("app.main.db.update_rule", AsyncMock(return_value=rule)),
            patch("app.main._alert_manager", alert_manager),
        ):
            created = self.client.post("/api/rules", json={"name": "Helmet rule"})
            updated = self.client.put(
                "/api/rules/7", json={"name": "Updated helmet rule"},
            )

        self.assertEqual(created.status_code, 200)
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(
            datetime.fromisoformat(created.json()["created_at"]),
            created_at,
        )
        self.assertEqual(
            datetime.fromisoformat(updated.json()["created_at"]),
            created_at,
        )

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

    def test_invalid_camera_rtsp_url_is_rejected_without_echoing_url(self):
        with patch("app.main.db.get_camera", new=AsyncMock()) as get_camera:
            response = self.client.put(
                "/api/cameras/CAM01",
                json={"rtsp_url": "https://operator:do-not-echo@camera.example/live"},
            )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()["detail"],
            "Enter a valid RTSP URL with the rtsp:// scheme, host, and stream path",
        )
        self.assertNotIn("do-not-echo", response.text)
        get_camera.assert_not_awaited()

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