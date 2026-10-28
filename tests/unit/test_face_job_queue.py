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
        self.execute = AsyncMock()
        self.fetchval = AsyncMock(side_effect=[None, 1])
        self.fetchrow = AsyncMock()

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


class FaceJobQueueCapacityTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_queue_defers_job_before_insert(self):
        connection = _Connection()
        with patch.object(db, "get_pool", return_value=_Pool(connection)), patch.object(
            db.config, "FACE_JOB_QUEUE_MAX", 1,
        ):
            queued = await db.enqueue_face_recognition_job(42)

        self.assertFalse(queued)
        connection.execute.assert_awaited_once()
        connection.fetchrow.assert_not_awaited()

    async def test_track_reuse_copies_only_same_camera_track_match(self):
        connection = _Connection()
        connection.fetchrow = AsyncMock(return_value={"incident_id": 9})
        with patch.object(db, "get_pool", return_value=_Pool(connection)):
            source_incident_id = await db.reuse_face_match_for_track(
                12, "CAM01", 7, 120, ["no_helmet", "no_vest", "phone_detected"],
            )

        self.assertEqual(source_incident_id, 9)
        query = connection.fetchrow.await_args.args[0]
        self.assertIn("prior.camera_id = $2", query)
        self.assertIn("prior.track_id = $3", query)
        self.assertIn("m.reused_from_track = false", query)
        self.assertIn("'No face found'", query)
        self.assertEqual(
            connection.fetchrow.await_args.args[1:],
            (12, "CAM01", 7, 120, ["no_helmet", "no_vest", "phone_detected"]),
        )
        self.assertIn("UPDATE face_recognition_jobs", connection.execute.await_args.args[0])
