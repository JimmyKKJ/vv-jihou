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
        "late_hours": False,
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


class LateHoursTests(unittest.TestCase):
    def sequence(self, now, **overrides):
        with patch.object(jihou_chime, "audio_path", side_effect=lambda *p: "/".join(p)):
            return jihou_chime.build_sequence(at(now), make_state(**overrides))

    def test_spoken_hour(self):
        self.assertEqual(jihou_common.spoken_hour(1, True), 25)
        self.assertEqual(jihou_common.spoken_hour(3, True), 27)
        self.assertEqual(jihou_common.spoken_hour(4, True), 4)
        self.assertEqual(jihou_common.spoken_hour(1, False), 1)

    def test_chime_reads_25_when_on(self):
        self.assertEqual(self.sequence("2026-10-17T01:30", late_hours=True), ["hour/25.wav", "minute/30.wav"])
        self.assertEqual(self.sequence("2026-10-17T01:30"), ["hour/01.wav", "minute/30.wav"])
        self.assertEqual(self.sequence("2026-10-17T04:00", late_hours=True), ["hour/04.wav", "minute/00_choudo.wav"])

    def test_midnight_reads_24_without_date_when_on(self):
        self.assertEqual(self.sequence("2026-10-17T00:00", late_hours=True), ["hour/24.wav", "minute/00_choudo.wav"])
        self.assertEqual(self.sequence("2026-10-17T00:00")[0], "month/10.wav")

    def test_parse_hhmm_accepts_24_to_27(self):
        self.assertEqual(jihou_common.parse_hhmm("25:30"), (1, 30))
        self.assertEqual(jihou_common.parse_hhmm("24:00"), (0, 0))
        self.assertEqual(jihou_common.parse_hhmm("07:00"), (7, 0))
        for value in ("28:00", "06:31", "6:3x"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                jihou_common.parse_hhmm(value)

    def test_sanitize_repairs_non_bool_late_hours(self):
        with patch.object(jihou_common, "log"):
            state, changed = jihou_common.sanitize_state(make_state(late_hours="yes"))
        self.assertTrue(changed)
        self.assertFalse(state["late_hours"])


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
            ["system/phrase_jihou_wo.wav", "hour/01.wav", "system/min_30.wav", "system/phrase_made_tomemasu.wav"],
        )

    def test_boost_until_sets_deadline_and_cancels_pause(self):
        state = make_state(enabled=False, paused_until="2026-10-17T06:00:00")
        args = Namespace(minutes=None, hours=None, until="01:00")
        _, play = self.run_cmd(jihou_ctl.cmd_boost, args, state, at("2026-10-16T22:55:00"))
        self.assertEqual(state["boost_until"], "2026-10-17T01:00:00")
        self.assertIsNone(state["paused_until"])
        self.assertEqual(
            play.call_args[0][0],
            ["system/phrase_jihou_wo.wav", "hour/01.wav", "system/phrase_made_narashimasu.wav"],
        )

    def test_boost_hours(self):
        state = make_state(enabled=False)
        args = Namespace(minutes=None, hours=2, until=None)
        _, play = self.run_cmd(jihou_ctl.cmd_boost, args, state, at("2026-10-16T23:00:00"))
        self.assertEqual(state["boost_until"], "2026-10-17T01:00:00")
        self.assertEqual(
            play.call_args[0][0],
            ["system/phrase_jihou_wo.wav", "system/hour_02.wav", "system/phrase_narashimasu.wav"],
        )

    def test_pause_minutes_reads_jihou_wo(self):
        args = Namespace(minutes=30, hours=None, until=None, allday=False)
        _, play = self.run_cmd(jihou_ctl.cmd_pause, args, make_state(), at("2026-10-16T10:00:00"))
        self.assertEqual(
            play.call_args[0][0],
            ["system/phrase_jihou_wo.wav", "system/min_30.wav", "system/stop.wav"],
        )

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

    def test_window_reads_25_when_late_hours(self):
        state = make_state(late_hours=True)
        args = Namespace(start="06:00", end="25:00")
        _, play = self.run_cmd(jihou_ctl.cmd_window, args, state, at("2026-10-16T10:00:00"))
        self.assertEqual(state["active_end"], "01:00")
        self.assertEqual(
            play.call_args[0][0],
            ["hour/06.wav", "system/phrase_kara.wav", "hour/25.wav", "system/phrase_made_ni_settei_shimashita.wav"],
        )

    def test_pause_until_reads_25_when_late_hours(self):
        state = make_state(late_hours=True)
        args = Namespace(minutes=None, hours=None, until="01:30", allday=False)
        _, play = self.run_cmd(jihou_ctl.cmd_pause, args, state, at("2026-10-16T23:00:00"))
        self.assertEqual(play.call_args[0][0][1], "hour/25.wav")

    def test_late_hours_on_off(self):
        state = make_state()
        with patch("builtins.print"):
            self.run_cmd(jihou_ctl.cmd_late_hours, Namespace(mode="on"), state, at("2026-10-16T10:00:00"))
            self.assertTrue(state["late_hours"])
            self.run_cmd(jihou_ctl.cmd_late_hours, Namespace(mode="off"), state, at("2026-10-16T10:00:00"))
        self.assertFalse(state["late_hours"])

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


def make_entry(entry_id, start, end, action="stop"):
    return {"id": entry_id, "start": start, "end": end, "action": action, "created_at": "2026-11-14T09:00:00"}


class ScheduleSpanTests(unittest.TestCase):
    def test_hhmm_future_today(self):
        self.assertEqual(
            jihou_common.resolve_schedule_span(at("2026-11-14T09:00:00"), "10:30", "12:00"),
            (at("2026-11-14T10:30"), at("2026-11-14T12:00")),
        )

    def test_hhmm_in_progress_starts_today(self):
        self.assertEqual(
            jihou_common.resolve_schedule_span(at("2026-11-14T11:00:00"), "10:30", "12:00"),
            (at("2026-11-14T10:30"), at("2026-11-14T12:00")),
        )

    def test_hhmm_past_goes_to_tomorrow(self):
        self.assertEqual(
            jihou_common.resolve_schedule_span(at("2026-11-14T13:00:00"), "10:30", "12:00"),
            (at("2026-11-15T10:30"), at("2026-11-15T12:00")),
        )

    def test_hhmm_crosses_midnight(self):
        self.assertEqual(
            jihou_common.resolve_schedule_span(at("2026-11-14T20:00:00"), "23:00", "25:00"),
            (at("2026-11-14T23:00"), at("2026-11-15T01:00")),
        )

    def test_datetime_span(self):
        self.assertEqual(
            jihou_common.resolve_schedule_span(at("2026-11-14T09:00:00"), "2026-11-20T10:30", "2026-11-20T12:00"),
            (at("2026-11-20T10:30"), at("2026-11-20T12:00")),
        )

    def test_rejects_invalid_spans(self):
        now = at("2026-11-14T09:00:00")
        for start, end in (
            ("10:30", "10:30"),  # 同じ時刻
            ("10:30", "2026-11-14T12:00"),  # 形式の混在
            ("2026-11-14T12:00", "2026-11-14T10:30"),  # 終わりが先
            ("2026-11-13T10:30", "2026-11-13T12:00"),  # 終わりが過ぎている
            ("10:31", "12:00"),  # 5分刻みでない
            ("2026-11-14T10:31", "2026-11-14T12:00"),
        ):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                jihou_common.resolve_schedule_span(now, start, end)

    def test_retime_keeps_date_and_changes_only_given_side(self):
        entry = make_entry(1, "2026-11-20T10:30:00", "2026-11-20T12:00:00")
        now = at("2026-11-14T09:00:00")
        self.assertEqual(
            jihou_common.retime_schedule_span(now, entry, None, "12:30"),
            (at("2026-11-20T10:30"), at("2026-11-20T12:30")),
        )
        self.assertEqual(
            jihou_common.retime_schedule_span(now, entry, "23:00", "01:00"),
            (at("2026-11-20T23:00"), at("2026-11-21T01:00")),
        )
        self.assertEqual(
            jihou_common.retime_schedule_span(now, entry, None, None),
            (at("2026-11-20T10:30"), at("2026-11-20T12:00")),
        )
        self.assertEqual(
            jihou_common.retime_schedule_span(now, entry, "2026-11-21T10:30", None),
            (at("2026-11-21T10:30"), at("2026-11-21T12:00")),
        )

    def test_find_overlap_treats_touching_spans_as_separate(self):
        entries = [make_entry(1, "2026-11-14T10:30:00", "2026-11-14T12:00:00")]
        self.assertIsNone(jihou_common.find_overlap(entries, at("2026-11-14T12:00"), at("2026-11-14T13:00")))
        self.assertIsNotNone(jihou_common.find_overlap(entries, at("2026-11-14T11:55"), at("2026-11-14T13:00")))
        self.assertIsNone(
            jihou_common.find_overlap(entries, at("2026-11-14T11:00"), at("2026-11-14T13:00"), ignore_id=1)
        )


class ScheduleApplyTests(unittest.TestCase):
    def test_stop_silences_from_start_and_resumes_at_end(self):
        state = make_state()
        entries = [make_entry(1, "2026-11-14T10:30:00", "2026-11-14T12:00:00")]

        # 始まりの前は、適用されず鳴る
        remaining, applied, expired = jihou_common.apply_due_schedule(state, entries, at("2026-11-14T10:15:02"))
        self.assertEqual((len(remaining), applied, expired), (1, [], []))
        self.assertTrue(jihou_chime.decide(at("2026-11-14T10:15:02"), state)["ring"])

        # 始まりの時刻の時報から止まる
        remaining, applied, expired = jihou_common.apply_due_schedule(state, entries, at("2026-11-14T10:30:02"))
        self.assertEqual((remaining, len(applied), expired), ([], 1, []))
        self.assertEqual(state["paused_until"], "2026-11-14T12:00:00")
        self.assertFalse(jihou_chime.decide(at("2026-11-14T10:30:02"), state)["ring"])
        self.assertFalse(jihou_chime.decide(at("2026-11-14T11:45:02"), state)["ring"])

        # 終わりの時刻の時報から鳴る
        self.assertTrue(jihou_chime.decide(at("2026-11-14T12:00:02"), state)["ring"])

    def test_ring_rings_when_off_until_end_chime(self):
        state = make_state(enabled=False)
        entries = [make_entry(1, "2026-11-14T23:30:00", "2026-11-15T01:00:00", "ring")]
        jihou_common.apply_due_schedule(state, entries, at("2026-11-14T23:30:02"))
        self.assertEqual(state["boost_until"], "2026-11-15T01:00:00")
        self.assertTrue(jihou_chime.decide(at("2026-11-14T23:30:02"), state)["ring"])
        self.assertTrue(jihou_chime.decide(at("2026-11-15T01:00:02"), state)["ring"])
        self.assertFalse(jihou_chime.decide(at("2026-11-15T01:15:02"), state)["ring"])

    def test_stop_cancels_boost_and_ring_cancels_pause(self):
        state = make_state(boost_until="2026-11-14T23:00:00")
        jihou_common.apply_due_schedule(
            state, [make_entry(1, "2026-11-14T10:30:00", "2026-11-14T12:00:00")], at("2026-11-14T10:30:00")
        )
        self.assertIsNone(state["boost_until"])
        jihou_common.apply_due_schedule(
            state, [make_entry(2, "2026-11-14T11:00:00", "2026-11-14T11:30:00", "ring")], at("2026-11-14T11:00:00")
        )
        self.assertIsNone(state["paused_until"])

    def test_missed_entry_is_dropped_without_applying(self):
        state = make_state()
        entries = [
            make_entry(1, "2026-11-14T10:30:00", "2026-11-14T12:00:00"),
            make_entry(2, "2026-11-14T15:00:00", "2026-11-14T16:00:00"),
        ]
        remaining, applied, expired = jihou_common.apply_due_schedule(state, entries, at("2026-11-14T12:00:02"))
        self.assertEqual([e["id"] for e in remaining], [2])
        self.assertEqual(applied, [])
        self.assertEqual([e["id"] for e in expired], [1])
        self.assertIsNone(state["paused_until"])

    def test_in_progress_entry_is_applied_late(self):
        state = make_state()
        entries = [make_entry(1, "2026-11-14T10:30:00", "2026-11-14T12:00:00")]
        _, applied, _ = jihou_common.apply_due_schedule(state, entries, at("2026-11-14T11:20:02"))
        self.assertEqual(len(applied), 1)
        self.assertEqual(state["paused_until"], "2026-11-14T12:00:00")


class ScheduleFileTests(unittest.TestCase):
    """schedule.json を一時ディレクトリに置いて、読み書きとCLIを確かめる。"""

    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.schedule_path = Path(self.tmp.name) / "schedule.json"
        self.broken_path = Path(self.tmp.name) / "schedule.broken.json"
        for name, value in (
            ("SCHEDULE_PATH", str(self.schedule_path)),
            ("SCHEDULE_BROKEN_PATH", str(self.broken_path)),
        ):
            patcher = patch.object(jihou_common, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_schedule(self, func, args, now):
        with (
            patch.object(jihou_ctl, "log"),
            patch.object(jihou_ctl, "optional_audio", side_effect=lambda *p: "/".join(p)),
            patch.object(jihou_ctl, "play_sequence") as play_sequence,
            patch.object(jihou_ctl, "datetime") as fake_datetime,
            patch("builtins.print"),
        ):
            fake_datetime.now.return_value = now
            fake_datetime.fromisoformat.side_effect = datetime.fromisoformat
            func(args)
        return play_sequence

    def test_missing_file_is_empty(self):
        self.assertEqual(jihou_common.load_schedule(), [])
        self.assertFalse(self.schedule_path.exists())

    def test_broken_file_is_moved_aside(self):
        self.schedule_path.write_text('[{"id": 1, "start": "x"}]', encoding="utf-8")
        with patch.object(jihou_common, "log"):
            self.assertEqual(jihou_common.load_schedule(repair=False), [])
            self.assertTrue(self.schedule_path.exists())
            self.assertEqual(jihou_common.load_schedule(), [])
        self.assertFalse(self.schedule_path.exists())
        self.assertTrue(self.broken_path.exists())

    def test_add_update_remove_read_their_voices(self):
        now = at("2026-11-14T09:00:00")
        play = self.run_schedule(
            jihou_ctl.cmd_schedule_add, Namespace(start="10:30", end="12:00", action="stop"), now
        )
        self.assertEqual(play.call_args[0][0], ["system/phrase_schedule_wo.wav", "system/phrase_tsuika_shimashita.wav"])
        entries = jihou_common.load_schedule()
        self.assertEqual(
            [(e["id"], e["start"], e["end"], e["action"]) for e in entries],
            [(1, "2026-11-14T10:30:00", "2026-11-14T12:00:00", "stop")],
        )

        play = self.run_schedule(
            jihou_ctl.cmd_schedule_update, Namespace(id=1, start=None, end="12:30", action="ring"), now
        )
        self.assertEqual(play.call_args[0][0][1], "system/phrase_koushin_shimashita.wav")
        entry = jihou_common.load_schedule()[0]
        self.assertEqual((entry["end"], entry["action"]), ("2026-11-14T12:30:00", "ring"))

        play = self.run_schedule(jihou_ctl.cmd_schedule_remove, Namespace(id=1), now)
        self.assertEqual(play.call_args[0][0][1], "system/phrase_sakujo_shimashita.wav")
        self.assertEqual(jihou_common.load_schedule(), [])

    def test_add_rejects_overlap_and_numbers_sequentially(self):
        now = at("2026-11-14T09:00:00")
        self.run_schedule(jihou_ctl.cmd_schedule_add, Namespace(start="10:30", end="12:00", action="stop"), now)
        with self.assertRaises(SystemExit), patch("sys.stderr"):
            self.run_schedule(jihou_ctl.cmd_schedule_add, Namespace(start="11:00", end="13:00", action="ring"), now)
        self.run_schedule(jihou_ctl.cmd_schedule_add, Namespace(start="12:00", end="13:00", action="ring"), now)
        self.assertEqual([e["id"] for e in jihou_common.load_schedule()], [1, 2])

    def test_update_and_remove_reject_unknown_id(self):
        now = at("2026-11-14T09:00:00")
        for func, args in (
            (jihou_ctl.cmd_schedule_update, Namespace(id=9, start=None, end="12:30", action=None)),
            (jihou_ctl.cmd_schedule_remove, Namespace(id=9)),
        ):
            with self.subTest(func=func.__name__), self.assertRaises(SystemExit), patch("sys.stderr"):
                self.run_schedule(func, args, now)

    def test_clear_is_silent_when_empty(self):
        now = at("2026-11-14T09:00:00")
        play = self.run_schedule(jihou_ctl.cmd_schedule_clear, Namespace(), now)
        play.assert_not_called()
        self.run_schedule(jihou_ctl.cmd_schedule_add, Namespace(start="10:30", end="12:00", action="stop"), now)
        play = self.run_schedule(jihou_ctl.cmd_schedule_clear, Namespace(), now)
        self.assertEqual(play.call_args[0][0][1], "system/phrase_sakujo_shimashita.wav")
        self.assertEqual(jihou_common.load_schedule(), [])

    def test_chime_applies_and_removes_due_entry(self):
        jihou_common.save_schedule([make_entry(1, "2026-11-14T10:30:00", "2026-11-14T12:00:00")])
        state = make_state()
        with (
            patch.object(jihou_chime, "save_state") as save_state,
            patch.object(jihou_chime, "log"),
        ):
            jihou_chime.run_schedule(at("2026-11-14T10:30:02"), state, False)
        save_state.assert_called_once_with(state)
        self.assertEqual(state["paused_until"], "2026-11-14T12:00:00")
        self.assertEqual(jihou_common.load_schedule(), [])

    def test_chime_simulation_does_not_write(self):
        jihou_common.save_schedule([make_entry(1, "2026-11-14T10:30:00", "2026-11-14T12:00:00")])
        state = make_state()
        with (
            patch.object(jihou_chime, "save_state") as save_state,
            patch.object(jihou_chime, "log"),
        ):
            jihou_chime.run_schedule(at("2026-11-14T10:30:02"), state, True)
        save_state.assert_not_called()
        self.assertEqual(state["paused_until"], "2026-11-14T12:00:00")
        self.assertEqual(len(jihou_common.load_schedule()), 1)


if __name__ == "__main__":
    unittest.main()
