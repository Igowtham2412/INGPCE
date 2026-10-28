"""
rules_engine.py — evaluates confirmed violations against DB-stored alert_rules.
"""
import logging
import time
from dataclasses import dataclass

from .. import db

logger = logging.getLogger("ppe.rules")


@dataclass
class ViolationEvent:
    camera_id: str
    camera_name: str
    zone: str
    track_id: int
    violation_type: str
    severity: int = 1


@dataclass
class MatchedAlert:
    rule_id: int
    rule_name: str
    channels: list[str]
    email_recipients: list[str]


class RulesEngine:
    def __init__(self, refresh_interval_seconds: int = 30):
        self.refresh_interval = refresh_interval_seconds
        self._rules_cache: list[dict] = []
        self._last_refresh = 0.0
        self._cooldowns: dict[tuple[int, str, str], float] = {}

    async def _refresh_if_needed(self):
        if time.time() - self._last_refresh >= self.refresh_interval or not self._rules_cache:
            try:
                self._rules_cache = await db.list_rules(enabled_only=True)
                self._last_refresh = time.time()
            except Exception as e:
                logger.warning(f"Could not refresh rules cache (using stale cache): {e}")

    async def force_refresh(self):
        self._rules_cache = await db.list_rules(enabled_only=True)
        self._last_refresh = time.time()

    @staticmethod
    def _zone_matches(rule_zone: str | None, event_zone: str) -> bool:
        if not rule_zone:
            return True
        return event_zone.lower().startswith(rule_zone.lower())

    @staticmethod
    def _type_matches(rule_type: str | None, event_type: str) -> bool:
        if not rule_type or rule_type == "*":
            return True
        return rule_type.lower() == event_type.lower()

    @staticmethod
    def _camera_matches(rule_camera: str | None, event_camera: str) -> bool:
        if not rule_camera:
            return True
        return rule_camera == event_camera

    async def evaluate(self, event: ViolationEvent) -> list[MatchedAlert]:
        await self._refresh_if_needed()
        matched: list[MatchedAlert] = []
        now = time.time()

        for rule in self._rules_cache:
            if event.severity < rule.get("min_severity", 1):
                continue
            if not self._camera_matches(rule.get("camera_id"), event.camera_id):
                continue
            if not self._zone_matches(rule.get("zone"), event.zone):
                continue
            if not self._type_matches(rule.get("violation_type"), event.violation_type):
                continue

            cooldown_key = (rule["id"], event.camera_id, event.violation_type)
            last_fired = self._cooldowns.get(cooldown_key, 0)
            if now - last_fired < rule.get("cooldown_seconds", 300):
                continue

            self._cooldowns[cooldown_key] = now
            matched.append(MatchedAlert(
                rule_id=rule["id"], rule_name=rule["name"],
                channels=list(rule.get("channels") or []),
                email_recipients=list(rule.get("email_recipients") or []),
            ))

        return matched
