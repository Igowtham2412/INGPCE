import json
import unittest
from unittest.mock import AsyncMock, patch

from app import db


class _Acquire:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *_args):
        return False


class _Pool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return _Acquire(self.connection)


class CameraSpeedTrackingPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_settings_are_saved_as_json_and_read_as_points(self):
        settings_row = {
            "camera_id": "CAM12",
            "gate_a": json.dumps([[0.1, 0.25], [0.9, 0.25]]),
            "gate_b": json.dumps([[0.1, 0.75], [0.9, 0.75]]),
            "distance_m": 8,
            "speed_limit_kmh": 15,
            "enabled": True,
        }
        connection = type("Connection", (), {})()
        connection.fetchrow = AsyncMock(return_value=settings_row)
        with patch.object(db, "get_pool", return_value=_Pool(connection)):
            result = await db.get_camera_speed_settings("CAM12")

        self.assertEqual(result["gate_a"], [[0.1, 0.25], [0.9, 0.25]])
        self.assertEqual(result["gate_b"], [[0.1, 0.75], [0.9, 0.75]])

    async def test_speed_measurement_persists_timestamp_and_alert_state(self):
        connection = type("Connection", (), {})()
        connection.fetchrow = AsyncMock(return_value={
            "id": 5,
            "camera_id": "CAM12",
            "track_id": 9,
            "vehicle_type": "car",
            "speed_kmh": 18.2,
            "speed_limit_kmh": 15,
            "speeding": True,
            "direction": "A-to-B",
        })
        with patch.object(db, "get_pool", return_value=_Pool(connection)):
            result = await db.record_vehicle_speed_measurement(
                "CAM12", 9, "car", 18.2, 15, True, "A-to-B", 1000,
            )

        self.assertTrue(result["speeding"])
        query = connection.fetchrow.await_args.args[0]
        self.assertIn("INSERT INTO vehicle_speed_measurements", query)
        self.assertEqual(
            connection.fetchrow.await_args.args[-1].timestamp(),
            1000,
        )

    async def test_speed_measurement_evidence_path_is_updated_by_camera_and_id(self):
        connection = type("Connection", (), {})()
        connection.execute = AsyncMock(return_value="UPDATE 1")
        with patch.object(db, "get_pool", return_value=_Pool(connection)):
            updated = await db.update_vehicle_speed_evidence(
                "CAM12", 5, snapshot_path="snapshots/speeding/test.jpg",
            )

        self.assertTrue(updated)
        query = connection.execute.await_args.args[0]
        self.assertIn("WHERE id = $1 AND camera_id = $2", query)
        self.assertEqual(
            connection.execute.await_args.args[1:],
            (5, "CAM12", "snapshots/speeding/test.jpg"),
        )


if __name__ == "__main__":
    unittest.main()
