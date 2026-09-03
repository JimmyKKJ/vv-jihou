"""状態値の検証と手動再開の回帰テスト。"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BIN_DIR = Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN_DIR))

import jihou_common  # noqa: E402
import jihou_ctl  # noqa: E402


class StateValidationTests(unittest.TestCase):
    def test_valid_hhmm_accepts_cli_compatible_values(self):
        self.assertTrue(jihou_common._valid_hhmm("06:30"))
        self.assertTrue(jihou_common._valid_hhmm("23:55"))

    def test_valid_hhmm_rejects_noncanonical_or_non_five_minute_values(self):
        for value in ("6:30", "06:31", "24:00", "06:60"):
            with self.subTest(value=value):
                self.assertFalse(jihou_common._valid_hhmm(value))


class ControlTests(unittest.TestCase):
    def test_on_clears_an_existing_pause(self):
        state = {
            "enabled": False,
            "interval_minutes": 15,
            "active_start": "06:30",
            "active_end": "23:00",
            "paused_until": "2026-09-05T12:00:00",
        }

        with (
            patch.object(jihou_ctl, "load_state", return_value=state),
            patch.object(jihou_ctl, "save_state") as save_state,
            patch.object(jihou_ctl, "log"),
            patch.object(jihou_ctl, "audio_path", return_value="on.wav"),
            patch.object(jihou_ctl, "play_sequence"),
        ):
            jihou_ctl.cmd_on(None)

        self.assertTrue(state["enabled"])
        self.assertIsNone(state["paused_until"])
        save_state.assert_called_once_with(state)


if __name__ == "__main__":
    unittest.main()
