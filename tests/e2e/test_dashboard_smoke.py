import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import config
from app.main import app
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class DashboardUnauthenticatedSmokeTests(unittest.TestCase):
    def test_dashboard_loads_signin_shell_without_exposing_camera_api(self):
        with patch.object(config, "AUTH_ENABLED", True):
            client = TestClient(app, raise_server_exceptions=False)
            root = client.get("/")
            api = client.get("/api/cameras")
            auth_state = client.get("/api/auth/me")

        self.assertEqual(root.status_code, 200)
        self.assertIn("loginForm", root.text)
        self.assertEqual(api.status_code, 401)
        self.assertEqual(auth_state.status_code, 200)
        self.assertFalse(auth_state.json()["authenticated"])

    def test_incident_time_filter_and_cleanup_controls_are_present(self):
        markup = (PROJECT_ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
        self.assertIn('value="2" selected>LAST 2 DAYS', markup)
        self.assertIn('value="all">ALL HISTORY', markup)
        self.assertIn("CLEAN OLDER THAN 2 DAYS", markup)


if __name__ == "__main__":
    unittest.main()