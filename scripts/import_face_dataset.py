"""Import the configured, consented employee face dataset."""
import argparse
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Import consented employee face references locally. "
            "No face-model downloads are performed."
        ),
    )
    parser.add_argument(
        "--dataset", type=Path,
        help="Dataset folder or CSV/Excel manifest (defaults to PPE_FACE_DATASET_PATH)",
    )
    parser.add_argument(
        "--consent-name",
        help="Name of the person confirming bulk consent for folder imports",
    )
    parser.add_argument(
        "--consent-date",
        help="Date bulk consent was recorded, in YYYY-MM-DD format",
    )
    parser.add_argument(
        "--consent-reference",
        help="Internal policy, form, or consent record reference",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Run model quality checks and produce a report without database writes",
    )
    parser.add_argument(
        "--report-only", action="store_true",
        help="Run model-free layout, consent, and image checks without importing",
    )
    return parser


async def _run(args: argparse.Namespace):
    from app import db
    from app.faces.face_dataset import import_face_dataset

    if args.dry_run:
        return await import_face_dataset(
            dataset_path=args.dataset,
            consent_name=args.consent_name,
            consent_date=args.consent_date,
            consent_reference=args.consent_reference,
            dry_run=True,
        )

    await db.init_pool()
    try:
        return await import_face_dataset(
            dataset_path=args.dataset,
            consent_name=args.consent_name,
            consent_date=args.consent_date,
            consent_reference=args.consent_reference,
            actor_email="local dataset import",
            dry_run=False,
        )
    finally:
        await db.close_pool()


def main() -> int:
    import json

    from app import config
    from app.faces.face_dataset import (
        ConsentError,
        FaceModelUnavailable,
        print_import_summary,
    )

    args = _parser().parse_args()
    if args.dry_run and args.report_only:
        print("--dry-run and --report-only are mutually exclusive", file=sys.stderr)
        return 2
    if args.report_only:
        from app.faces.face_validation import validate_face_dataset

        report = validate_face_dataset(
            args.dataset or Path(config.FACE_DATASET_PATH),
            consent_name=args.consent_name,
            consent_date=args.consent_date,
            consent_reference=args.consent_reference,
        )
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["valid"] else 1
    try:
        result = asyncio.run(_run(args))
    except (ConsentError, FaceModelUnavailable) as exc:
        print(f"Face dataset import blocked or failed: {exc}", file=sys.stderr)
        return 2
    except (ValueError, RuntimeError, OSError) as exc:
        print(
            f"Face dataset import failed ({type(exc).__name__}); "
            "sensitive details were omitted.",
            file=sys.stderr,
        )
        return 2
    print_import_summary(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
