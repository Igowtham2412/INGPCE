"""Move existing helmet/phone evidence into type-specific folders."""
import argparse
import asyncio
import filecmp
import logging
import shutil
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import config, db  # noqa: E402

logger = logging.getLogger("ppe.evidence_organizer")
VIOLATION_TYPES = ("helmet", "no_helmet", "phone_detected")
EVIDENCE_COLUMNS = ("snapshot_path", "clean_snapshot_path", "video_path")


def _resolve_stored_path(stored_path: str) -> Path:
    stored = Path(stored_path)
    if not stored.is_absolute():
        stored = PROJECT_ROOT / stored
    return stored.resolve()


def _target_for_path(
    stored_path: str, base_dir: str, category: str,
) -> tuple[Path, str] | None:
    stored = Path(stored_path)
    configured_root = Path(base_dir)
    root = (
        PROJECT_ROOT / configured_root
        if not configured_root.is_absolute() else configured_root
    ).resolve()
    source = _resolve_stored_path(stored_path)
    try:
        relative = source.relative_to(root)
    except ValueError:
        return None

    if relative.parts and relative.parts[0] == category:
        return None
    target_relative = Path(category) / relative
    target = root / target_relative
    if stored.is_absolute():
        stored_target = str(target)
    else:
        stored_target = str(Path(base_dir) / target_relative)
    return target, stored_target


def _path_plan(violation: dict[str, Any]) -> dict[str, tuple[Path, str]]:
    category = config.EVIDENCE_CATEGORY_BY_VIOLATION.get(
        violation["violation_type"],
    )
    if category is None:
        return {}
    plan = {}
    for column in EVIDENCE_COLUMNS:
        stored_path = violation.get(column)
        if not stored_path:
            continue
        base_dir = (
            config.EVIDENCE_CLIP_DIR
            if column == "video_path" else config.SNAPSHOT_DIR
        )
        destination = _target_for_path(stored_path, base_dir, category)
        if destination is not None:
            plan[column] = destination
    return plan


async def _organize(apply: bool) -> dict[str, int]:
    await db.init_pool()
    moved = missing = unchanged = errors = 0
    try:
        async with db.get_pool().acquire() as connection:
            violations = await connection.fetch(
                """SELECT id, violation_type, snapshot_path,
                          clean_snapshot_path, video_path
                   FROM violations
                   WHERE violation_type = ANY($1::text[])
                   ORDER BY id""",
                list(VIOLATION_TYPES),
            )

        for violation in violations:
            plan = _path_plan(dict(violation))
            if not plan:
                unchanged += 1
                continue
            file_moves = []
            conflict = False
            for column, (destination, new_db_path) in plan.items():
                source_value = violation[column]
                source = _resolve_stored_path(source_value)
                if not source.is_file():
                    missing += 1
                    logger.warning(
                        "Incident %s has missing %s evidence: %s",
                        violation["id"], column, source,
                    )
                    continue
                if destination.exists():
                    if not destination.is_file() or not filecmp.cmp(
                        source, destination, shallow=False,
                    ):
                        logger.error(
                            "Incident %s destination already exists with different content: %s",
                            violation["id"], destination,
                        )
                        conflict = True
                        break
                file_moves.append((
                    column, source, destination, new_db_path,
                    not destination.exists(),
                ))
            if conflict:
                errors += 1
                continue
            if not file_moves:
                unchanged += 1
                continue
            if not apply:
                moved += len(file_moves)
                continue

            created_targets = []
            try:
                for _, source, destination, _, is_new in file_moves:
                    if not is_new:
                        continue
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
                    created_targets.append(destination)

                updates = {
                    column: new_db_path
                    for column, _, _, new_db_path, _ in file_moves
                }
                async with db.get_pool().acquire() as connection:
                    async with connection.transaction():
                        await connection.execute(
                            """UPDATE violations
                               SET snapshot_path = COALESCE($2, snapshot_path),
                                   clean_snapshot_path = COALESCE($3, clean_snapshot_path),
                                   video_path = COALESCE($4, video_path)
                               WHERE id = $1""",
                            violation["id"],
                            updates.get("snapshot_path"),
                            updates.get("clean_snapshot_path"),
                            updates.get("video_path"),
                        )
            except Exception:
                for destination in created_targets:
                    destination.unlink(missing_ok=True)
                logger.exception(
                    "Could not organize evidence for incident %s",
                    violation["id"],
                )
                errors += 1
                continue

            for _, source, destination, _, _ in file_moves:
                if source == destination or not source.is_file():
                    continue
                try:
                    source.unlink()
                except OSError:
                    logger.exception(
                        "Database points to organized evidence, but the old file remains: %s",
                        source,
                    )
                    errors += 1
            moved += len(file_moves)
    finally:
        await db.close_pool()

    return {
        "violations_found": len(violations),
        "files_moved": moved,
        "files_missing": missing,
        "violations_unchanged": unchanged,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Organize existing missing-helmet and phone-detected incident "
            "evidence into category folders. Preview by default."
        ),
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Copy evidence, update database paths, then remove old files.",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        result = asyncio.run(_organize(args.apply))
    except Exception:
        logger.exception("Evidence organization failed")
        return 1
    print("Applied changes." if args.apply else "Dry run; no files were changed.")
    for name, count in result.items():
        print(f"{name}: {count}")
    if result["errors"]:
        return 1
    if not args.apply:
        print("Run with --apply to organize the listed evidence.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
