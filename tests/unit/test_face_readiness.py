import tempfile
import unittest
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook

from app.faces.face_readiness import get_face_readiness


class FaceReadinessTests(unittest.TestCase):
    def test_readiness_requires_local_yolo_and_embedding_models(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            detector = root / "yolov8n-face.pt"
            detector.write_bytes(b"detector weights")
            model_dir = root / "models"
            buffalo_dir = model_dir / "models" / "buffalo_l"
            buffalo_dir.mkdir(parents=True)
            (buffalo_dir / "recognition.onnx").write_bytes(b"ArcFace model")
            with patch(
                "app.faces.face_readiness.config.FACE_DETECTOR_MODEL_PATH",
                str(detector),
            ), patch(
                "app.faces.face_readiness.config.FACE_MODEL_DIR",
                str(model_dir),
            ), patch(
                "app.faces.face_readiness.config.FACE_MODEL_NAME",
                "insightface_buffalo_l",
            ):
                result = get_face_readiness(0)

        self.assertTrue(result["detector_model_present"])
        self.assertTrue(result["embedding_model_present"])
        self.assertTrue(result["model_present"])

    def test_readiness_reports_missing_yolo_detector_weights(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            "app.faces.face_readiness.config.FACE_DETECTOR_MODEL_PATH",
            str(Path(directory) / "missing-yolov8n-face.pt"),
        ):
            result = get_face_readiness(0)

        self.assertFalse(result["detector_model_present"])
        self.assertFalse(result["model_present"])
        self.assertTrue(any(
            "PPE_FACE_DETECTOR_MODEL_PATH" in item
            for item in result["missing"]
        ))

    def test_readiness_lists_missing_dependencies_and_inputs(self):
        with tempfile.TemporaryDirectory() as directory, patch(
                "app.faces.face_readiness.importlib.metadata.version",
                side_effect=PackageNotFoundError,
        ), patch(
                "app.faces.face_readiness.config.FACE_LICENSE_ACCEPTED", False,
        ), patch(
                "app.faces.face_readiness.config.FACE_ENABLED", False,
        ), patch(
                "app.faces.face_readiness.config.FACE_ENCRYPTION_KEY", "",
        ), patch(
                "app.faces.face_readiness.config.FACE_DATASET_PATH", directory,
        ):
            result = get_face_readiness(0)

        self.assertFalse(result["ready_for_recognition"])
        self.assertFalse(result["license_flag_accepted"])
        self.assertEqual(result["active_enrolled_people"], 0)
        self.assertIn("Review model terms and set PPE_FACE_LICENSE_ACCEPTED=true after authorization",
                      result["missing"])
        self.assertIn("Upload a validated consented face dataset", result["missing"])
        self.assertIn(
            "Configure PPE_FACE_ENCRYPTION_KEY in the local secret store",
            result["missing"],
        )

    def test_dataset_presence_ignores_gitkeep_and_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / ".gitkeep").touch()
            with patch(
                "app.faces.face_readiness.config.FACE_DATASET_PATH", directory,
            ):
                result = get_face_readiness(0)

        self.assertEqual(result["dataset_directory_configured"], directory)
        self.assertFalse(result["dataset_present"])

    def test_unlabelled_images_at_dataset_root_are_not_a_usable_roster(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "Image.jpg").write_bytes(b"not decoded by readiness")
            with patch(
                "app.faces.face_readiness.config.FACE_DATASET_PATH", directory,
            ):
                result = get_face_readiness(0)

        self.assertFalse(result["dataset_present"])
        self.assertFalse(result["excel_manifest_present"])

    def test_excel_manifest_is_detected_as_dataset_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "E001_Jane Doe").mkdir()
            image_path = root / "E001_Jane Doe" / "photo.jpg"
            image_path.write_bytes(b"synthetic-image")
            workbook = Workbook()
            sheet = workbook.active
            sheet.append([
                "employee_code", "full_name", "department", "image_path",
                "consent_recorded", "consent_reference", "consent_date",
            ])
            sheet.append([
                "E001", "Example Person", "Operations",
                "E001_Jane Doe/photo.jpg", True, "CONSENT-001", "2026-10-28",
            ])
            workbook.save(root / "employees.xlsx")
            with patch(
                "app.faces.face_readiness.config.FACE_DATASET_PATH", directory,
            ):
                result = get_face_readiness(0)

        self.assertTrue(result["dataset_present"])
        self.assertTrue(result["excel_manifest_present"])

    def test_example_workbook_without_real_enrollment_images_is_not_ready(self):
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

        self.assertFalse(result["dataset_present"])
        self.assertTrue(result["excel_manifest_present"])

    def test_invalid_encryption_key_is_reported_as_unconfigured(self):
        with patch(
            "app.faces.face_readiness.config.FACE_ENCRYPTION_KEY",
            "not-a-valid-key",
        ):
            result = get_face_readiness(0)

        self.assertFalse(result["encryption_key_configured"])
        self.assertIn(
            "Configure PPE_FACE_ENCRYPTION_KEY in the local secret store",
            result["missing"],
        )
