"""Evaluate a YOLO detector and write aggregate and per-class metrics to JSON."""
import argparse
import json
from pathlib import Path

from ultralytics import YOLO


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--data", required=True)
    parser.add_argument("--device", default="0")
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--output", default="evaluation.json")
    args = parser.parse_args()

    model = YOLO(args.model)
    metrics = model.val(data=args.data, device=args.device, imgsz=args.imgsz)
    box = metrics.box
    result = {
        "precision": float(box.mp),
        "recall": float(box.mr),
        "map50": float(box.map50),
        "map50_95": float(box.map),
        "per_class": {},
        "confusion_matrix": None,
    }
    for class_id, class_name in model.names.items():
        result["per_class"][str(class_name)] = {
            "precision": float(box.p[class_id]),
            "recall": float(box.r[class_id]),
            "map50": float(box.ap50[class_id]),
            "map50_95": float(box.ap[class_id]),
        }
    matrix = getattr(getattr(metrics, "confusion_matrix", None), "matrix", None)
    if matrix is not None:
        result["confusion_matrix"] = matrix.tolist()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Evaluation saved to {output.resolve()}")
    print(f"precision={result['precision']:.4f} recall={result['recall']:.4f} "
          f"mAP50={result['map50']:.4f} mAP50-95={result['map50_95']:.4f}")


if __name__ == "__main__":
    main()
