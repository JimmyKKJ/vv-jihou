#!/usr/bin/env python3
"""cronから定期的(*/5 * * * * 推奨)に起動し、鳴らすべきタイミングであれば音声を再生する。

使い方:
  jihou_chime.py            現在時刻で判定して鳴らす(cron用)
  jihou_chime.py --at HH:MM 指定時刻で判定して鳴らす(動作確認用、日付は今日扱い)
  jihou_chime.py --at YYYY-MM-DDTHH:MM 指定日時で判定して鳴らす(祝日パターン確認用)
  -h, --help  この使い方を表示
"""

import argparse
import sys
from datetime import datetime

sys.path.insert(0, __file__.rsplit("/", 1)[0])

from jihou_common import (
    audio_path,
    hhmm_to_minutes,
    load_state,
    log,
    play_error,
    play_sequence,
    save_state,
    state_lock,
)

WEEKDAY_FILES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def resolve_now(at_value):
    if at_value is None:
        return datetime.now()
    for fmt in ("%Y-%m-%dT%H:%M", "%H:%M"):
        try:
            parsed = datetime.strptime(at_value, fmt)
        except ValueError:
            continue
        if fmt == "%H:%M":
            today = datetime.now()
            parsed = parsed.replace(year=today.year, month=today.month, day=today.day)
        return parsed
    raise ValueError(f"--at の形式が不正です(指定値: {at_value})")


def holiday_marker(date):
    """祝日なら 'holiday'、国民の休日なら 'kokumin'、それ以外(平日土日含む)は None。"""
    try:
        import jpholiday
    except ImportError:
        log("警告: jpholidayが見つからないため祝日判定をスキップします")
        return None
    name = jpholiday.is_holiday_name(date)
    if name is None:
        return None
    if name == "国民の休日":
        return "kokumin"
    return "holiday"


def first_and_last_tick(active_start_min, active_end_min, interval):
    first_tick = None
    t = active_start_min - (active_start_min % interval)
    while t < active_start_min:
        t += interval
    if t <= active_end_min:
        first_tick = t

    last_tick = None
    t = active_end_min - (active_end_min % interval)
    if t >= active_start_min:
        last_tick = t

    return first_tick, last_tick


def build_sequence(now, state):
    hour, minute = now.hour, now.minute
    sequence = []

    if hour == 0 and minute == 0:
        sequence.append(audio_path("month", f"{now.month:02d}.wav"))
        sequence.append(audio_path("day", f"{now.day:02d}.wav"))
        sequence.append(audio_path("weekday", f"{WEEKDAY_FILES[now.weekday()]}.wav"))
        marker = holiday_marker(now.date())
        if marker == "holiday":
            sequence.append(audio_path("special", "holiday_bare.wav"))
        elif marker == "kokumin":
            sequence.append(audio_path("special", "kokuminnokyujitsu_bare.wav"))
        sequence.append(audio_path("hour", "00.wav"))
        sequence.append(audio_path("minute", "00_choudo.wav"))
        return sequence

    if hour == 12 and minute == 0:
        sequence.append(audio_path("special", "noon.wav"))
        return sequence

    sequence.append(audio_path("hour", f"{hour:02d}.wav"))
    if minute == 0:
        sequence.append(audio_path("minute", "00_choudo.wav"))
    else:
        sequence.append(audio_path("minute", f"{minute:02d}.wav"))
    return sequence


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--at", help="判定に使う時刻(HH:MM、またはYYYY-MM-DDTHH:MM)")
    args = parser.parse_args()

    try:
        now = resolve_now(args.at)
    except ValueError as e:
        print(f"エラー: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        # jihou_ctl.py側の手動操作と同時に走ってもstate.jsonが競合しないよう、
        # 読み込み〜(必要なら)paused_untilクリアの保存までをロックで囲む。
        with state_lock():
            state = load_state()

            if not state["enabled"]:
                return

            if state["paused_until"]:
                paused_until = datetime.fromisoformat(state["paused_until"])
                if now < paused_until:
                    return
                state["paused_until"] = None
                save_state(state)

        start_h, start_m = map(int, state["active_start"].split(":"))
        end_h, end_m = map(int, state["active_end"].split(":"))
        active_start_min = hhmm_to_minutes(start_h, start_m)
        active_end_min = hhmm_to_minutes(end_h, end_m)
        now_min = hhmm_to_minutes(now.hour, now.minute)

        if now_min < active_start_min or now_min > active_end_min:
            return

        interval = state["interval_minutes"]
        if now_min % interval != 0:
            return

        sequence = build_sequence(now, state)

        first_tick, last_tick = first_and_last_tick(active_start_min, active_end_min, interval)
        if first_tick is not None and now_min == first_tick:
            sequence.insert(0, audio_path("system", "ohayou.wav"))
        if last_tick is not None and now_min == last_tick:
            sequence.append(audio_path("system", "oyasumi.wav"))

        log(f"chime: {now.strftime('%Y-%m-%d %H:%M')} を再生")
        play_sequence(sequence)

    except Exception as e:  # noqa: BLE001 — 時報が完全に沈黙するよりエラー音を優先する
        log(f"エラー: {e}")
        play_error()


if __name__ == "__main__":
    main()
