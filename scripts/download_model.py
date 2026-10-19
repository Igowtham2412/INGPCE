"""download_model.py - fetch a pretrained PPE YOLO model from Hugging Face."""
import argparse, os
from huggingface_hub import hf_hub_download

DEFAULT_OPTIONS = {
    "Hansung-Cho/yolov8-ppe-detection": "best.pt",
    "Tanishjain9/yolov8n-ppe-detection-6classes": "best.pt",
    "keremberke/yolov8m-protective-equipment-detection": "best.pt",
}

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default="Hansung-Cho/yolov8-ppe-detection")
    parser.add_argument("--filename", default=None)
    parser.add_argument("--out", default="models/best.pt")
    args = parser.parse_args()
    filename = args.filename or DEFAULT_OPTIONS.get(args.repo, "best.pt")
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    print(f"Downloading {filename} from {args.repo} ...")
    path = hf_hub_download(repo_id=args.repo, filename=filename)
    import shutil
    shutil.copy(path, args.out)
    print(f"Saved model to {args.out}")
