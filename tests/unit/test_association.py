"""Unit tests for track association: IoU math, stable track ids, and
violation->person attachment (phone box inside a person box)."""
import unittest

from app.detection.association import (
    IoUTracker,
    iou,
    overlap_fraction_of_b,
)


class IouMathTests(unittest.TestCase):
    def test_identical_boxes(self):
        self.assertAlmostEqual(iou((0, 0, 10, 10), (0, 0, 10, 10)), 1.0)

    def test_half_overlap(self):
        self.assertAlmostEqual(iou((0, 0, 10, 10), (0, 0, 10, 5)), 0.5)

    def test_disjoint_boxes(self):
        self.assertEqual(iou((0, 0, 10, 10), (20, 20, 30, 30)), 0.0)

    def test_contained_box(self):
        # phone fully inside a person box: overlap fraction of the phone is 1
        self.assertAlmostEqual(
            overlap_fraction_of_b((0, 0, 100, 200), (40, 60, 50, 80)), 1.0,
        )

    def test_partial_containment(self):
        self.assertAlmostEqual(
            overlap_fraction_of_b((0, 0, 10, 10), (5, 0, 15, 10)), 0.5,
        )


class IoUTrackerTests(unittest.TestCase):
    def test_stable_ids_for_moving_people(self):
        tracker = IoUTracker(iou_threshold=0.2)
        frame1 = tracker.update([(100, 100, 140, 250), (400, 100, 440, 250)])
        frame2 = tracker.update([(108, 100, 148, 250), (392, 100, 432, 250)])
        self.assertEqual(sorted(frame1), sorted(frame2))

    def test_new_person_gets_new_id(self):
        tracker = IoUTracker(iou_threshold=0.2)
        frame1 = tracker.update([(100, 100, 140, 250)])
        existing_id = next(iter(frame1))
        frame2 = tracker.update([(100, 100, 140, 250), (600, 100, 640, 250)])
        self.assertEqual(len(frame2), 2)
        self.assertTrue(
            all(track_id != existing_id for track_id in frame2
                if frame2[track_id] == (600, 100, 640, 250)),
            "the new person must receive a fresh track id",
        )

    def test_brief_occlusion_keeps_id(self):
        tracker = IoUTracker(iou_threshold=0.2, max_misses=3)
        frame1 = tracker.update([(100, 100, 140, 250)])
        person_id = next(iter(frame1))
        tracker.update([])            # person hidden for one frame
        tracker.update([])
        frame4 = tracker.update([(116, 100, 156, 250)])
        self.assertIn(person_id, frame4)

    def test_long_occlusion_retires_id(self):
        tracker = IoUTracker(iou_threshold=0.2, max_misses=2)
        frame1 = tracker.update([(100, 100, 140, 250)])
        person_id = next(iter(frame1))
        for _ in range(3):
            tracker.update([])
        frame5 = tracker.update([(100, 100, 140, 250)])
        self.assertNotIn(person_id, frame5, "id must be retired after max misses")

    def test_approaching_people_keep_distinct_ids(self):
        """Two people walk toward each other; ids stay 1:1 with persons."""
        tracker = IoUTracker(iou_threshold=0.2, max_misses=5)
        left, right = (100, 100, 140, 250), (340, 100, 380, 250)
        frame1 = tracker.update([left, right])
        frame2 = tracker.update(
            [(120, 100, 160, 250), (320, 100, 360, 250)],
        )
        self.assertEqual(sorted(frame1), sorted(frame2))
        self.assertEqual(len(frame2), 2)


class ViolationAttachmentTests(unittest.TestCase):
    def test_phone_attaches_to_overlapping_person_only(self):
        person_a = (100, 100, 140, 250)
        person_b = (400, 100, 440, 250)
        phone_near_a = (108, 150, 118, 165)
        phone_near_b = (408, 150, 418, 165)
        for person, phone, expected in (
            (person_a, phone_near_a, True),
            (person_a, phone_near_b, False),
            (person_b, phone_near_b, True),
        ):
            attached = (
                overlap_fraction_of_b(person, phone) >= 0.9
                and person[0] - 40 <= (phone[0] + phone[2]) / 2 <= person[2] + 40
                and person[1] - 20 <= (phone[1] + phone[3]) / 2 <= person[3] + 20
            )
            self.assertEqual(attached, expected)


if __name__ == "__main__":
    unittest.main()
