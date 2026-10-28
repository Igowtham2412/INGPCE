"""Provision the locally licensed InsightFace buffalo_l model pack."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402


def main() -> int:
    if not config.FACE_LICENSE_ACCEPTED:
        raise SystemExit(
            "Set PPE_FACE_LICENSE_ACCEPTED=true only after reviewing "
            "InsightFace and buffalo_l model terms and authorization."
        )
    try:
        from insightface.app import FaceAnalysis
    except ImportError as exc:
        raise SystemExit(
            "Install the requirements first; InsightFace is not available."
        ) from exc

    model_root = Path(config.FACE_MODEL_DIR).expanduser().resolve()
    model_dir = model_root / "models" / "buffalo_l"
    if model_dir.exists() and any(model_dir.iterdir()):
        raise SystemExit(
            f"Refusing to overwrite an existing model directory: {model_dir}"
        )

    FaceAnalysis(
        name="buffalo_l",
        root=str(model_root),
        allowed_modules=["detection", "recognition"],
        providers=["CPUExecutionProvider"],
    )
    model_files = sorted(model_dir.glob("*.onnx"))
    if not model_files:
        raise SystemExit("InsightFace did not provision the buffalo_l ONNX files.")
    print(f"Provisioned {len(model_files)} buffalo_l ONNX models under {model_dir}")
    print("Record their checksums and retain the model license notices.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
