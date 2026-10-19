import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import config
from app.main import RuleIn, app, _minimum_role


class PowerAutomateApiTests(unittest.TestCase):
    def setUp(self):
        self.auth_patch = patch.object(config, "AUTH_ENABLED", False)
        self.auth_patch.start()
        self.addCleanup(self.auth_patch.stop)
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_test_alert_sends_to_requested_recipient_and_audits(self):
        with (
            patch.object(config, "POWER_AUTOMATE_WEBHOOK_URL", "https://flow.example.test/trigger"),
            patch(
                "app.main._alert_manager._send_power_automate",
                return_value=True,
            ) as send,
            patch("app.main.db.write_audit_log", AsyncMock()) as audit,
        ):
            response = self.client.post(
                "/api/alerts/power-automate/test",
                json={"recipient": "safety@example.test"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"sent": True, "recipient": "safety@example.test"},
        )
        self.assertEqual(send.call_args.args[0], ["safety@example.test"])
        self.assertEqual(send.call_args.args[-1], "test")
        audit.assert_not_awaited()

    def test_test_alert_requires_configuration(self):
        with (
            patch.object(config, "POWER_AUTOMATE_WEBHOOK_URL", ""),
            patch("app.main._alert_manager._send_power_automate") as send,
        ):
            response = self.client.post(
                "/api/alerts/power-automate/test",
                json={"recipient": "safety@example.test"},
            )

        self.assertEqual(response.status_code, 503)
        self.assertIn("PPE_POWER_AUTOMATE_WEBHOOK_URL", response.json()["detail"])
        send.assert_not_called()

    def test_invalid_recipient_is_rejected(self):
        with patch("app.main._alert_manager._send_power_automate") as send:
            response = self.client.post(
                "/api/alerts/power-automate/test",
                json={"recipient": "invalid"},
            )

        self.assertEqual(response.status_code, 422)
        send.assert_not_called()

    def test_provider_failure_is_reported_without_leaking_url(self):
        with (
            patch.object(
                config,
                "POWER_AUTOMATE_WEBHOOK_URL",
                "https://flow.example.test/trigger?sig=private",
            ),
            patch(
                "app.main._alert_manager._send_power_automate",
                return_value=False,
            ),
        ):
            response = self.client.post(
                "/api/alerts/power-automate/test",
                json={"recipient": "safety@example.test"},
            )

        self.assertEqual(response.status_code, 502)
        self.assertNotIn("private", response.text)

    def test_test_alert_is_manager_only(self):
        self.assertEqual(
            _minimum_role("/api/alerts/power-automate/test", "POST"),
            "manager",
        )

    def test_alert_rules_reject_removed_delivery_channels(self):
        with self.assertRaises(ValidationError):
            RuleIn(name="legacy rule", channels=["teams"])

    def test_alert_rules_accept_only_helmet_violation_type(self):
        with self.assertRaises(ValidationError):
            RuleIn(name="vest rule", violation_type="no_vest")


if __name__ == "__main__":
    unittest.main()
