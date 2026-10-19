"""Build a YOLO data config from reviewed incidents and held-out validation data."""
import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="dataset/feedback")
    parser.add_argument("--validation-dir", required=True,
                        help="Separate, manually labeled validation images directory")
    args = parser.parse_args()
    dataset = Path(args.dataset).resolve()
    validation = Path(args.validation_dir).resolve()
    validation_images = [
        path for path in validation.rglob("*")
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp"}
    ]
    if not validation_images:
        parser.error(f"validation directory has no images: {validation}")

    classes_path = dataset / "classes.json"
    if not classes_path.is_file():
        parser.error(f"no reviewed feedback class map found: {classes_path}")
    classes = json.loads(classes_path.read_text(encoding="utf-8"))
    class_ids = sorted(int(class_id) for class_id in classes)
    if class_ids != list(range(len(class_ids))):
        parser.error("model class IDs must be contiguous from zero to generate a YOLO dataset")
    names = [classes[str(class_id)] for class_id in class_ids]
    config = {
        "path": str(dataset),
        "train": ["confirmed/images", "false_positive/images"],
        "val": str(validation),
        "names": names,
    }
    output = dataset / "data.yaml"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(config, indent=2), encoding="utf-8")
    print(f"Wrote {output}; training samples are model-assisted review crops.")
    print("Verify/correct all YOLO labels and use a separately labeled validation set before training.")


if __name__ == "__main__":
    main()
