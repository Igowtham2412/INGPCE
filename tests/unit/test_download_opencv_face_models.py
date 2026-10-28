import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.download_opencv_face_models import (
    _download_verified,
    download_face_models,
)


class DownloadOpenCVFaceModelsTests(unittest.TestCase):
    def test_license_gate_blocks_before_downloading(self):
        with patch(
            "scripts.download_opencv_face_models.config.FACE_LICENSE_ACCEPTED",
            False,
        ), patch(
            "scripts.download_opencv_face_models._download_verified",
        ) as download:
            with self.assertRaisesRegex(RuntimeError, "PPE_FACE_LICENSE_ACCEPTED"):
                download_face_models(Path("unused"))

        download.assert_not_called()

    def test_download_verifies_checksum_and_size(self):
        payload = b"trusted model asset"
        response = Mock()
        response.url = "https://media.githubusercontent.com/model.onnx"
        response.iter_content.return_value = [payload]
        response.raise_for_status.return_value = None

        with tempfile.TemporaryDirectory() as directory, patch(
            "scripts.download_opencv_face_models.requests.get",
            return_value=response,
        ) as get:
            destination = Path(directory) / "model.onnx"
            _download_verified(
                "https://media.githubusercontent.com/model.onnx",
                hashlib.sha256(payload).hexdigest(),
                len(payload),
                destination,
            )
            self.assertEqual(destination.read_bytes(), payload)

        get.assert_called_once()
        response.close.assert_called_once()

    def test_mismatched_asset_is_removed(self):
        payload = b"untrusted model asset"
        response = Mock()
        response.url = "https://media.githubusercontent.com/model.onnx"
        response.iter_content.return_value = [payload]
        response.raise_for_status.return_value = None

        with tempfile.TemporaryDirectory() as directory, patch(
            "scripts.download_opencv_face_models.requests.get",
            return_value=response,
        ):
            destination = Path(directory) / "model.onnx"
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                _download_verified(
                    "https://media.githubusercontent.com/model.onnx",
                    "0" * 64,
                    len(payload),
                    destination,
                )

        self.assertFalse(destination.exists())

    def test_installs_models_and_notices_as_one_directory(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            "scripts.download_opencv_face_models.config.FACE_LICENSE_ACCEPTED",
            True,
        ), patch(
            "scripts.download_opencv_face_models._MODEL_ASSETS",
            (("yunet.onnx", "https://models.invalid/yunet", "a" * 64, 10),),
        ), patch(
            "scripts.download_opencv_face_models._LICENSE_ASSETS",
            (("LICENSE.txt", "https://models.invalid/license", "b" * 64, 12),),
        ), patch(
            "scripts.download_opencv_face_models._download_verified",
            side_effect=lambda _url, _sha, size, path: path.write_bytes(
                b"x" * size,
            ),
        ):
            target = Path(directory) / "models" / "face"
            installed = download_face_models(target)
            self.assertEqual(installed, target.resolve())
            self.assertEqual((installed / "yunet.onnx").stat().st_size, 10)
            self.assertEqual((installed / "LICENSE.txt").stat().st_size, 12)



if __name__ == "__main__":
    unittest.main()
