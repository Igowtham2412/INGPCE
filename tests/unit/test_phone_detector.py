import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from app import config
from app.detector import PPEDetector


class Value:
    def __init__(self, value):
        self.value = value

    def item(self):
        return self.value


class Coordinates(list):
    def tolist(self):
        return list(self)


class FakeBox:
    def __init__(self, class_id, confidence, coordinates):
        self.cls = [Value(class_id)]
        self.conf = [Value(confidence)]
        self.xyxy = [Coordinates(coordinates)]


class PhoneDetectorTests(unittest.TestCase):
    def test_phone_model_predictions_are_added_and_confidence_filtered(self):
        ppe_model = SimpleNamespace(
            names={0: "Person", 2: "NO-Hardhat"},
            predict=Mock(
                return_value=[SimpleNamespace(boxes=[
                    FakeBox(0, 0.95, [10, 10, 100, 190]),
                    FakeBox(2, 0.3, [45, 12, 60, 30]),
                ])]
            ),
        )
        phone_model = SimpleNamespace(
            names={67: "cell phone"},
            predict=Mock(
                return_value=[SimpleNamespace(boxes=[
                    FakeBox(67, 0.2, [80, 70, 95, 90]),
                ])]
            ),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            phone_model_path = Path(temp_dir) / "phone-model.pt"
            phone_model_path.touch()
            with (
                patch.object(config, "PHONE_MODEL_PATH", str(phone_model_path)),
                patch("app.detector.YOLO", side_effect=[ppe_model, phone_model]),
                patch("app.detector.torch.set_num_threads") as set_num_threads,
            ):
                detector = PPEDetector(model_path="ppe-model.pt", device="cpu")
                detections = detector.infer(
                    np.zeros((200, 200, 3), dtype="uint8"),
                )

        self.assertEqual(
            [(detection.cls_norm, detection.confidence) for detection in detections],
            [("person", 0.95), ("no_helmet", 0.3), ("phone", 0.2)],
        )
        phone_model.predict.assert_called_once()
        self.assertEqual(
            phone_model.predict.call_args.kwargs["classes"],
            [67],
        )
        self.assertFalse(ppe_model.predict.call_args.kwargs["half"])
        self.assertFalse(phone_model.predict.call_args.kwargs["half"])
        self.assertEqual(ppe_model.predict.call_args.kwargs["device"], "cpu")
        self.assertEqual(phone_model.predict.call_args.kwargs["device"], "cpu")
        set_num_threads.assert_called_once_with(config.CPU_INFERENCE_THREADS)

    def test_cuda_failure_retries_in_fp32_on_cpu(self):
        model = SimpleNamespace(
            predict=Mock(side_effect=[
                RuntimeError("CUDA out of memory"),
                ["cpu result"],
            ]),
        )
        detector = PPEDetector.__new__(PPEDetector)
        detector.device = "0"
        detector.half = True

        with patch("app.detector.torch.set_num_threads") as set_num_threads:
            result = detector._predict(model, source=np.zeros((2, 2, 3)))

        self.assertEqual(result, ["cpu result"])
        self.assertEqual(model.predict.call_count, 2)
        self.assertTrue(model.predict.call_args_list[0].kwargs["half"])
        self.assertFalse(model.predict.call_args_list[1].kwargs["half"])
        self.assertEqual(model.predict.call_args_list[1].kwargs["device"], "cpu")
        self.assertEqual(detector.device, "cpu")
        set_num_threads.assert_called_once_with(config.CPU_INFERENCE_THREADS)


if __name__ == "__main__":
    unittest.main()
