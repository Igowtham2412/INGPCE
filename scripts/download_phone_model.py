"""Download and verify the Ultralytics YOLO26m COCO model used for phone detection."""
import argparse
import os
import shutil
import tempfile
from pathlib import Path

from ultralytics import YOLO


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="models/yolo26m.pt")
    args = parser.parse_args()
    target = Path(args.out).resolve()
    if target.exists():
        model = YOLO(str(target))
        if not any(name.lower() == "cell phone" for name in model.names.values()):
            parser.error(f"existing model has no COCO cell phone class: {target}")
        print(f"Phone detection model is already verified: {target}")
        return

    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ingpce-phone-model-") as temp_dir:
        original_directory = Path.cwd()
        try:
            os.chdir(temp_dir)
            model = YOLO("yolo26m.pt")
        finally:
            os.chdir(original_directory)

        names = model.names
        if not any(name.lower() == "cell phone" for name in names.values()):
            parser.error("Downloaded YOLO26m model does not contain the COCO cell phone class")

        downloaded = Path(temp_dir) / "yolo26m.pt"
        if not downloaded.is_file():
            parser.error("Ultralytics did not save the downloaded model in the temporary directory")
        shutil.copy2(downloaded, target)

    print(f"Verified phone detection model: {target}")


if __name__ == "__main__":
    main()
