import unittest
from unittest.mock import patch

import numpy as np

from app.cameras.camera_manager import _encode_annotated_frame


class CameraManagerStreamingTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
