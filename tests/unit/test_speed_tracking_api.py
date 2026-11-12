import unittest
import tempfile
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from pydantic import ValidationError

from app import main


class CameraSpeedTrackingApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.camera = {"camera_id": "CAM12", "enabled": True}
        self.saved = {
            "camera_id": "CAM12",
            "enabled": True,
            "distance_m": 8,
            "speed_limit_kmh": 15,
            "gate_a": [[0.1, 0.25], [0.9, 0.25]],
            "gate_b": [[0.1, 0.75], [0.9, 0.75]],
        }
        self.manager = SimpleNamespace(
            refresh_camera=AsyncMock(return_value=True),
        )
        self.payload = main.CameraSpeedSettingsIn(**{
            key: value for key, value in self.saved.items()
            if key not in {"camera_id", "enabled"}
        })

    async def test_saves_calibration_and_reloads_camera_worker(self):
        with (
            patch.object(main.db, "get_camera", new=AsyncMock(
                return_value=self.camera,
            )),
            patch.object(main.db, "set_camera_speed_settings", new=AsyncMock(
                return_value=self.saved,
            )) as save_settings,
            patch.object(main, "_camera_manager", self.manager),
        ):
            result = await main.update_camera_speed_settings(
                "CAM12", self.payload,
            )

        self.assertEqual(result, self.saved)
        save_settings.assert_awaited_once_with(
            "CAM12",
            8,
            15,
            [[0.1, 0.25], [0.9, 0.25]],
            [[0.1, 0.75], [0.9, 0.75]],
        )
        self.manager.refresh_camera.assert_awaited_once_with(
            "CAM12", restart_stream=False,
        )

    async def test_cannot_calibrate_a_disabled_camera(self):
        with (
            patch.object(main.db, "get_camera", new=AsyncMock(
                return_value={**self.camera, "enabled": False},
            )),
            patch.object(main.db, "set_camera_speed_settings", new=AsyncMock()) as save_settings,
        ):
            with self.assertRaises(HTTPException) as error:
                await main.update_camera_speed_settings(
                    "CAM12", self.payload,
                )

        self.assertEqual(error.exception.status_code, 409)
        save_settings.assert_not_awaited()

    async def test_flags_saved_crossing_gates_as_paused_calibration(self):
        crossed = {
            **self.saved,
            "gate_b": [[0.1, 0.75], [0.9, 0.25]],
        }
        with (
            patch.object(main.db, "get_camera", new=AsyncMock(
                return_value=self.camera,
            )),
            patch.object(main.db, "get_camera_speed_settings", new=AsyncMock(
                return_value=crossed,
            )),
        ):
            result = await main.get_camera_speed_settings("CAM12")

        self.assertTrue(result["enabled"])
        self.assertIn("cross or touch", result["calibration_error"])

    async def test_measurement_list_returns_evidence_urls_not_disk_paths(self):
        measurement = {
            "id": 44,
            "camera_id": "CAM12",
            "speeding": True,
            "snapshot_path": "snapshots/speeding/test.jpg",
            "video_path": "evidence/clips/speeding/test.mp4",
        }
        with (
            patch.object(main.db, "get_camera", new=AsyncMock(
                return_value=self.camera,
            )),
            patch.object(main.db, "list_vehicle_speed_measurements", new=AsyncMock(
                return_value=[measurement],
            )),
        ):
            result = await main.get_camera_speed_measurements("CAM12")

        self.assertEqual(
            result[0]["snapshot_url"],
            "/api/cameras/CAM12/speed-measurements/44/evidence",
        )
        self.assertEqual(
            result[0]["video_url"],
            "/api/cameras/CAM12/speed-measurements/44/evidence?kind=video",
        )
        self.assertNotIn("snapshot_path", result[0])
        self.assertNotIn("video_path", result[0])

    async def test_speed_evidence_endpoint_only_serves_the_matched_camera_record(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "speed.jpg"
            snapshot.write_bytes(b"jpeg")
            measurement = {
                "id": 44,
                "camera_id": "CAM12",
                "snapshot_path": str(snapshot),
                "video_path": None,
            }
            with patch.object(
                main.db, "get_vehicle_speed_measurement",
                new=AsyncMock(return_value=measurement),
            ) as get_measurement:
                response = await main.get_vehicle_speed_evidence(
                    "CAM12", 44,
                )

        get_measurement.assert_awaited_once_with("CAM12", 44)
        self.assertEqual(response.path, str(snapshot))
        self.assertEqual(response.media_type, "image/jpeg")

    async def test_old_mp4_speed_clip_is_converted_and_saved_as_webm(self):
        with tempfile.TemporaryDirectory() as directory:
            old_clip = Path(directory) / "speed.mp4"
            webm_clip = Path(directory) / "speed.webm"
            old_clip.write_bytes(b"old mp4")
            webm_clip.write_bytes(b"webm")
            measurement = {
                "id": 44,
                "camera_id": "CAM12",
                "snapshot_path": None,
                "video_path": str(old_clip),
            }
            with (
                patch.object(
                    main.db, "get_vehicle_speed_measurement",
                    new=AsyncMock(return_value=measurement),
                ),
                patch.object(
                    main, "convert_speed_clip_to_webm",
                    return_value=str(webm_clip),
                ),
                patch.object(
                    main.db, "update_vehicle_speed_evidence",
                    new=AsyncMock(return_value=True),
                ) as update_evidence,
            ):
                response = await main.get_vehicle_speed_evidence(
                    "CAM12", 44, kind="video",
                )
            self.assertFalse(old_clip.exists())

        update_evidence.assert_awaited_once_with(
            "CAM12", 44, video_path=str(webm_clip),
        )
        self.assertEqual(response.path, str(webm_clip))
        self.assertEqual(response.media_type, "video/webm")
        self.assertIn("inline", response.headers["content-disposition"])

    def test_rejects_bad_or_degenerate_gate_coordinates(self):
        values = {
            "distance_m": 8,
            "speed_limit_kmh": 15,
            "gate_a": [[0.1, 0.25], [0.9, 0.25]],
            "gate_b": [[0.1, 0.75], [0.9, 0.75]],
        }
        for field, gate in (
            ("gate_a", [[0.1, 0.25], [0.1, 0.25]]),
            ("gate_b", [[-0.1, 0.75], [0.9, 0.75]]),
            ("gate_b", [[0.9, 0.25], [0.1, 0.25]]),
            ("gate_b", [[0.1, 0.75], [0.9, 0.25]]),
        ):
            with self.subTest(gate=gate), self.assertRaises(ValidationError):
                main.CameraSpeedSettingsIn(**{**values, field: gate})


if __name__ == "__main__":
    unittest.main()
