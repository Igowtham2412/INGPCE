"""Unit tests for violation debounce (persistence confirmation + cooldown).
Mirrors the semantics of the live ComplianceEngine temporal confirmation."""
import unittest

from app.detection.debounce import ViolationDebouncer


class ViolationDebouncerTests(unittest.TestCase):
    def test_single_frame_does_not_confirm(self):
        debouncer = ViolationDebouncer(confirm_frames=3, window=8)
        self.assertFalse(debouncer.observe("cam", 1, "no_helmet", True))
        self.assertFalse(debouncer.is_confirmed("cam", 1, "no_helmet"))

    def test_confirms_only_on_n_th_frame(self):
        debouncer = ViolationDebouncer(confirm_frames=3, window=8)
        results = [debouncer.observe("cam", 1, "no_helmet", True) for _ in range(5)]
        self.assertEqual(results, [False, False, True, False, False])
        self.assertTrue(debouncer.is_confirmed("cam", 1, "no_helmet"))

    def test_independent_per_track_and_type(self):
        debouncer = ViolationDebouncer(confirm_frames=2, window=8)
        debouncer.observe("cam", 1, "no_helmet", True)
        debouncer.observe("cam", 1, "no_helmet", True)
        # Same camera, different track -> not confirmed
        self.assertFalse(debouncer.is_confirmed("cam", 2, "no_helmet"))
        # Same track, different violation type -> not confirmed
        self.assertFalse(debouncer.is_confirmed("cam", 1, "phone_usage"))

    def test_intermittent_detection_still_confirms_within_window(self):
        """Detections in 3 of 8 frames confirm (occlusion-tolerant)."""
        debouncer = ViolationDebouncer(confirm_frames=3, window=8)
        pattern = [True, False, True, False, True, False, False, False]
        outcomes = [debouncer.observe("cam", 1, "no_helmet", p) for p in pattern]
        self.assertIn(True, outcomes)
        # but a clean frame after confirmation clears the flag
        debouncer.observe("cam", 1, "no_helmet", False)
        self.assertFalse(debouncer.is_confirmed("cam", 1, "no_helmet"))

    def test_clean_frame_clears_confirmation(self):
        debouncer = ViolationDebouncer(confirm_frames=2, window=8)
        for _ in range(3):
            debouncer.observe("cam", 1, "phone_usage", True)
        self.assertTrue(debouncer.is_confirmed("cam", 1, "phone_usage"))
        debouncer.observe("cam", 1, "phone_usage", False)
        self.assertFalse(debouncer.is_confirmed("cam", 1, "phone_usage"))

    def test_cooldown_blocks_repeated_logging(self):
        now = [1000.0]
        debouncer = ViolationDebouncer(
            confirm_frames=1, window=8, cooldown_seconds=60,
            clock=lambda: now[0],
        )
        self.assertTrue(debouncer.should_log("cam", 1, "no_helmet"))
        now[0] += 10
        self.assertFalse(debouncer.should_log("cam", 1, "no_helmet"))
        now[0] += 51
        self.assertTrue(debouncer.should_log("cam", 1, "no_helmet"))

    def test_prune_stale_drops_lost_tracks(self):
        debouncer = ViolationDebouncer(confirm_frames=1, window=8)
        debouncer.observe("cam", 1, "no_helmet", True)
        debouncer.should_log("cam", 1, "no_helmet")
        debouncer.prune_stale({("cam", 2, "no_helmet")})
        self.assertFalse(debouncer.is_confirmed("cam", 1, "no_helmet"))

    def test_invalid_settings_rejected(self):
        with self.assertRaises(ValueError):
            ViolationDebouncer(confirm_frames=0)
        with self.assertRaises(ValueError):
            ViolationDebouncer(confirm_frames=5, window=4)


if __name__ == "__main__":
    unittest.main()
