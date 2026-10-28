import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np

from app import config
from app.compliance_engine import ComplianceEngine, _phone_belongs_to_person
from app.detector import Detection


class ComplianceEnginePhoneTests(unittest.TestCase):
    def test_phone_near_person_is_associated_with_person_box(self):
        self.assertTrue(
            _phone_belongs_to_person(
                (10, 10, 90, 190),
                (88, 70, 98, 85),
            )
        )

    def test_phone_far_from_person_is_not_associated(self):
        self.assertFalse(
            _phone_belongs_to_person(
                (10, 10, 90, 190),
                (150, 70, 170, 90),
            )
        )

    def test_phone_held_just_outside_person_box_is_associated(self):
        self.assertTrue(
            _phone_belongs_to_person(
                (10, 10, 90, 190),
                (100, 70, 108, 85),
            )
        )

    def test_phone_event_is_temporally_confirmed_for_person_track(self):
        engine = ComplianceEngine("CAM01", [])
        engine.tracker.update = Mock(
            return_value=np.asarray(
                [[10, 10, 90, 190, 7, 0.99, 0, 0]],
                dtype=np.float32,
            )
        )
        engine.tracker.tracked_stracks = [SimpleNamespace(track_id=7)]
        engine.tracker.lost_stracks = []
        detections = [
            Detection("Person", "person", 0.99, 10, 10, 90, 190),
            Detection("cell phone", "phone", 0.9, 100, 70, 108, 85),
        ]

        first = engine.evaluate(detections, (200, 200, 3))[0]
        second = engine.evaluate(detections, (200, 200, 3))[0]

        self.assertFalse(first.phone_detected)
        self.assertTrue(second.phone_detected)
        self.assertTrue(second.is_violation)
        self.assertEqual(second.track_id, 7)
        self.assertEqual(second.phone_box, (100, 70, 108, 85))

    def test_arc_flash_suit_class_is_recognized_across_camera_engines(self):
        self.assertEqual(config.CLASS_MAP["Arc Flash Suit"], "arc_flash_suit")
        self.assertEqual(
            config.NEGATIVE_PPE_CLASSES["arc_flash_suit"],
            "no_arc_flash_suit",
        )
        for camera_id in ("CAM01", "CAM02"):
            engine = ComplianceEngine(
                camera_id, ["arc_flash_suit"],
                {"arc_flash_suit", "no_arc_flash_suit"},
            )
            engine.tracker.update = Mock(
                return_value=np.asarray(
                    [[10, 10, 90, 190, 7, 0.99, 0, 0]],
                    dtype=np.float32,
                )
            )
            engine.tracker.tracked_stracks = [SimpleNamespace(track_id=7)]
            engine.tracker.lost_stracks = []

            status = engine.evaluate(
                [
                    Detection("Person", "person", 0.99, 10, 10, 90, 190),
                    Detection(
                        "Arc Flash Suit", "arc_flash_suit", 0.9,
                        15, 15, 85, 185,
                    ),
                ],
                (200, 200, 3),
            )[0]

            self.assertIn("arc_flash_suit", status.present_ppe)
            self.assertNotIn("arc_flash_suit", status.unknown_ppe)

    def test_seeded_cameras_require_arc_suit_but_missing_model_class_is_unknown(self):
        self.assertEqual(
            [
                camera["camera_id"] for camera in config.SEED_CAMERAS
                if "arc_flash_suit" in camera["required_ppe"]
            ],
            ["CAM07"],
        )
        engine = ComplianceEngine(
            "CAM01", ["arc_flash_suit"], {"person", "helmet", "no_helmet"},
        )
        engine.tracker.update = Mock(
            return_value=np.asarray(
                [[10, 10, 90, 190, 7, 0.99, 0, 0]],
                dtype=np.float32,
            )
        )
        engine.tracker.tracked_stracks = [SimpleNamespace(track_id=7)]
        engine.tracker.lost_stracks = []
        status = engine.evaluate(
            [Detection("Person", "person", 0.99, 10, 10, 90, 190)],
            (200, 200, 3),
        )[0]

        self.assertIn("arc_flash_suit", status.unknown_ppe)
        self.assertNotIn("arc_flash_suit", status.missing_ppe)

    def test_safety_vest_model_classes_support_camera_required_ppe(self):
        self.assertEqual(config.CLASS_MAP["Safety Vest"], "vest")
        self.assertEqual(config.CLASS_MAP["NO-Safety Vest"], "no_vest")
        self.assertEqual(config.NEGATIVE_PPE_CLASSES["vest"], "no_vest")
        self.assertTrue(all(
            "vest" in camera["required_ppe"]
            for camera in config.SEED_CAMERAS
        ))

        engine = ComplianceEngine(
            "CAM01", ["vest"], {"person", "vest", "no_vest"},
        )
        engine.tracker.update = Mock(
            return_value=np.asarray(
                [[10, 10, 90, 190, 7, 0.99, 0, 0]],
                dtype=np.float32,
            )
        )
        engine.tracker.tracked_stracks = [SimpleNamespace(track_id=7)]
        engine.tracker.lost_stracks = []
        status = engine.evaluate(
            [
                Detection("Person", "person", 0.99, 10, 10, 90, 190),
                Detection("Safety Vest", "vest", 0.9, 15, 50, 85, 140),
            ],
            (200, 200, 3),
        )[0]

        self.assertIn("vest", status.present_ppe)
        self.assertNotIn("vest", status.unknown_ppe)


if __name__ == "__main__":
    unittest.main()
