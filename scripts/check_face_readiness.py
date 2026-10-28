"""Report local face package/model/dataset readiness without loading weights."""
import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import db  # noqa: E402
from app.faces.face_readiness import get_face_readiness  # noqa: E402


async def _run() -> dict:
    await db.init_pool()
    try:
        enrolled_people = await db.count_active_face_enrollments()
    finally:
        await db.close_pool()
    return get_face_readiness(enrolled_people)


def main() -> int:
    argparse.ArgumentParser(
        description=(
            "Check local face packages, providers, license approval, model, "
            "dataset, enrollment count, and feature state."
        ),
    ).parse_args()
    try:
        readiness = asyncio.run(_run())
    except Exception as exc:
        print(
            f"Readiness check failed ({type(exc).__name__}); "
            "database status could not be determined.",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(readiness, indent=2, sort_keys=True))
    return 0 if readiness["ready_for_recognition"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
