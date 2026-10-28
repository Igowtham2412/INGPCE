import unittest

import numpy as np

from app.feedback import _crop_and_labels


class FeedbackCorrectionTests(unittest.TestCase):
    def setUp(self):
        self.image = np.zeros((100, 100, 3), dtype=np.uint8)
        self.metadata = {
            "person_box": [20, 10, 80, 90],
            "model_names": {
                "0": "Hardhat",
                "2": "NO-Hardhat",
                "5": "Person",
            },
            "detections": [
                {"class_id": 5, "class_name": "person", "box": [20, 10, 80, 90]},
                {"class_id": 2, "class_name": "no_helmet", "box": [30, 12, 55, 35]},
            ],
        }

    def test_false_person_exports_empty_background_labels(self):
        _, labels = _crop_and_labels(
            self.image, self.metadata, "no_helmet", "false_positive",
            correction_type="false_person",
        )
        self.assertEqual(labels, [])

    def test_ppe_correction_replaces_false_negative_with_manual_box(self):
        _, labels = _crop_and_labels(
            self.image, self.metadata, "no_helmet", "false_positive",
            correction_type="ppe_corrected", corrected_class="helmet",
            corrected_box=[35, 14, 50, 29],
        )
        self.assertEqual(len(labels), 2)
        self.assertTrue(any(label.startswith("0 ") for label in labels))
        self.assertFalse(any(label.startswith("2 ") for label in labels))

    def test_ppe_correction_requires_box_inside_evidence(self):
        with self.assertRaisesRegex(ValueError, "outside the evidence image"):
            _crop_and_labels(
                self.image, self.metadata, "no_helmet", "false_positive",
                correction_type="ppe_corrected", corrected_class="helmet",
                corrected_box=[-1, 14, 50, 29],
            )


if __name__ == "__main__":
    unittest.main()
