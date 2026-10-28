import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from app.faces.face_calibration import (
    CalibrationSample,
    evaluate_queries,
    incident_queries,
    leave_one_out_queries,
    load_labeled_incident_crops,
)


class FaceCalibrationTests(unittest.TestCase):
    def test_leave_one_out_excludes_query_image_and_keeps_other_identities(self):
        references = {
            "E001": [
                CalibrationSample("E001", "primary_dataset", np.array([1.0, 0.0])),
                CalibrationSample("E001", "primary_dataset", np.array([0.9, 0.1])),
            ],
            "E002": [
                CalibrationSample("E002", "primary_dataset", np.array([0.0, 1.0])),
                CalibrationSample("E002", "primary_dataset", np.array([0.1, 0.9])),
            ],
        }

        queries = leave_one_out_queries(references)

        self.assertEqual(len(queries), 4)
        self.assertAlmostEqual(queries[0]["scores"]["E001"], 0.9938837, places=5)
        self.assertLess(queries[0]["scores"]["E001"], 1.0)
        self.assertIn("E002", queries[0]["scores"])

    def test_unknown_crop_is_not_identified_at_strict_impostor_threshold(self):
        queries = [
            {
                "label": "E001",
                "camera": "CAM01",
                "scores": {"E001": 0.95, "E002": 0.25},
            },
            {
                "label": "E002",
                "camera": "CAM01",
                "scores": {"E001": 0.2, "E002": 0.92},
            },
            {
                "label": "not_in_dataset",
                "camera": "CAM01",
                "scores": {"E001": 0.7, "E002": 0.65},
            },
        ]

        result = evaluate_queries(queries, target_far=0.001)
        camera = result["groups"]["unspecified_type|CAM01"]

        self.assertEqual(camera["recommended_match_threshold"], 0.7)
        self.assertEqual(camera["false_accept_count"], 0)
        self.assertEqual(camera["true_accept_rate"], 1.0)
        self.assertEqual(camera["not_identified_count"], 1)
        self.assertFalse(camera["threshold_sample_size_adequate"])
        self.assertEqual(
            sum(row["count"] for row in result["histogram"]), 5,
        )

    def test_incident_metrics_are_split_by_violation_type_and_camera(self):
        queries = [
            {
                "label": "E001", "camera": "CAM01",
                "violation_type": "no_helmet", "scores": {"E001": 0.95},
            },
            {
                "label": "not_in_dataset", "camera": "CAM01",
                "violation_type": "no_helmet", "scores": {"E001": 0.4},
            },
            {
                "label": "E001", "camera": "CAM02",
                "violation_type": "no_vest", "scores": {"E001": 0.92},
            },
            {
                "label": "not_in_dataset", "camera": "CAM02",
                "violation_type": "phone_detected", "scores": {"E001": 0.35},
            },
        ]

        result = evaluate_queries(queries, target_far=0.5)

        self.assertIn("no_helmet|CAM01", result["groups"])
        self.assertIn("no_vest|CAM02", result["groups"])
        self.assertIn("phone_detected|CAM02", result["groups"])
        self.assertEqual(
            result["by_violation_type"]["no_helmet"]["queries"], 2,
        )
        self.assertEqual(result["groups"]["no_vest|CAM02"]["camera"], "CAM02")
        self.assertTrue(all(
            "violation_type" in row and "camera" in row
            for row in result["histogram"]
        ))

    def test_loads_camera_violation_employee_crop_layout(self):
        class _Analyzer:
            def analyze(self, _image):
                return np.array([1.0, 0.0]), {"quality_score": 0.9}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for label, image_name, pixel_value in (
                ("E001", "known.png", 127),
                ("not_in_dataset", "unknown.png", 63),
            ):
                folder = root / "CAM01" / "phone_detected" / label
                folder.mkdir(parents=True)
                cv2.imwrite(
                    str(folder / image_name),
                    np.full((32, 32, 3), pixel_value, dtype=np.uint8),
                )
            samples, rejected, camera_counts = load_labeled_incident_crops(
                root, {"E001"}, _Analyzer(),
            )

        self.assertEqual(len(samples), 2)
        self.assertEqual({sample.violation_type for sample in samples}, {"phone_detected"})
        self.assertEqual({sample.camera for sample in samples}, {"CAM01"})
        self.assertEqual({sample.label for sample in samples}, {"E001", "not_in_dataset"})
        self.assertEqual(camera_counts, {"CAM01": 2})
        self.assertEqual(rejected, {})

    def test_loads_identity_labeled_csv_manifest(self):
        class _Analyzer:
            def analyze(self, _image):
                return np.array([1.0, 0.0]), {"quality_score": 0.9}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, value in (("known.png", 127), ("unknown.png", 63)):
                cv2.imwrite(
                    str(root / name),
                    np.full((32, 32, 3), value, dtype=np.uint8),
                )
            manifest = root / "labels.csv"
            manifest.write_text(
                "image_path,camera_id,violation_type,employee_id\n"
                "known.png,CAM01,no_arc_flash_suit,E001\n"
                "unknown.png,CAM01,no_arc_flash_suit,not_in_dataset\n",
                encoding="utf-8",
            )

            samples, rejected, camera_counts = load_labeled_incident_crops(
                manifest, {"E001"}, _Analyzer(),
            )

        self.assertEqual(len(samples), 2)
        self.assertEqual({sample.violation_type for sample in samples}, {
            "no_arc_flash_suit",
        })
        self.assertEqual({sample.camera for sample in samples}, {"CAM01"})
        self.assertEqual({sample.label for sample in samples}, {
            "E001", "not_in_dataset",
        })
        self.assertEqual(camera_counts, {"CAM01": 2})
        self.assertEqual(rejected, {})

    def test_incident_manifest_rejects_paths_outside_its_directory(self):
        class _Analyzer:
            def analyze(self, _image):
                return np.array([1.0, 0.0]), {"quality_score": 0.9}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_path = root / "outside.png"
            cv2.imwrite(
                str(image_path), np.zeros((32, 32, 3), dtype=np.uint8),
            )
            manifest_dir = root / "labels"
            manifest_dir.mkdir()
            manifest = manifest_dir / "labels.csv"
            manifest.write_text(
                "image_path,camera_id,violation_type,employee_id\n"
                "../outside.png,CAM01,no_helmet,E001\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "outside the manifest"):
                load_labeled_incident_crops(manifest, {"E001"}, _Analyzer())

    def test_incident_queries_compare_only_to_enrolled_gallery(self):
        references = {
            "E001": [
                CalibrationSample("E001", "primary_dataset", np.array([1.0, 0.0])),
            ],
        }
        incident = [
            CalibrationSample("not_in_dataset", "CAM02", np.array([0.0, 1.0])),
        ]

        query = incident_queries(incident, references)[0]

        self.assertEqual(query["label"], "not_in_dataset")
        self.assertEqual(set(query["scores"]), {"E001"})
        self.assertAlmostEqual(query["scores"]["E001"], 0.0)


if __name__ == "__main__":
    unittest.main()
