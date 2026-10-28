import tempfile
import unittest
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook

from app.faces.face_readiness import get_face_readiness


class FaceReadinessTests(unittest.TestCase):
    def test_readiness_lists_missing_dependencies_and_inputs(self):
        with patch(
            "app.faces.face_readiness.importlib.metadata.version",
            side_effect=PackageNotFoundError,
        ):
            result = get_face_readiness(0)

        self.assertFalse(result["ready_for_recognition"])
        self.assertFalse(result["license_flag_accepted"])
        self.assertEqual(result["active_enrolled_people"], 0)
        self.assertIn("Review model terms and set PPE_FACE_LICENSE_ACCEPTED=true after authorization",
                      result["missing"])
        self.assertIn("Upload a validated consented face dataset", result["missing"])

    def test_dataset_presence_ignores_gitkeep_and_reports(self):
        with patch("app.faces.face_readiness.config.FACE_DATASET_PATH", "dataset/faces"):
            result = get_face_readiness(0)

        self.assertTrue(Path(result["dataset_directory_configured"]).as_posix().endswith("dataset/faces")
                        or result["dataset_directory_configured"] == "dataset/faces")
        self.assertFalse(result["dataset_present"])

    def test_excel_manifest_is_detected_as_dataset_input(self):
        with tempfile.TemporaryDirectory() as directory:
            workbook = Workbook()
            workbook.active.append([
                "employee_code", "full_name", "department", "image_path",
                "consent_recorded", "consent_reference",
            ])
            workbook.save(Path(directory) / "employees.xlsx")
            with patch(
                "app.faces.face_readiness.config.FACE_DATASET_PATH", directory,
            ):
                result = get_face_readiness(0)

        self.assertTrue(result["dataset_present"])
        self.assertTrue(result["excel_manifest_present"])
