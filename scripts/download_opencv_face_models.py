"""Install checksum-pinned OpenCV Zoo YuNet and SFace models locally."""
import argparse
import hashlib
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import config

logger = logging.getLogger("ppe.face_model_download")
_CHUNK_SIZE = 1024 * 1024
_OPENCV_ZOO_COMMIT = "48b10fa505aeb51fbd127fc60a5c660040e823b7"
_MODEL_ASSETS = (
    (
        config.FACE_YUNET_MODEL_FILENAME,
        "https://media.githubusercontent.com/media/opencv/opencv_zoo/"
        f"{_OPENCV_ZOO_COMMIT}/models/face_detection_yunet/"
        "face_detection_yunet_2023mar.onnx",
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
        232589,
    ),
    (
        config.FACE_SFACE_MODEL_FILENAME,
        "https://media.githubusercontent.com/media/opencv/opencv_zoo/"
        f"{_OPENCV_ZOO_COMMIT}/models/face_recognition_sface/"
        "face_recognition_sface_2021dec.onnx",
        "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
        38696353,
    ),
)
_LICENSE_ASSETS = (
    (
        "LICENSE-yunet.txt",
        "https://raw.githubusercontent.com/opencv/opencv_zoo/"
        f"{_OPENCV_ZOO_COMMIT}/models/face_detection_yunet/LICENSE",
        "c83b8120c50ccbd4c4f96edf53141bdd566ebb8f8e9227e415326aa1b1aba958",
        1085,
    ),
    (
        "LICENSE-sface.txt",
        "https://raw.githubusercontent.com/opencv/opencv_zoo/"
        f"{_OPENCV_ZOO_COMMIT}/models/face_recognition_sface/LICENSE",
        "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
        11358,
    ),
)


def _download_verified(
        url: str, expected_sha256: str, expected_size: int, destination: Path,
) -> None:
    if urlsplit(url).scheme.lower() != "https":
        raise ValueError("Model asset URL must use HTTPS")
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    received = 0
    response = requests.get(url, stream=True, timeout=(15, 120))
    try:
        response.raise_for_status()
        if urlsplit(response.url).scheme.lower() != "https":
            raise ValueError("Model asset redirect did not use HTTPS")
        with destination.open("wb") as output:
            for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
                if not chunk:
                    continue
                output.write(chunk)
                digest.update(chunk)
                received += len(chunk)
    finally:
        response.close()
    if received != expected_size or digest.hexdigest() != expected_sha256:
        destination.unlink(missing_ok=True)
        raise ValueError("Downloaded OpenCV Zoo asset failed size or SHA-256 verification")


def download_face_models(model_dir: Path | None = None) -> Path:
    if not config.FACE_LICENSE_ACCEPTED:
        raise RuntimeError(
            "Set PPE_FACE_LICENSE_ACCEPTED=true only after model-license review and authorization"
        )
    target = (model_dir or Path(config.FACE_MODEL_DIR)).expanduser().resolve()
    if target.exists():
        raise FileExistsError(
            "The face-model directory already exists; move it aside before installation"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".opencv-face-models-", dir=target.parent))
    try:
        for filename, url, sha256, size in (*_MODEL_ASSETS, *_LICENSE_ASSETS):
            _download_verified(url, sha256, size, staging / filename)
        os.replace(staging, target)
        logger.info("Installed verified YuNet/SFace models in %s", target)
        return target
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Download the pinned OpenCV Zoo YuNet/SFace models after local "
            "license acceptance. SHA-256 and file-size checks are enforced."
        ),
    )
    parser.add_argument(
        "--model-dir", type=Path, default=Path(config.FACE_MODEL_DIR),
        help="Target directory (defaults to PPE_FACE_MODEL_DIR)",
    )
    args = parser.parse_args()
    try:
        target = download_face_models(args.model_dir)
    except (OSError, requests.RequestException, ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    print(f"Installed verified local face models: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
