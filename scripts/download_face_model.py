"""Download a licensed, checksum-verified local face model pack."""
import argparse
import hashlib
import logging
import shutil
import stat
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import config  # noqa: E402

logger = logging.getLogger("ppe.face_model_download")
_CHUNK_SIZE = 1024 * 1024


def _safe_extract(archive: zipfile.ZipFile, destination: Path) -> None:
    root = destination.resolve()
    for entry in archive.infolist():
        normalized = entry.filename.replace("\\", "/")
        member = PurePosixPath(normalized)
        if (
            member.is_absolute()
            or not member.parts
            or any(part in {"", ".", ".."} for part in member.parts)
            or ":" in member.parts[0]
        ):
            raise ValueError("Model archive contains an unsafe path")
        mode = entry.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise ValueError("Model archive contains a symbolic link")
        target = (destination / Path(*member.parts)).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ValueError("Model archive contains an unsafe path") from exc
        if entry.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(entry) as source, target.open("wb") as output:
            shutil.copyfileobj(source, output)


def _model_directory(staging: Path, model_name: str) -> Path:
    direct = staging / model_name
    if direct.is_dir() and any(direct.rglob("*.onnx")):
        return direct
    if any(staging.glob("*.onnx")):
        return staging
    candidates = [
        path for path in staging.iterdir()
        if path.is_dir() and any(path.rglob("*.onnx"))
    ]
    if len(candidates) == 1:
        return candidates[0]
    raise ValueError("Downloaded archive does not contain a recognizable ONNX model pack")


def download_face_model(
        url: str, expected_sha256: str, model_name: str | None = None) -> Path:
    if not config.FACE_LICENSE_ACCEPTED:
        logger.warning(
            "Face model download is blocked; PPE_FACE_LICENSE_ACCEPTED is not true",
        )
        raise RuntimeError(
            "Set PPE_FACE_LICENSE_ACCEPTED=true only after model-license review and authorization"
        )
    if not url.lower().startswith("https://"):
        raise ValueError("Model archive URL must use HTTPS")
    expected_sha256 = expected_sha256.strip().lower()
    if len(expected_sha256) != 64 or any(
        char not in "0123456789abcdef" for char in expected_sha256
    ):
        raise ValueError("--sha256 must be a 64-character hexadecimal digest")

    name = model_name or config.FACE_MODEL_NAME
    if (
        not name or name in {".", ".."}
        or "/" in name or "\\" in name
        or Path(name).name != name
    ):
        raise ValueError("Model name must be a single directory name")
    model_root = Path(config.FACE_MODEL_DIR).resolve()
    model_root.mkdir(parents=True, exist_ok=True)
    target = (model_root / name).resolve()
    try:
        target.relative_to(model_root)
    except ValueError as exc:
        raise ValueError("Model name must remain under the configured model directory") from exc
    if target.exists():
        raise FileExistsError(
            "The configured model directory already exists; move it aside before installing"
        )

    archive_path = None
    staging = None
    try:
        with tempfile.NamedTemporaryFile(
                prefix=".face-model-", suffix=".zip", dir=model_root,
                delete=False) as archive_file:
            archive_path = Path(archive_file.name)
            response = requests.get(url, stream=True, timeout=(15, 120))
            try:
                response.raise_for_status()
                if not response.url.lower().startswith("https://"):
                    raise ValueError("Model archive redirect did not use HTTPS")
                for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
                    if chunk:
                        archive_file.write(chunk)
            finally:
                response.close()

        digest = hashlib.sha256()
        with archive_path.open("rb") as archive_file:
            for block in iter(lambda: archive_file.read(_CHUNK_SIZE), b""):
                digest.update(block)
        if digest.hexdigest() != expected_sha256:
            raise ValueError("Downloaded model archive SHA-256 did not match")

        staging = Path(tempfile.mkdtemp(prefix=".face-model-stage-", dir=model_root))
        try:
            with zipfile.ZipFile(archive_path) as archive:
                _safe_extract(archive, staging)
        except zipfile.BadZipFile as exc:
            raise ValueError("Downloaded model archive is not a valid ZIP file") from exc
        source = _model_directory(staging, name)
        shutil.move(str(source), str(target))
        logger.info("Installed checksum-verified face model pack '%s'", name)
        return target
    finally:
        if archive_path is not None:
            archive_path.unlink(missing_ok=True)
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Download a local face model only after explicit license acceptance. "
            "A trusted SHA-256 is required; this script does not choose a model."
        ),
    )
    parser.add_argument("--url", required=True, help="HTTPS URL for the licensed model ZIP")
    parser.add_argument(
        "--sha256", required=True,
        help="Trusted SHA-256 checksum published by the authorized model supplier",
    )
    parser.add_argument(
        "--name", default=config.FACE_MODEL_NAME,
        help="Installed model-pack directory name (defaults to PPE_FACE_MODEL_NAME)",
    )
    return parser


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    try:
        path = download_face_model(args.url, args.sha256, args.name)
    except (OSError, requests.RequestException, ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    print(f"Installed verified local face model pack: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
