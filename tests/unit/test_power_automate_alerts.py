import asyncio
import base64
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import config
from app.alerts.alert_manager import AlertManager
from app.faces.face_recognition import FaceRecognitionWorker
from app.alerts.rules_engine import MatchedAlert


class FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


class PowerAutomateAlertTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.manager = AlertManager()

    async def test_email_payload_remains_unchanged_when_face_email_option_is_off(self):
        job = {
            "id": 1, "violation_id": 5, "rule_id": 2,
            "channel": "power_automate", "attempt_count": 0,
            "payload": {
                "camera_name": "CAM01", "zone": "Line A",
                "label": "Missing Helmet", "violation_type": "no_helmet",
                "email_recipients": ["safety@example.test"],
                "snapshot_path": "evidence.jpg",
            },
        }
        with patch.object(config, "FACE_INCLUDE_IN_EMAIL", False), patch(
            "app.alerts.alert_manager.db.get_face_match_for_email",
            new=AsyncMock(),
        ) as get_match, patch.object(
            self.manager, "_dispatch", new=AsyncMock(return_value=True),
        ) as dispatch, patch(
            "app.alerts.alert_manager.db.finish_alert_job", new=AsyncMock(),
        ):
            await self.manager._deliver_job(job)

        get_match.assert_not_awaited()
        self.assertEqual(len(dispatch.await_args.args), 7)

    async def test_email_dispatch_continues_during_slow_face_worker_failure(self):
        def slow_failure():
            time.sleep(0.15)
            raise RuntimeError("synthetic face worker failure")

        worker = FaceRecognitionWorker(analyzer_factory=slow_failure)
        face_job = {
            "incident_id": 77,
            "violation_type": "no_helmet",
            "camera_id": "CAM01",
            "track_id": 12,
            "clean_snapshot_path": "not-read-by-test",
        }
        alert_job = {
            "id": 7, "violation_id": 77, "rule_id": 3,
            "channel": "power_automate", "attempt_count": 0,
            "payload": {
                "camera_name": "CAM01", "zone": "Line A",
                "label": "Missing Helmet", "violation_type": "no_helmet",
                "email_recipients": ["safety@example.test"],
                "snapshot_path": "evidence.jpg",
            },
        }
        with patch.object(config, "FACE_INCLUDE_IN_EMAIL", False), patch.object(
            config, "FACE_MATCH_RETENTION_DAYS", 0,
        ), patch(
            "app.faces.face_recognition.db.claim_face_recognition_job",
            new=AsyncMock(return_value=None),
        ), patch(
            "app.faces.face_recognition.db.reuse_face_match_for_track",
            new=AsyncMock(return_value=None),
        ), patch(
            "app.faces.face_recognition.db.fail_face_recognition_job",
            new=AsyncMock(),
        ) as fail_face_job, patch.object(
            self.manager, "_dispatch", new=AsyncMock(return_value=True),
        ) as dispatch, patch(
            "app.alerts.alert_manager.db.finish_alert_job", new=AsyncMock(),
        ):
            await worker.start()
            try:
                pending_face_job = asyncio.create_task(
                    worker._process_job(face_job),
                )
                await asyncio.sleep(0.01)
                await self.manager._deliver_job(alert_job)

                self.assertFalse(pending_face_job.done())
                dispatch.assert_awaited_once()
                await pending_face_job
                fail_face_job.assert_awaited_once()
            finally:
                await worker.stop()

    async def test_email_adds_only_identified_match_with_pending_wording(self):
        job = {
            "id": 2, "violation_id": 6, "rule_id": 3,
            "channel": "power_automate", "attempt_count": 0,
            "payload": {
                "camera_name": "CAM02", "zone": "Line B",
                "label": "Phone Detected Near Person",
                "violation_type": "phone_detected",
                "email_recipients": ["safety@example.test"],
                "snapshot_path": "evidence.jpg",
            },
        }
        with patch.object(config, "FACE_INCLUDE_IN_EMAIL", True), patch(
            "app.alerts.alert_manager.db.get_face_match_for_email",
            new=AsyncMock(return_value={
                "full_name": "Example Person", "similarity": 0.91,
                "review_decision": None,
            }),
        ), patch.object(
            self.manager, "_dispatch", new=AsyncMock(return_value=True),
        ) as dispatch, patch(
            "app.alerts.alert_manager.db.finish_alert_job", new=AsyncMock(),
        ):
            await self.manager._deliver_job(job)

        face_match = dispatch.await_args.args[-1]
        self.assertIn("Possible match", face_match["wording"])
        self.assertIn("pending review", face_match["wording"])

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
                    "app.alerts.alert_manager.requests.post",
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
            patch("app.alerts.alert_manager.requests.post") as post,
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
                    "app.alerts.alert_manager.requests.post",
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

    async def test_phone_detection_is_sent_with_image_attachment(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "phone.jpg"
            image_path.write_bytes(b"phone-evidence")
            with (
                patch.object(
                    config,
                    "POWER_AUTOMATE_WEBHOOK_URL",
                    "https://flow.example.test/trigger?sig=private",
                ),
                patch(
                    "app.alerts.alert_manager.requests.post",
                    return_value=FakeResponse(200),
                ) as post,
            ):
                delivered = await self.manager._dispatch(
                    "power_automate",
                    "CAM01",
                    "Loading Bay",
                    "Phone Detected Near Person",
                    ["safety@example.test"],
                    str(image_path),
                    "phone_detected",
                )

        self.assertTrue(delivered)
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["violation"], "Phone Detected Near Person")
        self.assertEqual(payload["image_name"], "phone_detected.jpg")
        self.assertEqual(
            base64.b64decode(payload["image_content_bytes"]),
            b"phone-evidence",
        )

    async def test_arc_flash_suit_violation_is_sent_with_image_attachment(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "arc-suit.jpg"
            image_path.write_bytes(b"arc-suit-evidence")
            with (
                patch.object(
                    config,
                    "POWER_AUTOMATE_WEBHOOK_URL",
                    "https://flow.example.test/trigger?sig=private",
                ),
                patch(
                    "app.alerts.alert_manager.requests.post",
                    return_value=FakeResponse(200),
                ) as post,
            ):
                delivered = await self.manager._dispatch(
                    "power_automate",
                    "CAM01",
                    "Substation",
                    "Missing Arc Flash Suit",
                    ["safety@example.test"],
                    str(image_path),
                    "no_arc_flash_suit",
                )

        self.assertTrue(delivered)
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["violation"], "Missing Arc Flash Suit")
        self.assertEqual(payload["image_name"], "arc_flash_suit_violation.jpg")
        self.assertEqual(
            base64.b64decode(payload["image_content_bytes"]),
            b"arc-suit-evidence",
        )

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

    async def test_handle_violation_queues_helmet_and_phone_events_only(self):
        match = MatchedAlert(1, "Helmet rule", ["power_automate"], ["safety@example.test"])
        with (
            patch.object(self.manager.rules_engine, "evaluate", AsyncMock(return_value=[match])) as evaluate,
            patch("app.alerts.alert_manager.db.enqueue_alert_job", AsyncMock()) as enqueue,
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
                11, "CAM01", "CAM01", "Loading Bay", 3, "phone_detected",
                "C:\\evidence\\phone.jpg",
            )
            self.assertEqual(queued, [match])
            evaluate.assert_awaited_once()
            enqueue.assert_awaited_once()
            self.assertEqual(
                enqueue.await_args.kwargs["payload"]["violation_type"],
                "phone_detected",
            )
            enqueue.reset_mock()
            evaluate.reset_mock()

            queued = await self.manager.handle_violation(
                12, "CAM01", "CAM01", "Loading Bay", 3, "no_vest",
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
