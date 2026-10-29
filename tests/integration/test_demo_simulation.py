"""Integration test: the demo pipeline end-to-end in SIMULATION mode.

Exercises the full flow without cameras, models, or a database:
detection (synthetic) -> IoU tracking -> violation debounce -> best-N face
aggregation -> gallery matching -> decision -> evidence + results files.

Requires numpy (+ Pillow for evidence rendering, optional).
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from demo.run_demo import run_simulation, write_outputs
from demo.seed_employees import EMPLOYEES
from app.faces.track_identity import (
    IdentityThresholds,
    STATUS_MATCHED,
    STATUS_UNKNOWN,
)


class DemoSimulationIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.evidence_dir = Path(cls.tmp.name) / "evidence"
        cls.thresholds = IdentityThresholds(0.60, 0.45, 0.05)
        cls.seed = {"employees": [
            {"employee_code": e["employee_code"], "name": e["name"],
             "department": e["department"]}
            for e in EMPLOYEES
        ]}
        cls.records = run_simulation(
            cls.seed, cls.evidence_dir, frames=90, thresholds=cls.thresholds,
        )
        write_outputs(cls.records, cls.evidence_dir)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_three_violations_recorded(self):
        # Anita (no helmet), John (phone), intruder (no helmet)
        self.assertEqual(len(self.records), 3)

    def test_enrolled_employee_identified_with_score(self):
        anita = next(
            r for r in self.records if r["employee_code"] == "EMP002"
        )
        self.assertEqual(anita["violation_type"], "no_helmet")
        self.assertEqual(anita["status"], STATUS_MATCHED)
        self.assertIsNotNone(anita["match_score"])
        self.assertGreaterEqual(anita["match_score"], 0.60)
        self.assertGreaterEqual(anita["faces_aggregated"], 3,
                                "identity must come from multiple frames")

    def test_phone_user_identified_after_face_improves(self):
        """Phone tilt delays the face; identity must wait for a better frame."""
        john = next(
            r for r in self.records if r["employee_code"] == "EMP003"
        )
        self.assertEqual(john["violation_type"], "phone_usage")
        self.assertEqual(john["status"], STATUS_MATCHED)
        self.assertGreaterEqual(john["frame_idx"], 50,
                                "phone-tilt user identified only once the "
                                "face becomes usable")

    def test_unenrolled_intruder_is_unknown_not_misidentified(self):
        intruder = next(
            r for r in self.records if r["employee_code"] is None
        )
        self.assertEqual(intruder["status"], STATUS_UNKNOWN)
        self.assertIsNotNone(intruder["match_score"],
                             "score must be stored even for unknowns")

    def test_all_records_are_clearly_labeled_simulated(self):
        for record in self.records:
            self.assertTrue(record["simulated"])

    def test_results_files_written(self):
        results = json.loads(
            (self.evidence_dir / "results.json").read_text(),
        )
        self.assertEqual(len(results), 3)
        self.assertTrue((self.evidence_dir / "results.csv").exists())

    def test_compliant_person_never_recorded(self):
        """Ravi Kumar wears his helmet and stays off his phone."""
        self.assertFalse(
            any(r.get("employee_code") == "EMP001" for r in self.records)
        )


if __name__ == "__main__":
    unittest.main()
