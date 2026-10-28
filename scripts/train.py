"""train.py - fine-tune YOLOv8 on your own annotated PPE footage."""
import argparse
from pathlib import Path

from ultralytics import YOLO

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--base-model", default="yolov8n.pt")
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--device", default="0")
    parser.add_argument("--project", default="runs/ppe_train")
    args = parser.parse_args()
    data_path = Path(args.data)
    if not data_path.is_file():
        parser.error(f"dataset config does not exist: {data_path}")
    model = YOLO(args.base_model)
    model.train(data=args.data, epochs=args.epochs, imgsz=args.imgsz, batch=args.batch,
                device=args.device, project=args.project, name="ppe_model")
    print(f"Best weights: {args.project}/ppe_model/weights/best.pt")
