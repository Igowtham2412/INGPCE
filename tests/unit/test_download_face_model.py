import hashlib
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.download_face_model import download_face_model


def _model_archive():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("buffalo_l/detector.onnx", b"synthetic model bytes")
    return buffer.getvalue()


class DownloadFaceModelTests(unittest.TestCase):
    def test_license_gate_blocks_before_any_network_request(self):
        with patch("scripts.download_face_model.config.FACE_LICENSE_ACCEPTED", False), patch(
            "scripts.download_face_model.requests.get",
        ) as get:
            with self.assertRaisesRegex(RuntimeError, "PPE_FACE_LICENSE_ACCEPTED"):
                download_face_model(
                    "https://supplier.invalid/model.zip", "0" * 64,
                )

        get.assert_not_called()

    def test_download_requires_valid_https_digest_and_safe_zip(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            "scripts.download_face_model.config.FACE_LICENSE_ACCEPTED", True,
        ), patch(
            "scripts.download_face_model.config.FACE_MODEL_DIR", directory,
        ):
            with self.assertRaisesRegex(ValueError, "HTTPS"):
                download_face_model("http://supplier.invalid/model.zip", "0" * 64)
            with self.assertRaisesRegex(ValueError, "64-character"):
                download_face_model("https://supplier.invalid/model.zip", "abc")

    def test_download_installs_only_a_checksum_verified_pack(self):
        payload = _model_archive()
        response = Mock()
        response.url = "https://supplier.invalid/model.zip"
        response.iter_content.return_value = [payload]
        response.raise_for_status.return_value = None

        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "scripts.download_face_model.config.FACE_LICENSE_ACCEPTED", True,
            ), patch(
                "scripts.download_face_model.config.FACE_MODEL_DIR", directory,
            ), patch(
                "scripts.download_face_model.requests.get", return_value=response,
            ) as get:
                installed = download_face_model(
                    "https://supplier.invalid/model.zip",
                    hashlib.sha256(payload).hexdigest(),
                )

            get.assert_called_once()
            response.close.assert_called_once()
            self.assertEqual(
                (Path(directory) / "buffalo_l" / "detector.onnx").read_bytes(),
                b"synthetic model bytes",
            )
            self.assertEqual(installed, Path(directory) / "buffalo_l")

    def test_checksum_mismatch_never_extracts_pack(self):
        payload = _model_archive()
        response = Mock()
        response.url = "https://supplier.invalid/model.zip"
        response.iter_content.return_value = [payload]
        response.raise_for_status.return_value = None

        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "scripts.download_face_model.config.FACE_LICENSE_ACCEPTED", True,
            ), patch(
                "scripts.download_face_model.config.FACE_MODEL_DIR", directory,
            ), patch(
                "scripts.download_face_model.requests.get", return_value=response,
            ), self.assertRaisesRegex(ValueError, "SHA-256"):
                download_face_model(
                    "https://supplier.invalid/model.zip", "0" * 64,
                )

            self.assertFalse((Path(directory) / "buffalo_l").exists())

    def test_zip_path_traversal_is_rejected(self):
        payload_buffer = io.BytesIO()
        with zipfile.ZipFile(payload_buffer, "w") as archive:
            archive.writestr("../escaped.onnx", b"not safe")
            archive.writestr("buffalo_l/detector.onnx", b"model")
        payload = payload_buffer.getvalue()
        response = Mock()
        response.url = "https://supplier.invalid/model.zip"
        response.iter_content.return_value = [payload]
        response.raise_for_status.return_value = None

        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "scripts.download_face_model.config.FACE_LICENSE_ACCEPTED", True,
            ), patch(
                "scripts.download_face_model.config.FACE_MODEL_DIR", directory,
            ), patch(
                "scripts.download_face_model.requests.get", return_value=response,
            ), self.assertRaisesRegex(ValueError, "unsafe path"):
                download_face_model(
                    "https://supplier.invalid/model.zip",
                    hashlib.sha256(payload).hexdigest(),
                )

            self.assertFalse((Path(directory).parent / "escaped.onnx").exists())
