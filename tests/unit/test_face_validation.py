import csv
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np
from openpyxl import Workbook

from app.faces.face_dataset import discover_dataset
from app.faces.face_validation import validate_face_dataset


def _write_image(path: Path, value: int = 120) -> None:
    image = np.full((32, 32, 3), value, dtype=np.uint8)
    ok, encoded = cv2.imencode(path.suffix, image)
    if not ok:
        raise RuntimeError("could not encode synthetic test image")
    path.write_bytes(encoded.tobytes())


class FaceDatasetValidationTests(unittest.TestCase):
    def test_empty_dataset_directory_returns_readable_report(self):
        with tempfile.TemporaryDirectory() as directory:
            report = validate_face_dataset(Path(directory))

        self.assertFalse(report["valid"])
        self.assertEqual(report["people"], [])
        self.assertEqual(report["issues"][0]["code"], "bulk_consent_details_missing")
        self.assertEqual(report["warnings"][0]["code"], "no_employee_records_found")

    def test_folder_layout_counts_photos_duplicates_and_shortfall(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            employee = root / "E001_Jane Doe"
            employee.mkdir()
            _write_image(employee / "one.jpg")
            (employee / "duplicate.png").write_bytes((employee / "one.jpg").read_bytes())
            (employee / "bad.jpg").write_bytes(b"not an image")
            report = validate_face_dataset(
                root, min_photos=5, consent_name="Admin",
                consent_date="2026-10-26", consent_reference="CONSENT-1",
            )

        self.assertFalse(report["valid"])
        self.assertEqual(report["total_images"], 3)
        self.assertEqual(report["readable_images"], 2)
        self.assertEqual(report["duplicate_images"], 1)
        codes = {issue["code"] for issue in report["issues"]}
        self.assertIn("image_unreadable", codes)
        self.assertEqual(report["warnings"][0]["code"], "duplicate_image_hash")

    def test_csv_requires_consent_columns_and_rejects_false_consent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_image(root / "photo.jpg")
            manifest = root / "manifest.csv"
            manifest.write_text(
                "employee_code,full_name,department,image_path\n"
                "E001,Jane Doe,Safety,photo.jpg\n",
                encoding="utf-8",
            )
            missing = validate_face_dataset(manifest)
            self.assertIn(
                "consent_columns_missing:consent_recorded,consent_reference",
                {issue["code"] for issue in missing["issues"]},
            )

            with manifest.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow([
                    "employee_code", "full_name", "department", "image_path",
                    "consent_recorded", "consent_reference",
                ])
                writer.writerow(["E001", "Jane Doe", "Safety", "photo.jpg", "false", ""])
            invalid = validate_face_dataset(manifest)

        codes = {issue["code"] for issue in invalid["issues"]}
        self.assertIn("consent_not_recorded", codes)
        self.assertIn("consent_reference_missing", codes)

    def test_csv_path_escape_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "manifest.csv"
            manifest.write_text(
                "employee_code,full_name,department,image_path,consent_recorded,consent_reference\n"
                "E001,Jane Doe,Safety,../outside.jpg,true,CONSENT-1\n",
                encoding="utf-8",
            )
            report = validate_face_dataset(manifest)

        self.assertIn(
            "image_path_outside_dataset",
            {issue["code"] for issue in report["issues"]},
        )

    def test_excel_manifest_uses_employee_folders_and_validates_consent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_folder = root / "E001_Jane Doe"
            image_folder.mkdir()
            _write_image(image_folder / "photo.jpg")
            workbook = Workbook()
            sheet = workbook.active
            sheet.append([
                "employee_code", "full_name", "department", "image_path",
                "consent_recorded", "consent_reference",
            ])
            sheet.append([
                "E001", "Jane Doe", "Safety",
                "E001_Jane Doe/photo.jpg", True, "CONSENT-001",
            ])
            manifest = root / "employees.xlsx"
            workbook.save(manifest)

            report = validate_face_dataset(root)
            people = discover_dataset(root)

        self.assertTrue(report["valid"])
        self.assertEqual(report["layout"], "manifest")
        self.assertEqual(report["readable_images"], 1)
        self.assertEqual(people["E001"].images[0].path.name, "photo.jpg")
        self.assertEqual(people["E001"].consent_basis, "CONSENT-001")

    def test_blank_excel_template_is_not_reported_as_import_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            workbook = Workbook()
            workbook.active.append([
                "employee_code", "full_name", "department", "image_path",
                "consent_recorded", "consent_reference",
            ])
            manifest = Path(directory) / "employees.xlsx"
            workbook.save(manifest)

            report = validate_face_dataset(manifest)

        self.assertFalse(report["valid"])
        self.assertIn(
            "manifest_has_no_employee_records",
            {issue["code"] for issue in report["issues"]},
        )
