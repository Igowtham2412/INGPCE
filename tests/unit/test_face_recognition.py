import asyncio
import os
import threading
import unittest
from unittest.mock import AsyncMock, patch

import cv2
import numpy as np

from app import config
from app.faces.face_dataset import ImageRejected, decrypt_face_crop, encrypt_embedding
from app.faces.face_recognition import (
    FaceRecognitionWorker,
    _best_face_from_crops,
    decide_face_match,
    rank_person_scores,
    violation_requires_face_recognition,
)


class FaceMatchTests(unittest.TestCase):
    def test_only_supported_violation_types_are_eligible_by_default(self):
        self.assertTrue(violation_requires_face_recognition("no_helmet"))
        self.assertTrue(violation_requires_face_recognition("phone_detected"))
        self.assertFalse(violation_requires_face_recognition("no_vest"))
        self.assertTrue(violation_requires_face_recognition("no_arc_flash_suit"))

    def test_violation_eligibility_uses_configured_types(self):
        with patch.object(config, "FACE_VIOLATION_TYPES", frozenset({"phone_detected"})):
            self.assertTrue(violation_requires_face_recognition("phone_detected"))
            self.assertFalse(violation_requires_face_recognition("no_helmet"))

    def test_face_violation_config_maps_supported_aliases_and_warns_unknown(self):
        with patch.dict(
            os.environ,
            {"PPE_FACE_VIOLATION_TYPES": "missing_helmet,missing_vest,phone_detected,bogus"},
            clear=False,
        ), self.assertLogs("ppe.config", level="WARNING") as logs:
            configured = config._face_violation_types()

        self.assertEqual(configured, frozenset({
            "no_helmet", "no_arc_flash_suit", "phone_detected",
        }))
        self.assertIn("unknown PPE_FACE_VIOLATION_TYPES", logs.output[0])

    def test_strict_match_requires_threshold_and_different_person_margin(self):
        ranked = [
            {"person_id": 1, "similarity": 0.91},
            {"person_id": 2, "similarity": 0.84},
        ]

        decision = decide_face_match(
            ranked, match_threshold=0.85, suggest_threshold=0.65,
            min_margin=0.05,
        )

        self.assertEqual(decision[0], "MATCHED")
        self.assertEqual(decision[1], 1)
        self.assertAlmostEqual(decision[3], 0.07)

        ambiguous = decide_face_match(
            [{**ranked[0], "similarity": 0.91},
             {**ranked[1], "similarity": 0.89}],
            match_threshold=0.85, suggest_threshold=0.65,
            min_margin=0.05,
        )
        self.assertEqual(ambiguous[0], "NEEDS_REVIEW")

    def test_below_suggestion_threshold_is_not_identified_or_named(self):
        decision = decide_face_match(
            [{"person_id": 1, "similarity": 0.62}],
            match_threshold=0.9, suggest_threshold=0.7, min_margin=0.05,
        )

        self.assertEqual(decision[0], "UNKNOWN")
        self.assertIsNone(decision[1])
        self.assertEqual(decision[4], [{"person_id": 1, "similarity": 0.62}])

    def test_employee_score_strategy_is_configurable(self):
        query = np.array([1.0, 0.0], dtype=np.float32)
        gallery = {
            1: {
                "employee_code": "E001", "full_name": "Person One",
                "department": "Safety",
                "embeddings": [
                    np.array([1.0, 0.0]), np.array([0.8, 0.6]),
                ],
            },
            2: {
                "employee_code": "E002", "full_name": "Person Two",
                "department": "Safety",
                "embeddings": [np.array([0.93, 0.37])],
            },
        }

        maximum = rank_person_scores(query, gallery, "max", 2)
        top_k_mean = rank_person_scores(query, gallery, "top_k_mean", 2)

        self.assertEqual(maximum[0]["person_id"], 1)
        self.assertEqual(top_k_mean[0]["person_id"], 2)

    def test_best_face_crop_and_bbox_use_the_selected_source_frame(self):
        class _Analyzer:
            def analyze(self, _image):
                return np.asarray([1.0, 0.0]), {
                    "quality_score": 0.95,
                    "bbox": [10, 20, 50, 60],
                }

        image = np.zeros((80, 90, 3), dtype=np.uint8)
        best, _rejected = _best_face_from_crops(
            _Analyzer(), [(image, "tracked_person_buffer", [100, 200, 190, 280])],
        )

        self.assertEqual(best["absolute_bbox"], [110, 220, 150, 260])
        self.assertEqual(best["face_crop"].shape, (52, 52, 3))

    def test_per_violation_quality_floor_rejects_without_lowering_global_gates(self):
        class _Analyzer:
            def analyze(self, _image):
                return np.asarray([1.0, 0.0]), {
                    "quality_score": 0.8,
                    "bbox": [10, 10, 50, 50],
                }

        image = np.zeros((80, 90, 3), dtype=np.uint8)
        best, rejected = _best_face_from_crops(
            _Analyzer(), [(image, "incident_evidence", None)],
            minimum_quality=0.85,
        )

        self.assertIsNone(best)
        self.assertEqual(rejected, {"below_violation_quality_minimum": 1})


class FaceRecognitionQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_blocking_work_uses_dedicated_face_worker_thread(self):
        worker = FaceRecognitionWorker()
        with patch("app.faces.face_recognition.config.FACE_MATCH_RETENTION_DAYS", 0), patch(
            "app.faces.face_recognition.db.claim_face_recognition_job",
            new=AsyncMock(return_value=None),
        ):
            await worker.start()
            thread_name = await worker._run_blocking(
                lambda: threading.current_thread().name,
            )
            await worker.stop()

        self.assertTrue(thread_name.startswith("ppe-face-worker"))
        self.assertIsNone(worker._blocking_executor)

    async def test_normal_frames_do_not_enqueue_recognition(self):
        worker = FaceRecognitionWorker()
        worker._running = True
        with patch("app.faces.face_recognition.config.FACE_ENABLED", True), patch(
            "app.faces.face_recognition.db.enqueue_face_recognition_job",
            new=AsyncMock(),
        ) as enqueue:
            worker.observe(
                "CAM01", np.zeros((120, 120, 3), dtype=np.uint8),
                [type("Status", (), {
                    "track_id": 7, "box": (10, 10, 100, 110),
                })()],
            )
            await asyncio.sleep(0)
            enqueue.assert_not_awaited()

            worker.enqueue_confirmed_incident(
                42, "CAM01", 7, "no_helmet",
            )
            await asyncio.gather(*worker._enqueue_tasks)
            enqueue.assert_awaited_once_with(42)
        worker._running = False

    async def test_eligible_types_queue_but_other_incidents_do_not(self):
        worker = FaceRecognitionWorker()
        worker._running = True
        with patch("app.faces.face_recognition.config.FACE_ENABLED", True), patch(
            "app.faces.face_recognition.db.enqueue_face_recognition_job",
            new=AsyncMock(return_value=True),
        ) as enqueue:
            for incident_id, violation_type in (
                (43, "no_helmet"), (46, "no_vest"), (47, "phone_detected"),
                (48, "no_arc_flash_suit"),
            ):
                worker.enqueue_confirmed_incident(
                    incident_id, "CAM01", 7, violation_type,
                )
            worker.enqueue_confirmed_incident(49, "CAM01", 7, "no_mask")
            await asyncio.gather(*worker._enqueue_tasks)
            self.assertEqual(
                [call.args[0] for call in enqueue.await_args_list],
                [43, 47, 48],
            )
        worker._running = False

    async def test_feature_disabled_never_queues_incidents(self):
        worker = FaceRecognitionWorker()
        worker._running = True
        with patch("app.faces.face_recognition.config.FACE_ENABLED", False), patch(
            "app.faces.face_recognition.db.enqueue_face_recognition_job",
            new=AsyncMock(),
        ) as enqueue:
            worker.enqueue_confirmed_incident(
                44, "CAM01", 7, "no_helmet",
            )
            await asyncio.sleep(0)
            enqueue.assert_not_awaited()
        worker._running = False

    async def test_enqueue_backpressure_does_not_wait_for_database_or_detection(self):
        worker = FaceRecognitionWorker()
        worker._running = True
        worker._enqueue_tasks = set(range(64))
        with patch("app.faces.face_recognition.config.FACE_ENABLED", True), patch(
            "app.faces.face_recognition.db.enqueue_face_recognition_job",
            new=AsyncMock(),
        ) as enqueue:
            worker.enqueue_confirmed_incident(
                45, "CAM01", 7, "no_helmet",
            )
            await asyncio.sleep(0)
            enqueue.assert_not_awaited()
        worker._running = False


class FaceRecognitionWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_unenrolled_person_is_saved_as_not_identified(self):
        class _Analyzer:
            model_name = "test-model"
            model_version = "test-version"
            runtime_provider = "CPUExecutionProvider"

            def analyze(self, _image):
                return np.asarray([1.0, 0.0], dtype=np.float32), {
                    "quality_score": 0.95,
                    "bbox": [5, 5, 25, 25],
                }

        worker = FaceRecognitionWorker(analyzer_factory=_Analyzer)
        worker._analyzer = _Analyzer()
        with patch(
            "app.faces.face_recognition.db.get_face_match_settings",
            new=AsyncMock(return_value={
                "high_threshold": 0.6, "low_threshold": 0.45,
                "minimum_margin": 0.1, "quality_min_by_violation": {},
            }),
        ), patch(
            "app.faces.face_recognition._read_person_crop",
            return_value=(np.zeros((64, 64, 3), dtype=np.uint8), [10, 10, 74, 74]),
        ), patch(
            "app.faces.face_recognition._encryption_key", return_value=b"k" * 32,
        ), patch(
            "app.faces.face_recognition.db.list_face_enrollments",
            new=AsyncMock(return_value=[]),
        ), patch(
            "app.faces.face_recognition.db.list_face_embeddings",
            new=AsyncMock(return_value=[]),
        ), patch(
            "app.faces.face_recognition.db.complete_face_recognition_job",
            new=AsyncMock(),
        ) as complete, patch(
            "app.faces.face_recognition.db.write_audit_log", new=AsyncMock(),
        ):
            await worker._process_job({
                "incident_id": 100,
                "camera_id": "CAM01",
                "track_id": 7,
                "violation_type": "no_helmet",
                "clean_snapshot_path": "not-read-by-test",
            })

        self.assertEqual(complete.await_args.args[1], "UNKNOWN")
        self.assertIsNone(complete.await_args.args[2])
        self.assertEqual(complete.await_args.args[7], [])
        self.assertIsInstance(complete.await_args.args[-1], bytes)

    async def test_identified_face_crop_is_encrypted_for_manager_review(self):
        key = b"k" * 32

        class _Analyzer:
            model_name = "test-model"
            model_version = "test-version"
            runtime_provider = "CPUExecutionProvider"

            def analyze(self, _image):
                return np.asarray([1.0, 0.0], dtype=np.float32), {
                    "quality_score": 0.95,
                    "bbox": [5, 5, 25, 25],
                }

        worker = FaceRecognitionWorker(analyzer_factory=_Analyzer)
        worker._analyzer = _Analyzer()
        with patch(
            "app.faces.face_recognition.db.get_face_match_settings",
            new=AsyncMock(return_value={
                "high_threshold": 0.8, "low_threshold": 0.7,
                "minimum_margin": 0.1, "quality_min_by_violation": {},
            }),
        ), patch(
            "app.faces.face_recognition._read_person_crop",
            return_value=(np.zeros((64, 64, 3), dtype=np.uint8), [0, 0, 64, 64]),
        ), patch(
            "app.faces.face_recognition._encryption_key", return_value=key,
        ), patch(
            "app.faces.face_recognition.db.list_face_enrollments",
            new=AsyncMock(return_value=[
                {
                    "person_id": 1, "employee_code": "E001",
                    "full_name": "Person One", "department": None, "active": True,
                },
                {
                    "person_id": 2, "employee_code": "E002",
                    "full_name": "Person Two", "department": None, "active": True,
                },
            ]),
        ), patch(
            "app.faces.face_recognition.db.list_face_embeddings",
            new=AsyncMock(return_value=[
                {
                    "person_id": 1, "active": True,
                    "model_name": "test-model", "model_version": "test-version",
                    "embedding": encrypt_embedding(np.asarray([1.0, 0.0]), key),
                },
                {
                    "person_id": 2, "active": True,
                    "model_name": "test-model", "model_version": "test-version",
                    "embedding": encrypt_embedding(np.asarray([0.0, 1.0]), key),
                },
            ]),
        ), patch(
            "app.faces.face_recognition.config.FACE_MATCH_THRESHOLD", 0.8,
        ), patch(
            "app.faces.face_recognition.config.FACE_SUGGEST_THRESHOLD", 0.7,
        ), patch(
            "app.faces.face_recognition.config.FACE_MIN_MARGIN", 0.1,
        ), patch(
            "app.faces.face_recognition.db.complete_face_recognition_job",
            new=AsyncMock(),
        ) as complete, patch(
            "app.faces.face_recognition.db.write_audit_log", new=AsyncMock(),
        ):
            await worker._process_job({
                "incident_id": 103,
                "camera_id": "CAM01",
                "track_id": 7,
                "violation_type": "no_helmet",
                "clean_snapshot_path": "not-read-by-test",
            })

        self.assertEqual(complete.await_args.args[1], "MATCHED")
        encrypted_crop = complete.await_args.args[-1]
        self.assertIsInstance(encrypted_crop, bytes)
        decoded = cv2.imdecode(
            np.frombuffer(decrypt_face_crop(encrypted_crop, key), dtype=np.uint8),
            cv2.IMREAD_COLOR,
        )
        self.assertEqual(decoded.shape[:2], (28, 28))

    async def test_no_face_is_recorded_without_blocking_worker(self):
        class _Analyzer:
            model_name = "test-model"
            model_version = "test-version"
            runtime_provider = "CPUExecutionProvider"

            def analyze(self, _image):
                raise ImageRejected("no_face")

        worker = FaceRecognitionWorker(analyzer_factory=_Analyzer)
        worker._analyzer = _Analyzer()
        with patch(
            "app.faces.face_recognition.db.get_face_match_settings",
            new=AsyncMock(return_value={
                "high_threshold": 0.6, "low_threshold": 0.45,
                "minimum_margin": 0.1, "quality_min_by_violation": {},
            }),
        ), patch(
            "app.faces.face_recognition._read_person_crop",
            return_value=(np.zeros((64, 64, 3), dtype=np.uint8), [10, 10, 74, 74]),
        ), patch(
            "app.faces.face_recognition.db.complete_face_recognition_job",
            new=AsyncMock(),
        ) as complete, patch(
            "app.faces.face_recognition.db.write_audit_log", new=AsyncMock(),
        ):
            await worker._process_job({
                "incident_id": 101,
                "camera_id": "CAM01",
                "track_id": 7,
                "violation_type": "phone_detected",
                "clean_snapshot_path": "not-read-by-test",
            })

        self.assertEqual(complete.await_args.args[1], "FACE_NOT_VISIBLE")
        self.assertEqual(complete.await_args.args[7], [])

    async def test_track_frames_are_analyzed_for_each_new_violation(self):
        class _Analyzer:
            model_name = "test-model"
            model_version = "test-version"
            runtime_provider = "CPUExecutionProvider"

            def __init__(self):
                self.calls = 0

            def analyze(self, _image):
                self.calls += 1
                raise ImageRejected("no_face")

        analyzer = _Analyzer()
        worker = FaceRecognitionWorker(analyzer_factory=lambda: analyzer)
        worker._analyzer = analyzer
        image = np.zeros((64, 64, 3), dtype=np.uint8)
        encoded_ok, encoded = cv2.imencode(".jpg", image)
        self.assertTrue(encoded_ok)
        worker._pending_crops[101] = [(encoded.tobytes(), [0, 0, 64, 64])]
        with patch(
            "app.faces.face_recognition.db.get_face_match_settings",
            new=AsyncMock(return_value={
                "high_threshold": 0.6, "low_threshold": 0.45,
                "minimum_margin": 0.1, "quality_min_by_violation": {},
            }),
        ), patch(
            "app.faces.face_recognition._read_person_crop",
            return_value=(None, None),
        ), patch(
            "app.faces.face_recognition.db.complete_face_recognition_job",
            new=AsyncMock(),
        ) as complete, patch(
            "app.faces.face_recognition.db.reuse_face_match_for_track",
            new=AsyncMock(),
        ) as reuse, patch(
            "app.faces.face_recognition.db.write_audit_log", new=AsyncMock(),
        ):
            await worker._process_job({
                "incident_id": 101,
                "camera_id": "CAM01",
                "track_id": 7,
                "violation_type": "no_helmet",
            })

        self.assertEqual(analyzer.calls, 1)
        self.assertEqual(complete.await_args.args[1], "FACE_NOT_VISIBLE")
        reuse.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
