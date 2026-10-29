#!/usr/bin/env python3
"""
seed_employees.py — create the demo employee roster + face gallery.

Five example employees are defined (Ravi Kumar, Anita Sharma, John Mathew,
Priya Nair, Imran Khan). For each employee the script looks for enrollment
photos in:

    demo/employees/<EMPLOYEE_CODE>_<Name With Spaces>/*.jpg

  * Photos present  -> embeddings are computed with the real face analyzer
                       (needs the on-prem stack) and stored in the seed file.
  * Photos missing  -> pass --lfw to download 5 distinct people from the
                       LFW dataset (scikit-learn) into those folders so the
                       real-mode demo works anyway.
  * Neither         -> the seed file still contains the identities, which is
                       all the SIMULATION demo needs (synthetic embeddings).

The seed file (demo/seed_employees.json) feeds demo/run_demo.py and
demo/evaluate.py. To enroll the same people into the RUNNING app instead, use
the dashboard's face enrollment page or the /api/face endpoints.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

DEMO_DIR = Path(__file__).resolve().parent
EMPLOYEES = [
    {"employee_code": "EMP001", "name": "Ravi Kumar", "department": "Electrical", "shift": "A"},
    {"employee_code": "EMP002", "name": "Anita Sharma", "department": "Mechanical", "shift": "B"},
    {"employee_code": "EMP003", "name": "John Mathew", "department": "Civil", "shift": "A"},
    {"employee_code": "EMP004", "name": "Priya Nair", "department": "Electrical", "shift": "C"},
    {"employee_code": "EMP005", "name": "Imran Khan", "department": "Operations", "shift": "B"},
]


def photo_dir(employee: dict) -> Path:
    return DEMO_DIR / "employees" / (
        f"{employee['employee_code']}_{employee['name'].replace(' ', '_')}"
    )


def download_lfw_photos() -> bool:
    """Fetch 5 distinct people from LFW into the enrollment folders."""
    try:
        from sklearn.datasets import fetch_lfw_people
    except ImportError:
        print("scikit-learn not installed; skipping LFW download")
        return False
    print("Downloading LFW subset (~200MB first run)...")
    lfw = fetch_lfw_people(min_faces_per_person=20, resize=1.0, color=True)
    seen_people: set[int] = set()
    written = 0
    try:
        from PIL import Image
    except ImportError:
        print("Pillow not installed; cannot save LFW photos")
        return False
    for index, target in enumerate(lfw.target):
        if target in seen_people or len(seen_people) >= len(EMPLOYEES):
            continue
        seen_people.add(target)
        employee = EMPLOYEES[len(seen_people) - 1]
        folder = photo_dir(employee)
        folder.mkdir(parents=True, exist_ok=True)
        image = lfw.images[index]
        if image.ndim == 3:
            image = image.transpose(1, 2, 0)  # CHW -> HWC
        Image.fromarray((image * 255).astype("uint8"), "RGB").save(
            folder / "lfw_01.jpg", quality=92,
        )
        print(f"  {employee['name']}: 1 LFW photo -> {folder}")
        written += 1
    return written >= len(EMPLOYEES) or written > 0


def compute_real_embeddings(employee: dict, photos: list[Path]) -> list[list[float]]:
    """Embed photos with the app's real analyzer (on-prem stack only)."""
    import sys
    import cv2
    import numpy as np

    if str(DEMO_DIR.parent) not in sys.path:
        sys.path.insert(0, str(DEMO_DIR.parent))
    from app.faces.face_dataset import create_face_analyzer

    analyzer = create_face_analyzer()
    embeddings: list[list[float]] = []
    for photo in photos[:10]:
        image = cv2.imread(str(photo), cv2.IMREAD_COLOR)
        if image is None:
            print(f"  !! could not read {photo}")
            continue
        try:
            embedding, _ = analyzer.analyze(image)
        except Exception as exc:
            print(f"  !! rejected {photo.name}: {getattr(exc, 'reason', exc)}")
            continue
        embeddings.append(np.asarray(embedding, dtype=float).tolist())
    return embeddings


def build_seed(include_embeddings: bool = True) -> list[dict]:
    employees = []
    for employee in EMPLOYEES:
        folder = photo_dir(employee)
        photos = sorted(folder.glob("*.jpg")) if folder.exists() else []
        entry = {**employee, "photo_paths": [str(p) for p in photos]}
        if photos:
            print(f"{employee['name']}: {len(photos)} photo(s)")
            if include_embeddings:
                try:
                    entry["embeddings"] = compute_real_embeddings(employee, photos)
                    print(f"  computed {len(entry['embeddings'])} embedding(s)")
                except ImportError:
                    print("  real analyzer unavailable here; "
                          "simulation demo will use synthetic embeddings")
        else:
            print(f"{employee['name']}: no photos "
                  f"(drop .jpg files into {folder})")
        employees.append(entry)
    return employees


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lfw", action="store_true",
                        help="download LFW sample faces for missing employees")
    parser.add_argument("--no-embeddings", action="store_true",
                        help="skip real-embedding computation (identity-only seed)")
    parser.add_argument("--out", default=str(DEMO_DIR / "seed_employees.json"))
    args = parser.parse_args()

    include_embeddings = not args.no_embeddings
    employees = build_seed(include_embeddings)
    if not any(e["photo_paths"] for e in employees) and args.lfw:
        print("No photos found; downloading LFW samples...")
        if download_lfw_photos():
            employees = build_seed(include_embeddings)

    out = Path(args.out)
    out.write_text(json.dumps({"employees": employees}, indent=2))
    print(f"\nWrote {out} ({len(employees)} employees)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
