import base64
import csv
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
from cryptography.exceptions import InvalidTag
from openpyxl import Workbook

from app.faces import face_dataset
from app.faces.face_dataset import (
    ConsentError,
    ImageRejected,
    InsightFaceAnalyzer,
    _quality_approved_face,
    decrypt_embedding,
    decrypt_face_crop,
    discover_dataset,
    encrypt_embedding,
    encrypt_face_crop,
    import_face_dataset,
)


class _FakeFace:
    def __init__(self, size=100, sharp=True, pose=(0, 0), embedding=(1, 0)):
        self.bbox = np.asarray([20, 20, 20 + size, 20 + size], dtype=np.float32)
        self.det_score = 0.99
        self.pose = np.asarray(pose, dtype=np.float32)
        self.normed_embedding = np.asarray(embedding, dtype=np.float32)


class _FakeAnalyzer:
    model_name = "unit-test-model"
    model_version = "unit-test-version"

    def __init__(self, embedding=(1, 0)):
        self.embedding = np.asarray(embedding, dtype=np.float32)

    def analyze(self, image):
        return self.embedding.copy(), {
            "quality_score": 0.95,
            "bbox": [10, 10, 90, 90],
        }


class _OutlierAnalyzer(_FakeAnalyzer):
    def analyze(self, image):
        if float(image.mean()) > 170:
            embedding = np.asarray([0, 1], dtype=np.float32)
        else:
            embedding = np.asarray([1, 0], dtype=np.float32)
        return embedding, {"quality_score": 0.95, "bbox": [10, 10, 90, 90]}


class _MemoryFaceRepository:
    def __init__(self):
        self.people = {}
        self.embeddings = {}
        self.next_person_id = 1
        self.runs = []
        self.audit = []

    async def list_face_enrollments(self):
        return list(self.people.values())

    async def list_face_embeddings(self):
        return list(self.embeddings.values())

    async def upsert_face_enrollment(
            self, employee_code, full_name, department, consent_basis,
            consent_date, enrolled_by):
        current = self.people.get(employee_code)
        if current is None:
            current = {
                "person_id": self.next_person_id,
                "employee_code": employee_code,
                "active": True,
                "embedding_count": 0,
            }
            self.next_person_id += 1
        current.update({
            "full_name": full_name,
            "department": department,
            "consent_recorded": True,
            "consent_basis": consent_basis,
            "consent_date": consent_date,
            "enrolled_by": enrolled_by,
        })
        self.people[employee_code] = current
        return current

    async def get_face_embedding_by_hash(self, image_hash):
        return self.embeddings.get(image_hash)

    async def insert_face_embedding(
            self, person_id, embedding, quality_score, image_hash,
            photo_path, model_name, model_version, source="dataset import"):
        if image_hash in self.embeddings:
            return None
        self.embeddings[image_hash] = {
            "embedding_id": len(self.embeddings) + 1,
            "person_id": person_id,
            "embedding": embedding,
            "quality_score": quality_score,
            "image_hash": image_hash,
            "model_name": model_name,
            "model_version": model_version,
            "active": True,
        }
        return len(self.embeddings)

    async def create_face_import_run(self, status, actor_user_id, summary, report_path):
        self.runs.append((status, actor_user_id, summary, report_path))
        return len(self.runs)

    async def write_audit_log(self, *args, **kwargs):
        self.audit.append((args, kwargs))


class FaceDatasetDiscoveryTests(unittest.TestCase):
    def test_folder_layout_requires_explicit_bulk_consent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "E001_Jane Doe").mkdir()
            with self.assertRaises(ConsentError):
                discover_dataset(root)

    def test_folder_layout_parses_identity_and_allows_zero_photo_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "E001_Jane_Doe").mkdir()
            people = discover_dataset(
                root,
                consent_name="Safety Manager",
                consent_date="2026-10-26",
                consent_reference="CONSENT-2026-01",
            )

        person = people["E001"]
        self.assertEqual(person.full_name, "Jane Doe")
        self.assertEqual(person.consent_basis.split(";")[0], "Bulk consent confirmed by Safety Manager")
        self.assertEqual(person.consent_date.isoformat(), "2026-10-26")
        self.assertEqual(person.images, [])

    def test_csv_layout_requires_consent_for_each_row(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_path = root / "photo.jpg"
            image_path.write_bytes(b"not decoded during discovery")
            csv_path = root / "employees.csv"
            with csv_path.open("w", newline="", encoding="utf-8") as csv_file:
                writer = csv.DictWriter(csv_file, fieldnames=[
                    "employee_code", "full_name", "department", "image_path",
                    "consent_recorded",
                ])
                writer.writeheader()
                writer.writerow({
                    "employee_code": "E001", "full_name": "Jane Doe",
                    "department": "Safety", "image_path": "photo.jpg",
                    "consent_recorded": "true",
                })

            people = discover_dataset(root)
            self.assertEqual(len(people["E001"].images), 1)
            self.assertTrue(people["E001"].images[0].consent_recorded)
            with csv_path.open("w", newline="", encoding="utf-8") as csv_file:
                writer = csv.DictWriter(csv_file, fieldnames=[
                    "employee_code", "full_name", "department", "image_path",
                    "consent_recorded",
                ])
                writer.writeheader()
                writer.writerow({
                    "employee_code": "E001", "full_name": "Jane Doe",
                    "department": "Safety", "image_path": "photo.jpg",
                    "consent_recorded": "false",
                })
            with self.assertRaises(ConsentError):
                discover_dataset(root)

    def test_csv_rejects_image_paths_outside_the_dataset_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "dataset"
            root.mkdir()
            outside = Path(directory) / "outside.jpg"
            outside.write_bytes(b"image")
            csv_path = root / "employees.csv"
            csv_path.write_text(
                "employee_code,full_name,department,image_path,consent_recorded\n"
                f"E001,Jane Doe,Safety,{outside},true\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "inside the dataset root"):
                discover_dataset(root)

    def test_excel_manifest_reads_consent_and_images_from_employee_folders(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            employee = root / "E001_Jane Doe"
            employee.mkdir()
            image_path = employee / "photo.jpg"
            image_path.write_bytes(b"synthetic-image-data")
            workbook = Workbook()
            sheet = workbook.active
            sheet.append([
                "employee_code", "full_name", "department", "image_path",
                "consent_recorded", "consent_reference", "consent_date",
            ])
            sheet.append([
                "E001", "Jane Doe", "Safety",
                "E001_Jane Doe/photo.jpg", True, "CONSENT-001", "2026-10-27",
            ])
            workbook.save(root / "employees.xlsx")

            people = discover_dataset(root)

        person = people["E001"]
        self.assertEqual(person.full_name, "Jane Doe")
        self.assertEqual(person.consent_basis, "CONSENT-001")
        self.assertEqual(person.consent_date.isoformat(), "2026-10-27")
        self.assertEqual(person.images[0].path, image_path.resolve())


class FaceQualityTests(unittest.TestCase):
    def test_quality_gate_rejects_small_face(self):
        image = np.random.default_rng(3).integers(
            0, 256, (160, 160, 3), dtype=np.uint8,
        )
        with self.assertRaisesRegex(ImageRejected, "face_too_small"):
            _quality_approved_face(_FakeFace(size=50), image)

    def test_quality_gate_rejects_non_frontal_pose(self):
        image = np.random.default_rng(4).integers(
            0, 256, (160, 160, 3), dtype=np.uint8,
        )
        with self.assertRaisesRegex(ImageRejected, "non_frontal_yaw"):
            _quality_approved_face(_FakeFace(pose=(70, 0)), image)

    def test_insightface_analyzer_rejects_zero_and_multiple_faces(self):
        analyzer = InsightFaceAnalyzer.__new__(InsightFaceAnalyzer)
        analyzer.app = type("App", (), {"get": lambda _self, _image: []})()
        image = np.zeros((100, 100, 3), dtype=np.uint8)
        with self.assertRaisesRegex(ImageRejected, "no_face"):
            analyzer.analyze(image)
        analyzer.app = type(
            "App", (), {"get": lambda _self, _image: [object(), object()]}
        )()
        with self.assertRaisesRegex(ImageRejected, "multiple_faces"):
            analyzer.analyze(image)

    def test_insightface_model_requires_explicit_license_approval(self):
        with patch.object(face_dataset.config, "FACE_LICENSE_ACCEPTED", False):
            with self.assertRaisesRegex(
                    face_dataset.FaceModelUnavailable, "authorization"):
                InsightFaceAnalyzer()

    def test_insightface_inference_falls_back_to_cpu_after_gpu_failure(self):
        calls = []

        class _FaceAnalysis:
            def __init__(self, name, providers):
                self.providers = providers
                calls.append(providers)

            def prepare(self, **_kwargs):
                return None

            def get(self, _image):
                if self.providers[0] == "CUDAExecutionProvider":
                    raise RuntimeError("GPU provider failed")
                return []

        with tempfile.TemporaryDirectory() as directory:
            model_pack = Path(directory) / "buffalo_l"
            model_pack.mkdir()
            (model_pack / "detector.onnx").write_bytes(b"local model bytes")

            ort = types.ModuleType("onnxruntime")
            ort.get_available_providers = lambda: [
                "CUDAExecutionProvider", "CPUExecutionProvider",
            ]
            insightface = types.ModuleType("insightface")
            insightface.__path__ = []
            app_module = types.ModuleType("insightface.app")
            app_module.FaceAnalysis = _FaceAnalysis
            with patch.dict(sys.modules, {
                "onnxruntime": ort,
                "insightface": insightface,
                "insightface.app": app_module,
            }), patch.object(
                face_dataset.config, "FACE_LICENSE_ACCEPTED", True,
            ), patch.object(
                face_dataset.config, "FACE_MODEL_DIR", directory,
            ), patch.object(
                face_dataset.config, "FACE_MODEL_NAME", "buffalo_l",
            ):
                analyzer = InsightFaceAnalyzer()
                with self.assertRaisesRegex(ImageRejected, "no_face"):
                    analyzer.analyze(np.zeros((20, 20, 3), dtype=np.uint8))

        self.assertEqual(analyzer.runtime_provider, "CPUExecutionProvider")
        self.assertEqual(len(calls), 2)

    def test_embeddings_are_authenticated_and_encrypted(self):
        key = b"f" * 32
        source = np.asarray([0.6, 0.8], dtype=np.float32)
        encrypted = encrypt_embedding(source, key)
        self.assertNotEqual(encrypted, source.tobytes())
        np.testing.assert_allclose(decrypt_embedding(encrypted, key), source)
        with self.assertRaises(InvalidTag):
            decrypt_embedding(encrypted[:-1] + bytes([encrypted[-1] ^ 1]), key)

    def test_face_crops_are_authenticated_and_encrypted(self):
        key = b"c" * 32
        image = b"private face crop bytes"
        encrypted = encrypt_face_crop(image, key)

        self.assertNotEqual(encrypted, image)
        self.assertEqual(decrypt_face_crop(encrypted, key), image)
        with self.assertRaisesRegex(ValueError, "could not be authenticated"):
            decrypt_face_crop(encrypted[:-1] + bytes([encrypted[-1] ^ 1]), key)


class FaceDatasetImportTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_folder_dry_run_returns_report_without_model_or_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "faces"
            root.mkdir()
            reports = Path(directory) / "reports"
            with patch.object(face_dataset.config, "FACE_REPORT_DIR", str(reports)), patch(
                "app.faces.face_dataset.db.write_audit_log", side_effect=AssertionError,
            ), patch(
                "app.faces.face_dataset.InsightFaceAnalyzer", side_effect=AssertionError,
            ):
                result = await import_face_dataset(root, dry_run=True)
                self.assertTrue(Path(result["report_path"]).is_file())

        self.assertTrue(result["dry_run"])
        self.assertEqual(result["people"], 0)
        self.assertEqual(result["images"], 0)

    async def test_model_quality_dry_run_does_not_write_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "faces"
            employee = root / "E001_Jane Doe"
            employee.mkdir(parents=True)
            image = np.random.default_rng(10).integers(
                0, 256, (120, 120, 3), dtype=np.uint8,
            )
            ok, encoded = cv2.imencode(".jpg", image)
            self.assertTrue(ok)
            (employee / "photo.jpg").write_bytes(encoded.tobytes())
            reports = Path(directory) / "reports"
            with patch.object(face_dataset.config, "FACE_REPORT_DIR", str(reports)), patch(
                "app.faces.face_dataset.db.write_audit_log", side_effect=AssertionError,
            ), patch(
                "app.faces.face_dataset.db.list_face_enrollments", side_effect=AssertionError,
            ):
                result = await import_face_dataset(
                    root, consent_name="Admin", consent_date="2026-10-26",
                    consent_reference="CONSENT-1", analyzer=_FakeAnalyzer(),
                    dry_run=True,
                )
                self.assertTrue(Path(result["report_path"]).is_file())

        self.assertEqual(result["accepted"], 1)
        self.assertEqual(result["per_person"][0]["employee_code"], "E001")

    async def test_import_is_idempotent_and_skips_duplicate_hashes(self):
        repository = _MemoryFaceRepository()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            employee = root / "E001_Jane Doe"
            employee.mkdir()
            image = np.random.default_rng(5).integers(
                0, 256, (120, 120, 3), dtype=np.uint8,
            )
            ok, encoded = cv2.imencode(".jpg", image)
            self.assertTrue(ok)
            (employee / "photo.jpg").write_bytes(encoded.tobytes())
            report_dir = root / "reports"
            key_text = base64.urlsafe_b64encode(b"k" * 32).decode("ascii")
            with (
                patch.object(face_dataset.config, "FACE_ENCRYPTION_KEY", key_text),
                patch.object(face_dataset.config, "FACE_REPORT_DIR", str(report_dir)),
                patch.object(face_dataset.config, "FACE_KEEP_PHOTOS", False),
                patch.object(face_dataset.db, "list_face_enrollments", repository.list_face_enrollments),
                patch.object(face_dataset.db, "list_face_embeddings", repository.list_face_embeddings),
                patch.object(face_dataset.db, "upsert_face_enrollment", repository.upsert_face_enrollment),
                patch.object(face_dataset.db, "get_face_embedding_by_hash", repository.get_face_embedding_by_hash),
                patch.object(face_dataset.db, "insert_face_embedding", repository.insert_face_embedding),
                patch.object(face_dataset.db, "create_face_import_run", repository.create_face_import_run),
                patch.object(face_dataset.db, "write_audit_log", repository.write_audit_log),
            ):
                first = await import_face_dataset(
                    root,
                    consent_name="Safety Manager",
                    consent_date="2026-10-26",
                    consent_reference="CONSENT-001",
                    analyzer=_FakeAnalyzer(),
                )
                second = await import_face_dataset(
                    root,
                    consent_name="Safety Manager",
                    consent_date="2026-10-26",
                    consent_reference="CONSENT-001",
                    analyzer=_FakeAnalyzer(),
                )
                self.assertTrue(Path(first["report_path"]).is_file())

        self.assertEqual(first["accepted"], 1)
        self.assertEqual(second["accepted"], 0)
        self.assertEqual(second["duplicates"], 1)
        self.assertEqual(second["per_person"][0]["usable_photos"], 1)
        self.assertEqual(len(repository.embeddings), 1)
        self.assertEqual(len(repository.runs), 2)

    async def test_cross_identity_similarity_is_flagged_not_discarded(self):
        repository = _MemoryFaceRepository()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for code, name in (("E001", "Jane Doe"), ("E002", "John Doe")):
                folder = root / f"{code}_{name}"
                folder.mkdir()
                image_seed = sum(ord(character) for character in code + name)
                image = np.random.default_rng(image_seed).integers(
                    0, 256, (120, 120, 3), dtype=np.uint8,
                )
                ok, encoded = cv2.imencode(".jpg", image)
                self.assertTrue(ok)
                (folder / "photo.jpg").write_bytes(encoded.tobytes())
            key_text = base64.urlsafe_b64encode(b"z" * 32).decode("ascii")
            with (
                patch.object(face_dataset.config, "FACE_ENCRYPTION_KEY", key_text),
                patch.object(face_dataset.config, "FACE_REPORT_DIR", str(root / "reports")),
                patch.object(face_dataset.config, "FACE_KEEP_PHOTOS", False),
                patch.object(face_dataset.config, "FACE_CROSS_ID_DUPLICATE_THRESHOLD", 0.85),
                patch.object(face_dataset.db, "list_face_enrollments", repository.list_face_enrollments),
                patch.object(face_dataset.db, "list_face_embeddings", repository.list_face_embeddings),
                patch.object(face_dataset.db, "upsert_face_enrollment", repository.upsert_face_enrollment),
                patch.object(face_dataset.db, "get_face_embedding_by_hash", repository.get_face_embedding_by_hash),
                patch.object(face_dataset.db, "insert_face_embedding", repository.insert_face_embedding),
                patch.object(face_dataset.db, "create_face_import_run", repository.create_face_import_run),
                patch.object(face_dataset.db, "write_audit_log", repository.write_audit_log),
            ):
                result = await import_face_dataset(
                    root,
                    consent_name="Safety Manager",
                    consent_date="2026-10-26",
                    consent_reference="CONSENT-002",
                    analyzer=_FakeAnalyzer(),
                )

        self.assertEqual(result["accepted"], 2)
        self.assertTrue(any(
            flag["type"] == "cross_identity_embedding_similarity"
            for flag in result["flags"]
        ))

    async def test_within_person_outlier_is_flagged_without_removal(self):
        repository = _MemoryFaceRepository()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            employee = root / "E001_Jane Doe"
            employee.mkdir()
            for filename, color in (
                ("one.png", (20, 30, 40)),
                ("two.png", (50, 60, 70)),
                ("three.png", (220, 230, 240)),
            ):
                image = np.full((100, 100, 3), color, dtype=np.uint8)
                ok, encoded = cv2.imencode(".png", image)
                self.assertTrue(ok)
                (employee / filename).write_bytes(encoded.tobytes())
            key_text = base64.urlsafe_b64encode(b"q" * 32).decode("ascii")
            with (
                patch.object(face_dataset.config, "FACE_ENCRYPTION_KEY", key_text),
                patch.object(face_dataset.config, "FACE_REPORT_DIR", str(root / "reports")),
                patch.object(face_dataset.config, "FACE_KEEP_PHOTOS", False),
                patch.object(face_dataset.config, "FACE_OUTLIER_THRESHOLD", 0.4),
                patch.object(face_dataset.db, "list_face_enrollments", repository.list_face_enrollments),
                patch.object(face_dataset.db, "list_face_embeddings", repository.list_face_embeddings),
                patch.object(face_dataset.db, "upsert_face_enrollment", repository.upsert_face_enrollment),
                patch.object(face_dataset.db, "get_face_embedding_by_hash", repository.get_face_embedding_by_hash),
                patch.object(face_dataset.db, "insert_face_embedding", repository.insert_face_embedding),
                patch.object(face_dataset.db, "create_face_import_run", repository.create_face_import_run),
                patch.object(face_dataset.db, "write_audit_log", repository.write_audit_log),
            ):
                result = await import_face_dataset(
                    root,
                    consent_name="Safety Manager",
                    consent_date="2026-10-26",
                    consent_reference="CONSENT-003",
                    analyzer=_OutlierAnalyzer(),
                )

        self.assertEqual(result["accepted"], 3)
        self.assertTrue(any(
            flag["type"] == "within_folder_outlier"
            for flag in result["flags"]
        ))
        self.assertEqual(len(repository.embeddings), 3)


if __name__ == "__main__":
    unittest.main()
