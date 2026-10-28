"""Create a blank identity-label manifest for a flat incident image folder."""
import argparse
import csv
from pathlib import Path

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
FIELDNAMES = ("image_path", "camera_id", "violation_type", "employee_id")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create a CSV template for labeling incident images by camera, "
            "violation type, and identity."
        ),
    )
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    image_root = args.images.expanduser().resolve(strict=True)
    if not image_root.is_dir():
        parser.error("--images must point to a directory")
    output = args.output.expanduser().resolve()
    if output.parent != image_root:
        parser.error("--output must be directly inside the --images directory")
    if output.exists():
        parser.error(f"Refusing to overwrite an existing manifest: {output}")

    images = []
    for path in sorted(image_root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        resolved = path.resolve(strict=True)
        try:
            resolved.relative_to(image_root)
        except ValueError:
            parser.error("Image symlinks must remain inside the image directory")
        images.append(resolved.relative_to(image_root).as_posix())
    if not images:
        parser.error("No JPG, JPEG, or PNG images were found")

    with output.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows({
            "image_path": image_path,
            "camera_id": "",
            "violation_type": "",
            "employee_id": "",
        } for image_path in images)
    print(f"Created blank calibration manifest for {len(images)} images: {output}")
    print(
        "Fill camera_id, violation_type, and employee_id; use "
        "'not_in_dataset' for people absent from the enrolled gallery."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
