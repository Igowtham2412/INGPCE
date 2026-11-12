import tempfile
import unittest
from pathlib import Path

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
            validate_values({"AUTH_SESSION_SECRET": "not-allowed"}, current)
        with self.assertRaisesRegex(ValueError, "Track low threshold"):
            validate_values({"TRACK_LOW_THRESHOLD": 0.9}, current)
        with self.assertRaisesRegex(ValueError, "must be an integer"):
            validate_values({"FRAME_SKIP": True}, current)


if __name__ == "__main__":
    unittest.main()
