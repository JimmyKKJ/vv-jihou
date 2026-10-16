"""状態値の検証・時報の判定・制御CLIの回帰テスト。"""

import sys
import unittest
from argparse import Namespace
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


BIN_DIR = Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN_DIR))

import jihou_chime  # noqa: E402
import jihou_common  # noqa: E402
import jihou_ctl  # noqa: E402


def make_state(**overrides):
    state = {
        "enabled": True,
        "interval_minutes": 15,
        "active_start": "06:30",
        "active_end": "23:00",
        "paused_until": None,
        "boost_until": None,
    }
    state.update(overrides)
    return state


def at(value):
    return datetime.fromisoformat(value)


class StateValidationTests(unittest.TestCase):
    def test_valid_hhmm_accepts_cli_compatible_values(self):
        self.assertTrue(jihou_common._valid_hhmm("06:30"))
        self.assertTrue(jihou_common._valid_hhmm("23:55"))

    def test_valid_hhmm_rejects_noncanonical_or_non_five_minute_values(self):
        for value in ("6:30", "06:31", "24:00", "06:60"):
            with self.subTest(value=value):
                self.assertFalse(jihou_common._valid_hhmm(value))

    def test_sanitize_keeps_overnight_window(self):
        state, changed = jihou_common.sanitize_state(make_state(active_start="06:00", active_end="01:00"))
        self.assertFalse(changed)
        self.assertEqual((state["active_start"], state["active_end"]), ("06:00", "01:00"))

    def test_sanitize_repairs_zero_length_window(self):
        with patch.object(jihou_common, "log"):
            state, changed = jihou_common.sanitize_state(make_state(active_start="07:00", active_end="07:00"))
        self.assertTrue(changed)
        self.assertEqual(state["active_start"], jihou_common.DEFAULT_STATE["active_start"])

    def test_sanitize_clears_invalid_boost_until(self):
        with patch.object(jihou_common, "log"):
            state, changed = jihou_common.sanitize_state(make_state(boost_until="not-a-date"))
        self.assertTrue(changed)
        self.assertIsNone(state["boost_until"])


class WindowTests(unittest.TestCase):
    def test_in_window_overnight(self):
        start, end = 6 * 60, 1 * 60  # 06:00〜翌01:00
        self.assertTrue(jihou_common.in_window(0 * 60 + 30, start, end))
        self.assertTrue(jihou_common.in_window(1 * 60, start, end))
        self.assertTrue(jihou_common.in_window(23 * 60, start, end))
        self.assertFalse(jihou_common.in_window(3 * 60, start, end))
        self.assertFalse(jihou_common.in_window(5 * 60 + 55, start, end))

    def test_in_window_same_day_unchanged(self):
        start, end = 6 * 60 + 30, 23 * 60
        self.assertTrue(jihou_common.in_window(6 * 60 + 30, start, end))
        self.assertFalse(jihou_common.in_window(23 * 60 + 5, start, end))
        self.assertFalse(jihou_common.in_window(6 * 60 + 25, start, end))

    def test_resolve_until_rolls_over_midnight(self):
        self.assertEqual(jihou_common.resolve_until(at("2026-10-16T23:00:10"), 1, 30), at("2026-10-17T01:30"))
        self.assertEqual(jihou_common.resolve_until(at("2026-10-16T10:00:00"), 12, 0), at("2026-10-16T12:00"))
        # ちょうど同じ時刻は翌日
        self.assertEqual(jihou_common.resolve_until(at("2026-10-16T12:00:00"), 12, 0), at("2026-10-17T12:00"))


class DecideTests(unittest.TestCase):
    def test_overnight_window_rings_after_midnight(self):
        state = make_state(active_start="06:00", active_end="01:00")
        self.assertTrue(jihou_chime.decide(at("2026-10-17T00:30"), state)["ring"])
        self.assertFalse(jihou_chime.decide(at("2026-10-17T03:00"), state)["ring"])

    def test_overnight_greetings(self):
        state = make_state(active_start="06:00", active_end="01:00")
        morning = jihou_chime.decide(at("2026-10-17T06:00"), state)
        night = jihou_chime.decide(at("2026-10-17T01:00"), state)
        self.assertTrue(morning["morning"])
        self.assertFalse(morning["night"])
        self.assertTrue(night["night"])
        self.assertFalse(jihou_chime.decide(at("2026-10-16T23:00"), state)["night"])

    def test_same_day_greetings_unchanged(self):
        state = make_state()
        self.assertTrue(jihou_chime.decide(at("2026-10-16T06:30"), state)["morning"])
        self.assertTrue(jihou_chime.decide(at("2026-10-16T23:00"), state)["night"])

    def test_pause_until_resumes_at_that_chime(self):
        state = make_state(active_start="06:00", active_end="01:00", paused_until="2026-10-17T00:30:00")
        self.assertFalse(jihou_chime.decide(at("2026-10-17T00:15"), state)["ring"])
        result = jihou_chime.decide(at("2026-10-17T00:30"), state)
        self.assertTrue(result["ring"])
        self.assertEqual(result["clear"], ["paused_until"])

    def test_boost_rings_when_off_and_outside_window(self):
        state = make_state(enabled=False, boost_until="2026-10-17T01:30:00")
        result = jihou_chime.decide(at("2026-10-16T23:45"), state)
        self.assertTrue(result["ring"])
        self.assertFalse(result["night"])
        self.assertTrue(jihou_chime.decide(at("2026-10-17T00:00"), state)["ring"])

    def test_boost_until_includes_that_chime_then_clears(self):
        state = make_state(enabled=False, boost_until="2026-10-17T01:30:00")
        self.assertTrue(jihou_chime.decide(at("2026-10-17T01:30:05"), state)["ring"])
        after = jihou_chime.decide(at("2026-10-17T01:45"), state)
        self.assertFalse(after["ring"])
        self.assertEqual(after["clear"], ["boost_until"])

    def test_boost_overrides_pause(self):
        state = make_state(paused_until="2026-10-17T12:00:00", boost_until="2026-10-16T22:00:00")
        self.assertTrue(jihou_chime.decide(at("2026-10-16T21:00"), state)["ring"])

    def test_boost_respects_interval(self):
        state = make_state(enabled=False, boost_until="2026-10-17T01:30:00")
        self.assertFalse(jihou_chime.decide(at("2026-10-16T23:50"), state)["ring"])


class ControlTests(unittest.TestCase):
    def run_cmd(self, func, args, state, now):
        with (
            patch.object(jihou_ctl, "load_state", return_value=state),
            patch.object(jihou_ctl, "save_state") as save_state,
            patch.object(jihou_ctl, "log"),
            patch.object(jihou_ctl, "audio_path", side_effect=lambda *p: "/".join(p)),
            patch.object(jihou_ctl, "optional_audio", side_effect=lambda *p: "/".join(p)),
            patch.object(jihou_ctl, "play_sequence") as play_sequence,
            patch.object(jihou_ctl, "datetime") as fake_datetime,
        ):
            fake_datetime.now.return_value = now
            func(args)
        return save_state, play_sequence

    def test_pause_until_sets_next_day_and_cancels_boost(self):
        state = make_state(boost_until="2026-10-17T02:00:00")
        args = Namespace(minutes=None, hours=None, until="01:30", allday=False)
        _, play = self.run_cmd(jihou_ctl.cmd_pause, args, state, at("2026-10-16T23:00:00"))
        self.assertEqual(state["paused_until"], "2026-10-17T01:30:00")
        self.assertIsNone(state["boost_until"])
        self.assertEqual(
            play.call_args[0][0],
            ["hour/01.wav", "system/min_30.wav", "system/phrase_made.wav", "system/stop.wav"],
        )

    def test_boost_until_sets_deadline_and_cancels_pause(self):
        state = make_state(enabled=False, paused_until="2026-10-17T06:00:00")
        args = Namespace(minutes=None, hours=None, until="01:00")
        _, play = self.run_cmd(jihou_ctl.cmd_boost, args, state, at("2026-10-16T22:55:00"))
        self.assertEqual(state["boost_until"], "2026-10-17T01:00:00")
        self.assertIsNone(state["paused_until"])
        self.assertEqual(
            play.call_args[0][0],
            ["hour/01.wav", "system/phrase_made.wav", "system/phrase_narashimasu.wav"],
        )

    def test_boost_hours(self):
        state = make_state(enabled=False)
        args = Namespace(minutes=None, hours=2, until=None)
        self.run_cmd(jihou_ctl.cmd_boost, args, state, at("2026-10-16T23:00:00"))
        self.assertEqual(state["boost_until"], "2026-10-17T01:00:00")

    def test_boost_rejects_multiple_options(self):
        args = Namespace(minutes=30, hours=None, until="01:00")
        with self.assertRaises(SystemExit), patch("sys.stderr"):
            self.run_cmd(jihou_ctl.cmd_boost, args, make_state(), at("2026-10-16T23:00:00"))

    def test_allday_in_overnight_window_pauses_past_window_end(self):
        state = make_state(active_start="06:00", active_end="01:00")
        args = Namespace(minutes=None, hours=None, until=None, allday=True)
        self.run_cmd(jihou_ctl.cmd_pause, args, state, at("2026-10-16T23:10:30"))
        self.assertEqual(state["paused_until"], "2026-10-17T01:01:00")
        self.assertFalse(jihou_chime.decide(at("2026-10-17T01:00"), state)["ring"])

    def test_allday_same_day_window_unchanged(self):
        state = make_state()
        args = Namespace(minutes=None, hours=None, until=None, allday=True)
        self.run_cmd(jihou_ctl.cmd_pause, args, state, at("2026-10-16T10:00:00"))
        self.assertEqual(state["paused_until"], "2026-10-16T23:59:59")

    def test_window_accepts_overnight(self):
        state = make_state()
        args = Namespace(start="06:00", end="01:00")
        _, play = self.run_cmd(jihou_ctl.cmd_window, args, state, at("2026-10-16T10:00:00"))
        self.assertEqual((state["active_start"], state["active_end"]), ("06:00", "01:00"))
        self.assertEqual(
            play.call_args[0][0],
            ["hour/06.wav", "system/phrase_kara.wav", "hour/01.wav", "system/phrase_made_ni_settei_shimashita.wav"],
        )

    def test_window_rejects_same_time(self):
        args = Namespace(start="07:00", end="07:00")
        with self.assertRaises(SystemExit), patch("sys.stderr"):
            self.run_cmd(jihou_ctl.cmd_window, args, make_state(), at("2026-10-16T10:00:00"))

    def test_off_cancels_boost(self):
        state = make_state(boost_until="2026-10-17T01:00:00")
        self.run_cmd(jihou_ctl.cmd_off, None, state, at("2026-10-16T22:00:00"))
        self.assertFalse(state["enabled"])
        self.assertIsNone(state["boost_until"])

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
