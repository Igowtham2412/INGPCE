import unittest
from unittest.mock import AsyncMock, patch

from app.alerts.rules_engine import RulesEngine, ViolationEvent


class AlertRuleCameraScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_rule_matches_any_camera_in_its_scope(self):
        engine = RulesEngine()
        engine._rules_cache = [{
            "id": 1,
            "name": "Multi-camera helmet rule",
            "camera_ids": ["CAM01", "CAM02"],
            "camera_id": None,
            "zone": None,
            "violation_type": "no_helmet",
            "min_severity": 1,
            "cooldown_seconds": 300,
            "channels": ["power_automate"],
            "email_recipients": ["safety@example.test"],
        }]
        engine._last_refresh = 1e20

        with patch("app.alerts.rules_engine.db.list_rules", new=AsyncMock()):
            first = await engine.evaluate(
                ViolationEvent("CAM01", "Camera 1", "", 1, "no_helmet"),
            )
            second = await engine.evaluate(
                ViolationEvent("CAM02", "Camera 2", "", 2, "no_helmet"),
            )
            excluded = await engine.evaluate(
                ViolationEvent("CAM03", "Camera 3", "", 3, "no_helmet"),
            )

        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        self.assertEqual(excluded, [])

    async def test_legacy_single_camera_rule_remains_supported(self):
        engine = RulesEngine()
        engine._rules_cache = [{
            "id": 2,
            "name": "Legacy camera rule",
            "camera_id": "CAM01",
            "zone": None,
            "violation_type": "no_helmet",
            "min_severity": 1,
            "cooldown_seconds": 300,
            "channels": ["power_automate"],
            "email_recipients": [],
        }]
        engine._last_refresh = 1e20

        with patch("app.alerts.rules_engine.db.list_rules", new=AsyncMock()):
            matches = await engine.evaluate(
                ViolationEvent("CAM01", "Camera 1", "", 1, "no_helmet"),
            )
            excluded = await engine.evaluate(
                ViolationEvent("CAM02", "Camera 2", "", 2, "no_helmet"),
            )

        self.assertEqual(len(matches), 1)
        self.assertEqual(excluded, [])
