import os
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from app.cameras.camera_manager import (
    convert_speed_clip_to_webm,
    _encode_annotated_frame,
    _group_violation_people,
    _save_speed_snapshot,
)


class CameraManagerStreamingTests(unittest.TestCase):
    def test_same_policy_violations_group_people_without_merging_boxes(self):
        statuses = [
            type("Status", (), {
                "is_violation": True,
                "missing_ppe": ["helmet"],
                "phone_detected": False,
                "track_id": track_id,
                "box": box,
            })()
            for track_id, box in (
                (11, (10, 20, 50, 90)),
                (22, (60, 25, 100, 95)),
            )
        ]

        grouped = _group_violation_people(statuses, {}, 10.0, 5.0)

        self.assertEqual(len(grouped), 1)
        people = grouped["no_helmet"]
        self.assertEqual(
            [(status.track_id, status.box) for status, _ in people],
            [(11, (10, 20, 50, 90)), (22, (60, 25, 100, 95))],
        )

    @patch("app.cameras.camera_manager.cv2.imencode")
    @patch("app.cameras.camera_manager._resize_to_tile")
    @patch("app.cameras.camera_manager._draw_annotations")
    def test_annotated_frame_is_encoded_to_jpeg_bytes(
        self, draw, resize, imencode,
    ):
        frame = np.zeros((2, 2, 3), dtype=np.uint8)
        annotated = np.ones((2, 2, 3), dtype=np.uint8)
        draw.return_value = annotated
        resize.return_value = annotated
        imencode.return_value = (True, np.array([1, 2, 3], dtype=np.uint8))

        result = _encode_annotated_frame(frame, [], "CAM01")

        self.assertEqual(result, b"\x01\x02\x03")
        draw.assert_called_once()
        resize.assert_called_once_with(frame)
        self.assertIs(draw.call_args.args[0], annotated)

    @patch("app.cameras.camera_manager.cv2.imencode", return_value=(False, None))
    @patch("app.cameras.camera_manager._resize_to_tile")
    @patch("app.cameras.camera_manager._draw_annotations")
    def test_failed_jpeg_encode_is_not_reported_as_a_frame(
        self, _draw, _resize, _imencode,
    ):
        frame = np.zeros((2, 2, 3), dtype=np.uint8)
        self.assertIsNone(_encode_annotated_frame(frame, [], "CAM01"))

    @patch("app.cameras.camera_manager.cv2.imwrite", return_value=True)
    @patch("app.cameras.camera_manager._draw_annotations")
    def test_speed_snapshot_includes_speed_reading_and_gate_annotations(
        self, draw, imwrite,
    ):
        from app.detection.speed_tracking import SpeedReading

        frame = np.zeros((2, 2, 3), dtype=np.uint8)
        annotated = np.ones((2, 2, 3), dtype=np.uint8)
        draw.return_value = annotated
        reading = SpeedReading(
            3, "car", (0, 0, 2, 2), 22, 15, True, "A-to-B", 1000,
        )
        settings = {"enabled": True, "gate_a": [], "gate_b": []}

        _save_speed_snapshot(
            "snapshot.jpg", frame, [], "CAM01", reading, settings,
        )

        self.assertIsNot(draw.call_args.args[0], frame)
        self.assertEqual(draw.call_args.kwargs["speed_readings"], [reading])
        self.assertEqual(draw.call_args.kwargs["speed_settings"], settings)
        imwrite.assert_called_once_with("snapshot.jpg", annotated)

    def test_existing_mp4_speed_clip_converts_to_browser_playable_webm(self):
        with tempfile.TemporaryDirectory() as directory:
            mp4_path = os.path.join(directory, "speed.mp4")
            writer = cv2.VideoWriter(
                mp4_path, cv2.VideoWriter_fourcc(*"mp4v"), 4.0, (64, 64),
            )
            self.assertTrue(writer.isOpened())
            for value in (32, 96, 160, 224):
                writer.write(np.full((64, 64, 3), value, dtype=np.uint8))
            writer.release()

            webm_path = convert_speed_clip_to_webm(mp4_path)

            self.assertEqual(os.path.splitext(webm_path)[1], ".webm")
            capture = cv2.VideoCapture(webm_path)
            self.assertTrue(capture.isOpened())
            success, frame = capture.read()
            capture.release()

        self.assertTrue(success)
        self.assertEqual(frame.shape, (64, 64, 3))


if __name__ == "__main__":
    unittest.main()
