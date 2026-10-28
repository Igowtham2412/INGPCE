"""Calibrate optional local face matching thresholds from consented data."""
import argparse
import asyncio
from collections import Counter
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402
from app.faces.face_calibration import (  # noqa: E402
    evaluate_queries,
    incident_queries,
    leave_one_out_queries,
    load_labeled_incident_crops,
    load_reference_samples,
    write_calibration_report,
)
from app.faces.face_dataset import create_face_analyzer  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Estimate face-score thresholds locally. Results are not accuracy "
            "claims and must be validated on representative camera footage."
        ),
    )
    parser.add_argument(
        "--dataset", type=Path, default=Path(config.FACE_DATASET_PATH),
        help="Primary consented face dataset folder or CSV",
    )
    parser.add_argument(
        "--incident-crops", type=Path,
        help="Optional labeled incident-crop directory for camera-specific evaluation",
    )
    parser.add_argument(
        "--target-far", type=float, default=0.001,
        help="Target empirical false-accept rate, default 0.001 (0.1%%)",
    )
    parser.add_argument(
        "--min-margin", type=float, default=config.FACE_MIN_MARGIN,
        help="Minimum score margin used for decision metrics (defaults to PPE_FACE_MIN_MARGIN)",
    )
    parser.add_argument("--consent-name", help="Bulk-consent confirmer")
    parser.add_argument("--consent-date", help="Bulk-consent date (YYYY-MM-DD)")
    parser.add_argument("--consent-reference", help="Internal consent record reference")
    parser.add_argument(
        "--output-dir", type=Path, default=Path(config.FACE_REPORT_DIR),
        help="Private output directory for JSON results and score histogram CSV",
    )
    return parser


async def _run(args: argparse.Namespace) -> int:
    if not 0 < args.target_far <= 1:
        raise ValueError("--target-far must be greater than 0 and at most 1")
    if args.min_margin < 0:
        raise ValueError("--min-margin cannot be negative")

    analyzer = create_face_analyzer()
    references, rejected_references = load_reference_samples(
        args.dataset, analyzer, args.consent_name,
        args.consent_date, args.consent_reference,
    )
    queries = leave_one_out_queries(references)
    report = {
        "model_name": analyzer.model_name,
        "model_version": analyzer.model_version,
        "target_false_accept_rate": args.target_far,
        "primary_reference_people": len(references),
        "primary_accepted_photos": sum(map(len, references.values())),
        "primary_rejected_photos_by_reason": rejected_references,
        "validation_scope": "primary_dataset_leave_one_out_only",
        "labeled_incident_crops_included": False,
        "warning": (
            "Leave-one-out estimates are not camera accuracy claims. "
            "Any supplied incident crops must be representative, consented footage."
        ),
    }
    if args.incident_crops:
        incident_samples, rejected_crops, crop_counts = load_labeled_incident_crops(
            args.incident_crops, references.keys(), analyzer,
        )
        queries.extend(incident_queries(
            incident_samples, references,
            config.FACE_MATCH_STRATEGY, config.FACE_MATCH_TOP_K,
        ))
        report["validation_scope"] = (
            "primary_dataset_leave_one_out_and_labeled_incident_crops"
        )
        report["labeled_incident_crops_included"] = True
        report["incident_crop_counts_by_camera"] = crop_counts
        report["incident_crop_counts_by_violation_and_camera"] = dict(
            Counter(
                f"{sample.violation_type}|{sample.camera}"
                for sample in incident_samples
            )
        )
        report["incident_crop_rejected_by_reason"] = rejected_crops

    if not queries:
        raise ValueError(
            "No leave-one-out queries were available; each tested employee needs "
            "at least two quality-approved reference photos"
        )
    report.update(evaluate_queries(queries, args.target_far, args.min_margin))
    report["match_strategy"] = config.FACE_MATCH_STRATEGY
    report["match_top_k"] = config.FACE_MATCH_TOP_K
    report["quality_min_by_violation"] = config.FACE_QUALITY_MIN_BY_VIOLATION
    json_path, histogram_path = write_calibration_report(report, args.output_dir)
    print(json.dumps({
        "validation_scope": report["validation_scope"],
        "query_count": report["query_count"],
        "groups": report["groups"],
        "by_violation_type": report["by_violation_type"],
        "json_report": str(json_path),
        "histogram_csv": str(histogram_path),
        "warning": report["warning"],
    }, indent=2, sort_keys=True))
    return 0


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    try:
        return asyncio.run(_run(args))
    except (OSError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
