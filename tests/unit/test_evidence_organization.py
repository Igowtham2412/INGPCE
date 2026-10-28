import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import config
from scripts.organize_violation_evidence import _path_plan, _target_for_path


class EvidenceOrganizationTests(unittest.TestCase):
    def test_helmet_and_phone_evidence_use_distinct_directories(self):
        self.assertEqual(
            Path(config.evidence_directory("snapshots", "no_helmet")),
            Path("snapshots") / "helmet_violations",
        )
        self.assertEqual(
            Path(config.evidence_directory("evidence/clips", "phone_detected")),
            Path("evidence/clips") / "phone_detected",
        )

    def test_other_violation_types_remain_in_the_existing_directory(self):
        self.assertEqual(
            config.evidence_directory("snapshots", "no_vest"),
            "snapshots",
        )

    def test_path_plan_covers_snapshots_and_clips_for_legacy_helmet_type(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = root / "snapshots"
            clips = root / "clips"
            with (
                patch.object(config, "SNAPSHOT_DIR", str(snapshots)),
                patch.object(config, "EVIDENCE_CLIP_DIR", str(clips)),
            ):
                plan = _path_plan({
                    "violation_type": "helmet",
                    "snapshot_path": str(snapshots / "incident_annotated.jpg"),
                    "clean_snapshot_path": str(snapshots / "incident_clean.jpg"),
                    "video_path": str(clips / "violation_12.mp4"),
                })

        self.assertEqual(
            plan["snapshot_path"][0],
            snapshots / "helmet_violations" / "incident_annotated.jpg",
        )
        self.assertEqual(
            plan["clean_snapshot_path"][0],
            snapshots / "helmet_violations" / "incident_clean.jpg",
        )
        self.assertEqual(
            plan["video_path"][0],
            clips / "helmet_violations" / "violation_12.mp4",
        )

    def test_path_plan_ignores_other_violation_types(self):
        self.assertEqual(_path_plan({
            "violation_type": "no_vest",
            "snapshot_path": "snapshots/incident.jpg",
            "clean_snapshot_path": None,
            "video_path": None,
        }), {})

    def test_paths_outside_configured_evidence_roots_are_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            result = _target_for_path(
                str(Path(directory) / "outside.jpg"),
                str(Path(directory) / "snapshots"),
                "phone_detected",
            )

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
