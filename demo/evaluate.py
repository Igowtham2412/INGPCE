#!/usr/bin/env python3
"""
evaluate.py — identification quality report: precision / recall / FAR.

Given a gallery (from the seed file) and a labeled probe set, this reports:
  * precision & recall of IDENTIFIED decisions vs ground truth
  * false-accept rate (unknown probes wrongly MATCHED)
  * score distribution per probe class

Modes:
  default            synthetic probes per employee + unknown intruders
                     (SIMULATION — runs anywhere; identities are synthetic)
  --test-dir <dir>   real probes from <dir>/<employee_code>/*.jpg, embedded
                     with the real analyzer (on-prem stack required)

Usage:
  python demo/evaluate.py
  python demo/evaluate.py --test-dir ./test_faces --report eval_real.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np

from app.faces.track_identity import (
    IdentityThresholds,
    STATUS_MATCHED,
    STATUS_NEEDS_REVIEW,
    STATUS_UNKNOWN,
    decide_identity,
    rank_gallery,
)

DEMO_DIR = Path(__file__).resolve().parent


def build_gallery(seed: dict, analyzer) -> dict[int, dict]:
    gallery = {}
    for index, employee in enumerate(seed.get("employees", []), start=1):
        embeddings = employee.get("embeddings")
        if not embeddings:
            embeddings = [analyzer._base_for(
                f"emp:{employee['employee_code']}"
            )]
        gallery[index] = {
            "employee_code": employee["employee_code"],
            "full_name": employee["name"],
            "department": employee.get("department", ""),
            "embeddings": [np.asarray(v, dtype=np.float32) for v in embeddings],
        }
    return gallery


def simulation_probes(seed: dict, analyzer, per_employee: int = 3):
    """Labeled probes: (true_employee_code, embedding, quality)."""
    probes = []
    # Noise levels calibrated to realistic cosine behaviour for unit vectors:
    # noise-norm 0.5 -> cos~0.89 (MATCHED), 1.0 -> cos~0.71 (MATCHED),
    # 1.5 -> cos~0.55 (NEEDS_REVIEW borderline -> human review queue).
    for employee in seed.get("employees", []):
        label = f"emp:{employee['employee_code']}"
        base = analyzer._base_for(label)
        for n in range(per_employee):
            rng = np.random.default_rng(abs(hash(f"{label}:{n}")) % (2**32))
            noise_norm = 0.5 + 0.5 * n
            probes.append((
                employee["employee_code"],
                base + (noise_norm / np.sqrt(base.size))
                * rng.normal(size=base.size),
                0.85 - 0.1 * n,
            ))
    # Unknown intruders (ground truth None) — must NOT be matched.
    for n in range(3):
        base = analyzer._base_for(f"intruder:{n}")
        probes.append((None, base, 0.85))
    return probes


def real_probes(test_dir: Path):
    import sys
    import cv2

    if str(DEMO_DIR.parent) not in sys.path:
        sys.path.insert(0, str(DEMO_DIR.parent))
    from app.faces.face_dataset import create_face_analyzer

    analyzer = create_face_analyzer()
    probes = []
    for folder in sorted(test_dir.iterdir()):
        if not folder.is_dir():
            continue
        for photo in sorted(folder.glob("*.jpg")):
            image = cv2.imread(str(photo), cv2.IMREAD_COLOR)
            if image is None:
                continue
            try:
                embedding, measurements = analyzer.analyze(image)
            except Exception as exc:
                print(f"skip {photo}: {getattr(exc, 'reason', exc)}")
                continue
            probes.append((
                folder.name, embedding,
                measurements.get("quality_score", 0.0),
            ))
    return probes


def evaluate(probes, gallery, thresholds: IdentityThresholds) -> dict:
    confusion = {
        "true_positive": 0, "false_positive": 0,
        "false_negative": 0, "true_negative": 0, "needs_review": 0,
    }
    rows = []
    for true_code, embedding, quality in probes:
        ranked = rank_gallery(np.asarray(embedding, dtype=np.float32), gallery)
        decision = decide_identity(
            ranked, thresholds.match_threshold,
            thresholds.suggest_threshold, thresholds.min_margin,
        )
        matched_code = None
        if decision.person_id is not None and decision.status != STATUS_UNKNOWN:
            matched_code = gallery[decision.person_id]["employee_code"]
        correct = matched_code is not None and matched_code == true_code
        if decision.status == STATUS_NEEDS_REVIEW:
            confusion["needs_review"] += 1
        elif true_code is None:
            # Unknown ground truth: MATCHED == false accept
            if decision.status == STATUS_MATCHED:
                confusion["false_positive"] += 1
            else:
                confusion["true_negative"] += 1
        else:
            if decision.status == STATUS_MATCHED and correct:
                confusion["true_positive"] += 1
            else:
                confusion["false_negative"] += 1
        rows.append({
            "true_code": true_code,
            "status": decision.status,
            "matched_code": matched_code,
            "score": round(decision.score, 4) if decision.score else None,
            "correct": correct,
        })
    tp = confusion["true_positive"]
    fp = confusion["false_positive"]
    fn = confusion["false_negative"]
    tn = confusion["true_negative"]
    known = tp + fn
    unknowns = fp + tn
    return {
        "confusion": confusion,
        "precision": round(tp / (tp + fp), 4) if tp + fp else None,
        "recall": round(tp / known, 4) if known else None,
        "false_accept_rate": round(fp / unknowns, 4) if unknowns else None,
        "needs_review_rate": round(
            confusion["needs_review"] / len(probes), 4,
        ) if probes else None,
        "probes": rows,
        "mode": "simulation" if rows and rows[0]["true_code"] else "real",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test-dir", default=None,
                        help="real probe folder <dir>/<employee_code>/*.jpg")
    parser.add_argument("--seed", default=str(DEMO_DIR / "seed_employees.json"))
    parser.add_argument("--report", default=str(DEMO_DIR / "eval_report.json"))
    parser.add_argument("--match-threshold", type=float, default=0.60)
    parser.add_argument("--suggest-threshold", type=float, default=0.45)
    parser.add_argument("--min-margin", type=float, default=0.05)
    args = parser.parse_args()

    seed = json.loads(Path(args.seed).read_text())
    thresholds = IdentityThresholds(
        args.match_threshold, args.suggest_threshold, args.min_margin,
    )
    if args.test_dir:
        probes = real_probes(Path(args.test_dir))
    else:
        try:
            from run_demo import SimulatedAnalyzer  # script mode
        except ImportError:
            from .run_demo import SimulatedAnalyzer  # package mode
        analyzer = SimulatedAnalyzer()
        probes = simulation_probes(seed, analyzer)
        gallery = build_gallery(seed, analyzer)
        # probes built on the same synthetic space as the gallery
        report = evaluate(probes, gallery, thresholds)
        report["mode"] = "simulation"
        Path(args.report).write_text(json.dumps(report, indent=2))
        c = report["confusion"]
        print("=== [SIMULATED] identification evaluation (synthetic) ===")
        print(f"probes: {len(probes)}  TP {c['true_positive']}  "
              f"FP {c['false_positive']}  FN {c['false_negative']}  "
              f"TN {c['true_negative']}  needs_review {c['needs_review']}")
        print(f"precision {report['precision']}  recall {report['recall']}  "
              f"false-accept rate {report['false_accept_rate']}")
        print(f"report -> {args.report}")
        return 0
    gallery = build_gallery_from_seed(seed)
    report = evaluate(probes, gallery, thresholds)
    report["mode"] = "real"
    Path(args.report).write_text(json.dumps(report, indent=2))
    print(f"probes: {len(probes)} precision {report['precision']} "
          f"recall {report['recall']} FAR {report['false_accept_rate']}")
    print(f"report -> {args.report}")
    return 0


def build_gallery_from_seed(seed: dict) -> dict[int, dict]:
    """Real-mode gallery from stored embeddings in the seed file."""
    gallery = {}
    for index, employee in enumerate(seed.get("employees", []), start=1):
        embeddings = employee.get("embeddings") or []
        gallery[index] = {
            "employee_code": employee["employee_code"],
            "full_name": employee["name"],
            "department": employee.get("department", ""),
            "embeddings": [np.asarray(v, dtype=np.float32) for v in embeddings],
        }
    return gallery


if __name__ == "__main__":
    raise SystemExit(main())
