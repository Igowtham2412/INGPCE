import unittest
from unittest.mock import patch

from app.detection.detector import _resolve_device


class DetectorDeviceTests(unittest.TestCase):
    @patch("app.detection.detector.torch.cuda.is_available", return_value=True)
    def test_auto_uses_first_cuda_device_when_available(self, _cuda_available):
        self.assertEqual(_resolve_device("auto"), "0")

    @patch("app.detection.detector.torch.cuda.is_available", return_value=False)
    def test_auto_falls_back_to_cpu_without_cuda(self, _cuda_available):
        self.assertEqual(_resolve_device("auto"), "cpu")

    @patch("app.detection.detector.torch.cuda.is_available", return_value=True)
    def test_explicit_cpu_setting_is_preserved(self, _cuda_available):
        self.assertEqual(_resolve_device("cpu"), "cpu")

    @patch("app.detection.detector.torch.cuda.is_available", return_value=False)
    def test_explicit_cuda_request_falls_back_to_cpu(self, _cuda_available):
        self.assertEqual(_resolve_device("cuda"), "cpu")


if __name__ == "__main__":
    unittest.main()
