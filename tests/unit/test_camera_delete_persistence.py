import unittest
from unittest.mock import AsyncMock, patch

from app import db


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _Connection:
    def __init__(self):
        self.execute = AsyncMock(side_effect=["INSERT 0 1", "DELETE 1"])
        self.fetchrow = AsyncMock(return_value={
            "camera_id": "CAM07",
            "name": "CAM07",
            "rtsp_url": "rtsp://camera-host/Streaming/Channels/701",
        })

    def transaction(self):
        return _Transaction()


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


class CameraDeletePersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_delete_persists_seed_exclusion_atomically(self):
        connection = _Connection()
        with patch.object(db, "get_pool", return_value=_Pool(connection)):
            deleted = await db.delete_camera("CAM07")

        self.assertTrue(deleted)
        exclusion_query, exclusion_id = connection.execute.await_args_list[0].args
        delete_query, deleted_id = connection.execute.await_args_list[1].args
        self.assertIn("INSERT INTO camera_seed_exclusions", exclusion_query)
        self.assertIn("DELETE FROM cameras", delete_query)
        self.assertEqual(exclusion_id, "CAM07")
        self.assertEqual(deleted_id, "CAM07")

    async def test_explicit_camera_create_clears_previous_exclusion(self):
        connection = _Connection()
        with patch.object(db, "get_pool", return_value=_Pool(connection)):
            camera = await db.create_camera(
                "CAM07", "CAM07", "rtsp://camera-host/Streaming/Channels/701",
                None, ["helmet", "vest"],
            )

        self.assertEqual(camera["camera_id"], "CAM07")
        self.assertIn("INSERT INTO cameras", connection.fetchrow.await_args.args[0])
        clear_query, clear_id = connection.execute.await_args.args
        self.assertIn("DELETE FROM camera_seed_exclusions", clear_query)
        self.assertEqual(clear_id, "CAM07")


if __name__ == "__main__":
    unittest.main()
