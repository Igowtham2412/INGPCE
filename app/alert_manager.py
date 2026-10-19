"""Dispatches Power Automate email alerts for matching violation rules."""
import time
import logging
import asyncio
import json
import requests
import base64
from typing import Optional, List
from urllib.parse import urlsplit

from . import config, db
from .rules_engine import RulesEngine, ViolationEvent, MatchedAlert

logger = logging.getLogger("ppe.alerts")

_VIOLATION_LABELS = {
    "no_helmet": "Missing Helmet", "no_vest": "Missing Safety Vest",
    "no_gloves": "Missing Gloves", "no_boots": "Missing Safety Boots",
    "no_goggles": "Missing Goggles", "no_mask": "Missing Mask",
}


class AlertManager:
    def __init__(self):
        self.rules_engine = RulesEngine()
        self._worker_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._worker_task and not self._worker_task.done():
            return
        self._stop_event.clear()
        self._worker_task = asyncio.create_task(
            self._run_outbox(), name="alert-outbox-worker",
        )

    async def stop(self) -> None:
        if not self._worker_task:
            return
        self._stop_event.set()
        await self._worker_task
        self._worker_task = None

    async def _run_outbox(self) -> None:
        while not self._stop_event.is_set():
            try:
                job = await db.claim_alert_job()
                if job is None:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=config.ALERT_OUTBOX_POLL_SECONDS,
                    )
                    continue
                await self._deliver_job(job)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Alert outbox worker failed; retrying")
                await asyncio.sleep(config.ALERT_OUTBOX_POLL_SECONDS)

    async def _deliver_job(self, job) -> None:
        payload = job["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        violation_type = payload.get("violation_type")
        if violation_type is None and payload.get("label") == _VIOLATION_LABELS["no_helmet"]:
            violation_type = "no_helmet"
        if violation_type != "no_helmet":
            await db.finish_alert_job(
                job_id=job["id"],
                violation_id=job["violation_id"],
                rule_id=job["rule_id"],
                channel=job["channel"],
                success=False,
                attempt_count=config.ALERT_OUTBOX_MAX_ATTEMPTS,
                max_attempts=config.ALERT_OUTBOX_MAX_ATTEMPTS,
                error="Suppressed: Power Automate sends helmet violations only",
            )
            return
        success = False
        error = None
        try:
            success = await self._dispatch(
                job["channel"],
                payload["camera_name"],
                payload["zone"],
                payload["label"],
                payload["email_recipients"],
                payload.get("snapshot_path"),
                violation_type,
            )
            if not success:
                error = "Provider returned an unsuccessful delivery result"
        except Exception as exc:
            error = str(exc)
            logger.exception(
                "Alert delivery failed for outbox job %s", job["id"],
            )
        await db.finish_alert_job(
            job_id=job["id"],
            violation_id=job["violation_id"],
            rule_id=job["rule_id"],
            channel=job["channel"],
            success=success,
            attempt_count=job["attempt_count"] + 1,
            max_attempts=config.ALERT_OUTBOX_MAX_ATTEMPTS,
            error=error,
        )

    async def handle_violation(self, violation_id: int, camera_id: str, camera_name: str,
                                zone: str, track_id: int, violation_type: str,
                                snapshot_path: Optional[str] = None) -> List[MatchedAlert]:
        if not config.ALERTS_ENABLED or violation_type != "no_helmet":
            return []

        event = ViolationEvent(camera_id, camera_name, zone, track_id, violation_type)
        matches = await self.rules_engine.evaluate(event)
        if not matches:
            return []

        label = _VIOLATION_LABELS.get(violation_type, violation_type)
        for match in matches:
            for channel in match.channels:
                try:
                    await db.enqueue_alert_job(
                        violation_id=violation_id,
                        rule_id=match.rule_id,
                        channel=channel,
                        payload={
                            "camera_name": camera_name,
                            "zone": zone,
                            "label": label,
                            "violation_type": violation_type,
                            "email_recipients": match.email_recipients,
                            "snapshot_path": snapshot_path,
                        },
                    )
                except Exception:
                    logger.exception(
                        "Could not enqueue alert for rule=%s channel=%s "
                        "(violation still saved)",
                        match.rule_id, channel,
                    )
        return matches

    async def _dispatch(
        self,
        channel: str,
        camera_name: str,
        zone: str,
        label: str,
        recipients: List[str],
        snapshot_path: Optional[str],
        violation_type: Optional[str] = None,
    ) -> bool:
        if channel != "power_automate":
            logger.warning("Unsupported alert channel '%s'", channel)
            return False
        if violation_type != "no_helmet":
            logger.warning("Power Automate only delivers helmet violation alerts")
            return False
        if not recipients:
            logger.warning("Power Automate alert matched but no recipients configured")
            return False
        if not snapshot_path:
            logger.error("Helmet violation alert has no evidence image")
            return False
        occurred_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        body = (
            "Helmet Violation Detected\n"
            f"Camera: {camera_name}\n"
            f"Zone: {zone}\n"
            f"Violation: {label}\n"
            f"Time: {occurred_at}"
        )
        return await asyncio.to_thread(
            self._send_power_automate,
            recipients,
            f"PPE Compliance Violation: {label}",
            body,
            camera_name,
            zone,
            label,
            occurred_at,
            "ppe_violation",
            snapshot_path,
            violation_type,
        )

    def _send_power_automate(
        self,
        recipients: List[str],
        subject: str,
        body: str,
        camera_name: str,
        zone: str,
        label: str,
        occurred_at: str,
        event_type: str = "ppe_violation",
        snapshot_path: Optional[str] = None,
        violation_type: Optional[str] = None,
    ) -> bool:
        webhook_url = config.POWER_AUTOMATE_WEBHOOK_URL
        if not webhook_url:
            logger.warning(
                "Power Automate channel matched but "
                "PPE_POWER_AUTOMATE_WEBHOOK_URL is not configured",
            )
            return False
        parsed_url = urlsplit(webhook_url)
        if parsed_url.scheme != "https" or not parsed_url.netloc:
            logger.error(
                "Power Automate webhook URL must be a valid HTTPS URL",
            )
            return False

        payload = {
            "event_type": event_type,
            "subject": subject,
            "body": body,
            "camera_name": camera_name,
            "zone": zone,
            "violation": label,
            "occurred_at": occurred_at,
            "recipients": recipients,
        }
        if event_type == "ppe_violation":
            if violation_type != "no_helmet":
                logger.warning("Power Automate only delivers helmet violation alerts")
                return False
            if not snapshot_path:
                logger.error("Helmet violation alert has no evidence image")
                return False
            try:
                with open(snapshot_path, "rb") as image_file:
                    image_bytes = image_file.read()
            except OSError as exc:
                logger.error(
                    "Could not read helmet violation evidence image (%s)",
                    type(exc).__name__,
                )
                return False
            if not image_bytes:
                logger.error("Helmet violation evidence image is empty")
                return False
            payload.update({
                "image_name": "helmet_violation.jpg",
                "image_content_type": "image/jpeg",
                "image_content_bytes": base64.b64encode(image_bytes).decode("ascii"),
            })

        try:
            response = requests.post(
                webhook_url,
                json=payload,
                timeout=(5, 30),
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            logger.error(
                "Power Automate webhook request failed (%s)",
                type(exc).__name__,
            )
            return False

        if not 200 <= response.status_code < 300:
            logger.error(
                "Power Automate webhook returned HTTP %s",
                response.status_code,
            )
            return False
        return True
