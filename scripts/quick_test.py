"""quick_test.py - visually verify PPE detection on ONE camera, no DB needed."""
import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
import argparse

import cv2

from app import config
from app.compliance_engine import ComplianceEngine
from app.detector import PPEDetector


def draw(frame, statuses):
    for status in statuses:
        x1, y1, x2, y2 = status.box
        color = (0, 0, 255) if status.is_violation else (0, 200, 0)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        label = f"ID{status.track_id} " + ("MISSING: " + ",".join(sorted(status.missing_ppe)) if status.is_violation else "OK")
        cv2.putText(frame, label, (x1, max(20, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
    return frame

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--camera-id", default="TEST")
    parser.add_argument("--required-ppe", default="helmet,vest")
    args = parser.parse_args()
    required_ppe = [p.strip() for p in args.required_ppe.split(",") if p.strip()]
    print(f"Loading model from {config.MODEL_PATH} ...")
    detector = PPEDetector()
    engine = ComplianceEngine(args.camera_id, required_ppe, detector.normalized_classes)
    source = args.source
    if source.isdigit(): source = int(source)
    if isinstance(source, str) and source.lower().endswith((".jpg", ".jpeg", ".png", ".bmp")):
        frame = cv2.imread(source)
        if frame is None: print(f"ERROR: could not read {source}"); return
        detections = detector.infer(frame, args.camera_id)
        for _ in range(config.VIOLATION_CONFIRM_FRAMES):
            statuses = engine.evaluate(detections, frame.shape)
        cv2.imwrite("quick_test_output.jpg", draw(frame.copy(), statuses))
        print("Saved quick_test_output.jpg"); return
    print(f"Connecting to {source} ...")
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        print("ERROR: could not open source. Check RTSP URL/credentials/network/firewall."); return
    print("Connected! Press 'q' to quit.")
    frame_count = 0; last_statuses = []
    while True:
        ok, frame = cap.read()
        if not ok: print("Stream ended."); break
        frame_count += 1
        if frame_count % config.FRAME_SKIP == 0:
            last_statuses = engine.evaluate(detector.infer(frame, args.camera_id), frame.shape)
        try:
            cv2.imshow("PPE Quick Test", draw(frame.copy(), last_statuses))
            if cv2.waitKey(1) & 0xFF == ord("q"): break
        except cv2.error:
            if frame_count % 30 == 0:
                cv2.imwrite("quick_test_output.jpg", draw(frame.copy(), last_statuses))
                print(f"[headless] wrote quick_test_output.jpg at frame {frame_count}")
    cap.release(); cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
