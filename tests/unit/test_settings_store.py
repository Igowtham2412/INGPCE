import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import config
from app.settings_store import (
    get_settings,
    load_overrides,
    save_settings,
    validate_values,
)


class SettingsStoreTests(unittest.TestCase):
    def test_saved_settings_are_loaded_after_restart(self):
        initial = vars(config).copy()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            save_settings(
                {"FRAME_SKIP": 7, "DETECT_FPS": 2.5},
                initial,
                path,
            )
            pending = get_settings(initial, path)
            after_restart = vars(config).copy()
            load_overrides(after_restart, path)

        pending_values = {
            setting["key"]: setting["value"]
            for group in pending
            for setting in group["settings"]
        }
        self.assertEqual(pending_values["FRAME_SKIP"], 7)
        self.assertEqual(pending_values["DETECT_FPS"], 2.5)
        self.assertEqual(after_restart["FRAME_SKIP"], 7)
        self.assertEqual(after_restart["DETECT_FPS"], 2.5)

    def test_unknown_keys_and_invalid_cross_field_values_are_rejected(self):
        current = vars(config)
        with self.assertRaisesRegex(ValueError, "Unsupported setting key"):
            validate_values({"UNKNOWN_SETTING": "not-allowed"}, current)
        with self.assertRaisesRegex(ValueError, "Track low threshold"):
            validate_values({"TRACK_LOW_THRESHOLD": 0.9}, current)
        with self.assertRaisesRegex(ValueError, "must be an integer"):
            validate_values({"FRAME_SKIP": True}, current)

    def test_secrets_persist_but_settings_responses_never_return_them(self):
        current = vars(config).copy()
        face_key = base64.urlsafe_b64encode(b"x" * 32).decode("ascii")
        secret_values = {
            "PG_PASSWORD": "db-password-not-for-response",
            "POWER_AUTOMATE_WEBHOOK_URL": (
                "https://hooks.example/secret-token-not-for-response"
            ),
            "AUTH_SESSION_SECRET": "session-signing-secret-" * 2,
            "FACE_ENCRYPTION_KEY": face_key,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            save_settings(secret_values, current, path)
            groups = get_settings(current, path)
            after_restart = current.copy()
            load_overrides(after_restart, path)

        returned = {
            setting["key"]: setting
            for group in groups
            for setting in group["settings"]
        }
        for key, secret in secret_values.items():
            self.assertTrue(returned[key]["secret"])
            self.assertTrue(returned[key]["configured"])
            self.assertEqual(returned[key]["value"], "")
            self.assertNotIn(secret, str(groups))
            self.assertEqual(after_restart[key], secret)

    def test_secret_can_be_cleared_and_face_key_must_be_valid(self):
        current = vars(config).copy()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            save_settings({"PG_PASSWORD": ""}, current, path)
            groups = get_settings(current, path)

        returned = {
            setting["key"]: setting
            for group in groups
            for setting in group["settings"]
        }
        self.assertFalse(returned["PG_PASSWORD"]["configured"])
        self.assertEqual(returned["PG_PASSWORD"]["value"], "")
        with self.assertRaisesRegex(ValueError, "decode to exactly 32 bytes"):
            validate_values({"FACE_ENCRYPTION_KEY": "YQ=="}, current)

    def test_seed_camera_urls_use_host_overrides_and_global_fallbacks(self):
        with (
            patch.object(config, "CAMERA_USERNAME", "default-user"),
            patch.object(config, "CAMERA_PASSWORD", "default-pass"),
            patch.object(config, "NVR_192_169_0_65_USERNAME", "nvr-user"),
            patch.object(config, "NVR_192_169_0_65_PASSWORD", "nvr@pass"),
        ):
            host_url = config._apply_camera_credentials(
                "rtsp://old:old@192.169.0.65:554/stream",
            )
            fallback_credentials = config._camera_credentials("192.168.2.10")

        self.assertIn("nvr-user:nvr%40pass@", host_url)
        self.assertEqual(fallback_credentials, ("default-user", "default-pass"))


if __name__ == "__main__":
    unittest.main()
