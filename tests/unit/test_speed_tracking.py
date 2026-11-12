import unittest

from app.detection.speed_tracking import (
    VehicleSpeedTracker,
    gates_intersect,
)


class VehicleSpeedTrackerTests(unittest.TestCase):
    def setUp(self):
        self.tracker = VehicleSpeedTracker(
            gate_a=[[0.1, 0.25], [0.9, 0.25]],
            gate_b=[[0.1, 0.75], [0.9, 0.75]],
            distance_m=8,
            speed_limit_kmh=15,
        )

    def test_estimates_speed_for_a_vehicle_crossing_both_calibrated_gates(self):
        self.tracker.update_tracks([(4, (40, 0, 60, 10), "car")], 0)
        self.tracker.update_tracks([(4, (40, 10, 60, 30), "car")], 1)
        readings = self.tracker.update_tracks(
            [(4, (40, 60, 60, 80), "car")],
            3,
        )

        self.assertEqual(len(readings), 1)
        self.assertEqual(readings[0].speed_kmh, 14.0)
        self.assertFalse(readings[0].speeding)
        self.assertEqual(readings[0].direction, "A-to-B")

    def test_marks_speeding_measurement_above_the_configured_limit(self):
        self.tracker.update_tracks([(9, (40, 0, 60, 10), "truck")], 0)
        self.tracker.update_tracks([(9, (40, 10, 60, 30), "truck")], 1)
        readings = self.tracker.update_tracks(
            [(9, (40, 60, 60, 80), "truck")],
            2,
        )

        self.assertEqual(readings[0].speed_kmh, 25.0)
        self.assertTrue(readings[0].speeding)
        self.assertEqual(readings[0].vehicle_type, "truck")

    def test_interpolates_both_gate_crossings_between_sparse_detections(self):
        self.tracker.update_tracks([(3, (40, 0, 60, 10), "car")], 0)
        readings = self.tracker.update_tracks(
            [(3, (40, 80, 60, 90), "car")],
            1,
        )

        self.assertEqual(len(readings), 1)
        self.assertEqual(readings[0].speed_kmh, 46.1)
        self.assertTrue(readings[0].speeding)

    def test_ignores_motion_that_does_not_cross_the_gate_segment(self):
        self.tracker.update_tracks([(1, (0, 0, 10, 10), "car")], 0)
        readings = self.tracker.update_tracks(
            [(1, (0, 10, 10, 30), "car")],
            1,
        )

        self.assertEqual(readings, [])

    def test_detects_crossing_calibration_gates(self):
        self.assertTrue(gates_intersect(
            [[0.05, 0.7], [0.82, 0.37]],
            [[0.03, 0.27], [0.83, 0.66]],
        ))

    def test_accepts_separate_parallel_calibration_gates(self):
        self.assertFalse(gates_intersect(
            [[0.1, 0.25], [0.9, 0.25]],
            [[0.1, 0.75], [0.9, 0.75]],
        ))


if __name__ == "__main__":
    unittest.main()
