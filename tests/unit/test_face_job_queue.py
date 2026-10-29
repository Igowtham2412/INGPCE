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
        connection.fetchrow = AsyncMock(return_value={
            "violation_type": "no_helmet", "has_evidence": True,
        })
        with patch.object(db, "get_pool", return_value=_Pool(connection)), patch.object(
            db.config, "FACE_JOB_QUEUE_MAX", 1,
        ):
            queued = await db.enqueue_face_recognition_job(42)

        self.assertFalse(queued)
        connection.execute.assert_awaited_once()
        connection.fetchrow.assert_awaited_once()
        self.assertIn(
            "SELECT count(*) FROM face_recognition_jobs",
            connection.fetchval.await_args_list[1].args[0],
        )

    async def test_rerun_is_available_without_reviewing_incident_first(self):
        connection = _Connection()
        connection.fetchrow = AsyncMock(return_value={
            "violation_type": "no_helmet",
            "has_evidence": True,
        })
        with patch.object(db, "get_pool", return_value=_Pool(connection)):
            queued = await db.requeue_face_recognition_job(42)

        self.assertTrue(queued)
        query = connection.fetchrow.await_args.args[0]
        self.assertNotIn("review_status", query)
        self.assertIn("clean_snapshot_path IS NOT NULL OR snapshot_path IS NOT NULL", query)
        self.assertEqual(connection.execute.await_count, 2)

    async def test_rerun_rejects_ineligible_or_evidence_free_incident(self):
        for incident in (
            {"violation_type": "no_vest", "has_evidence": True},
            {"violation_type": "no_helmet", "has_evidence": False},
            None,
        ):
            connection = _Connection()
            connection.fetchrow = AsyncMock(return_value=incident)
            with patch.object(db, "get_pool", return_value=_Pool(connection)):
                queued = await db.requeue_face_recognition_job(42)

            self.assertFalse(queued)
            connection.execute.assert_not_awaited()


class FaceJobResultPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_complete_result_supplies_required_violation_type(self):
        connection = _Connection()
        connection.fetchrow = AsyncMock(return_value=None)
        with patch.object(db, "get_pool", return_value=_Pool(connection)):
            await db.complete_face_recognition_job(
                42, "UNKNOWN", None, 0.1, 0.0, {}, None, [],
                "unit-model", "unit-version",
            )

        insert = connection.execute.await_args_list[0].args[0]
        self.assertIn("violation_type", insert)
        self.assertIn(
            "(SELECT violation_type FROM violations WHERE id = $1)",
            insert,
        )

    async def test_failed_result_supplies_required_violation_type(self):
        connection = _Connection()
        connection.fetchrow = AsyncMock(return_value=None)
        with patch.object(db, "get_pool", return_value=_Pool(connection)):
            await db.fail_face_recognition_job(42, "unit_error")

        insert = connection.execute.await_args_list[0].args[0]
        self.assertIn("violation_type", insert)
        self.assertIn(
            "(SELECT violation_type FROM violations WHERE id = $1)",
            insert,
        )
