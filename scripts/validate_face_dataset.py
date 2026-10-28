"""Validate face dataset layout, consent markers, and image files without a model."""
import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import config  # noqa: E402
from app.face_validation import validate_face_dataset  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Check dataset layout, consent fields, image readability and exact "
            "duplicates without loading InsightFace or a model."
        ),
    )
    parser.add_argument(
        "--dataset", type=Path, default=Path(config.FACE_DATASET_PATH),
        help="Employee folder root or CSV/Excel manifest",
    )
    parser.add_argument(
        "--min-photos", type=int, default=5,
        help="Recommended minimum per employee (default: 5)",
    )
    parser.add_argument("--consent-name", help="Bulk-consent confirmer for folder layout")
    parser.add_argument("--consent-date", help="Bulk-consent date (YYYY-MM-DD)")
    parser.add_argument("--consent-reference", help="Internal consent record reference")
    parser.add_argument("--report", type=Path, help="Optional JSON report file")
    args = parser.parse_args()

    try:
        report = validate_face_dataset(
            args.dataset, args.min_photos, args.consent_name,
            args.consent_date, args.consent_reference,
        )
    except (OSError, ValueError) as exc:
        print(f"Dataset validation failed: {exc}", file=sys.stderr)
        return 2

    print(
        f"Dataset validation: {'PASS' if report['valid'] else 'NEEDS ATTENTION'}; "
        f"layout={report['layout']}; people={len(report['people'])}; "
        f"readable={report['readable_images']}/{report['total_images']}; "
        f"duplicates={report['duplicate_images']}; "
        f"errors={sum(issue['level'] == 'error' for issue in report['issues'])}; "
        f"warnings={len(report['warnings'])}"
    )
    for person in report["people"]:
        print(
            f"{person['employee_code']}: {person['readable_photos']} readable "
            f"of {person['photos_found']}, {person['duplicate_photos']} duplicates"
        )
    for issue in report["issues"]:
        print(f"ERROR {issue['code']}")
    for warning in report["warnings"]:
        print(f"WARNING {warning['code']}")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, indent=2, sort_keys=True), encoding="utf-8",
        )
        print(f"JSON report written to: {args.report}")
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
