import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import config
from app.alert_manager import AlertManager
from app.rules_engine import MatchedAlert


class FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


class PowerAutomateAlertTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.manager = AlertManager()

    def test_sends_incident_details_and_recipients_to_https_flow(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "helmet.jpg"
            image_path.write_bytes(b"test-jpeg-evidence")
            with (
                patch.object(
                    config,
                    "POWER_AUTOMATE_WEBHOOK_URL",
                    "https://flow.example.test/trigger?sig=private",
                ),
                patch(
                    "app.alert_manager.requests.post",
                    return_value=FakeResponse(200),
                ) as post,
            ):
                delivered = self.manager._send_power_automate(
                    ["safety@example.test"],
                    "PPE Compliance Violation: Missing Helmet",
                    "Camera: CAM01",
                    "CAM01",
                    "Loading Bay",
                    "Missing Helmet",
                    "2026-10-15 10:00:00 IST",
                    "ppe_violation",
                    str(image_path),
                    "no_helmet",
                )

        self.assertTrue(delivered)
        args, kwargs = post.call_args
        self.assertEqual(
            args[0],
            "https://flow.example.test/trigger?sig=private",
        )
        self.assertEqual(kwargs["json"]["event_type"], "ppe_violation")
        self.assertEqual(kwargs["json"]["camera_name"], "CAM01")
        self.assertEqual(kwargs["json"]["zone"], "Loading Bay")
        self.assertEqual(kwargs["json"]["violation"], "Missing Helmet")
        self.assertEqual(kwargs["json"]["recipients"], ["safety@example.test"])
        self.assertEqual(kwargs["json"]["image_name"], "helmet_violation.jpg")
        self.assertEqual(kwargs["json"]["image_content_type"], "image/jpeg")
        self.assertEqual(
            base64.b64decode(kwargs["json"]["image_content_bytes"]),
            b"test-jpeg-evidence",
        )
        self.assertEqual(kwargs["timeout"], (5, 30))

    def test_rejects_http_webhook_urls(self):
        with (
            patch.object(
                config,
                "POWER_AUTOMATE_WEBHOOK_URL",
                "http://flow.example.test/trigger?sig=private",
            ),
            patch("app.alert_manager.requests.post") as post,
        ):
            delivered = self.manager._send_power_automate(
                ["safety@example.test"], "Subject", "Body",
                "CAM01", "Loading Bay", "Missing Helmet", "now",
            )

        self.assertFalse(delivered)
        post.assert_not_called()

    def test_non_success_response_is_reported_as_failed_delivery(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "helmet.jpg"
            image_path.write_bytes(b"test-jpeg-evidence")
            with (
                patch.object(
                    config,
                    "POWER_AUTOMATE_WEBHOOK_URL",
                    "https://flow.example.test/trigger?sig=private",
                ),
                patch(
                    "app.alert_manager.requests.post",
                    return_value=FakeResponse(401),
                ),
            ):
                delivered = self.manager._send_power_automate(
                    ["safety@example.test"], "Subject", "Body",
                    "CAM01", "Loading Bay", "Missing Helmet", "now",
                    "ppe_violation", str(image_path), "no_helmet",
                )

        self.assertFalse(delivered)

    async def test_dispatch_passes_rule_recipients_to_provider(self):
        with (
            patch.object(
                self.manager,
                "_send_power_automate",
                return_value=True,
            ) as send,
        ):
            delivered = await self.manager._dispatch(
                "power_automate",
                "CAM01",
                "Loading Bay",
                "Missing Helmet",
                ["safety@example.test"],
                "C:\\evidence\\helmet.jpg",
                "no_helmet",
            )

        self.assertTrue(delivered)
        self.assertEqual(send.call_args.args[0], ["safety@example.test"])
        self.assertEqual(send.call_args.args[7], "ppe_violation")
        self.assertEqual(send.call_args.args[8], "C:\\evidence\\helmet.jpg")
        self.assertEqual(send.call_args.args[9], "no_helmet")

    async def test_dispatch_rejects_non_helmet_violations(self):
        with patch.object(self.manager, "_send_power_automate") as send:
            delivered = await self.manager._dispatch(
                "power_automate",
                "CAM01",
                "Loading Bay",
                "Missing Safety Vest",
                ["safety@example.test"],
                "C:\\evidence\\vest.jpg",
                "no_vest",
            )

        self.assertFalse(delivered)
        send.assert_not_called()

    async def test_dispatch_requires_helmet_image(self):
        with patch.object(self.manager, "_send_power_automate") as send:
            delivered = await self.manager._dispatch(
                "power_automate",
                "CAM01",
                "Loading Bay",
                "Missing Helmet",
                ["safety@example.test"],
                None,
                "no_helmet",
            )

        self.assertFalse(delivered)
        send.assert_not_called()

    async def test_handle_violation_queues_only_helmet_events(self):
        match = MatchedAlert(1, "Helmet rule", ["power_automate"], ["safety@example.test"])
        with (
            patch.object(self.manager.rules_engine, "evaluate", AsyncMock(return_value=[match])) as evaluate,
            patch("app.alert_manager.db.enqueue_alert_job", AsyncMock()) as enqueue,
        ):
            queued = await self.manager.handle_violation(
                10, "CAM01", "CAM01", "Loading Bay", 3, "no_helmet",
                "C:\\evidence\\helmet.jpg",
            )
            self.assertEqual(queued, [match])
            evaluate.assert_awaited_once()
            enqueue.assert_awaited_once()
            self.assertEqual(
                enqueue.await_args.kwargs["payload"]["violation_type"],
                "no_helmet",
            )
            enqueue.reset_mock()
            evaluate.reset_mock()

            queued = await self.manager.handle_violation(
                11, "CAM01", "CAM01", "Loading Bay", 3, "no_vest",
                "C:\\evidence\\vest.jpg",
            )

        self.assertEqual(queued, [])
        evaluate.assert_not_awaited()
        enqueue.assert_not_awaited()

    async def test_legacy_channels_are_not_dispatched(self):
        delivered = await self.manager._dispatch(
            "teams",
            "CAM01",
            "Loading Bay",
            "Missing Helmet",
            ["safety@example.test"],
            None,
        )

        self.assertFalse(delivered)


if __name__ == "__main__":
    unittest.main()
