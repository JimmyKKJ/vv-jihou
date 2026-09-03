#!/usr/bin/env python3
"""時報システムの制御CLI(オン/オフ・頻度設定・一時停止・稼働時間帯設定)。

使い方:
  jihou_ctl.py on
  jihou_ctl.py off
  jihou_ctl.py interval --minutes 5|10|15|30
  jihou_ctl.py interval --hours 1|2|3|6|12
  jihou_ctl.py pause --minutes 5|10|15|30
  jihou_ctl.py pause --hours 1〜12|24|48
  jihou_ctl.py pause --allday
  jihou_ctl.py window --start HH:MM --end HH:MM (5分刻み)
  jihou_ctl.py status

  -h, --help  この使い方を表示
"""

import argparse
import sys
from datetime import datetime, timedelta

sys.path.insert(0, __file__.rsplit("/", 1)[0])

from jihou_common import (
    ALLOWED_INTERVAL_HOURS,
    ALLOWED_INTERVAL_MINUTES,
    ALLOWED_PAUSE_HOURS,
    PlaybackError,
    audio_path,
    hhmm_to_minutes,
    load_state,
    log,
    parse_hhmm,
    play_sequence,
    save_state,
    state_lock,
)


def cmd_on(args):
    state = load_state()
    state["enabled"] = True
    save_state(state)
    log("on: 時報を有効化")
    play_sequence([audio_path("system", "on.wav")])


def cmd_off(args):
    state = load_state()
    state["enabled"] = False
    save_state(state)
    log("off: 時報を無効化")
    play_sequence([audio_path("system", "off.wav")])


def cmd_interval(args):
    state = load_state()
    if args.minutes is not None and args.hours is not None:
        print("エラー: --minutes と --hours は同時に指定できません", file=sys.stderr)
        sys.exit(1)
    if args.minutes is None and args.hours is None:
        print("エラー: --minutes か --hours のどちらかを指定してください", file=sys.stderr)
        sys.exit(1)

    if args.minutes is not None:
        if args.minutes not in ALLOWED_INTERVAL_MINUTES:
            print(
                f"エラー: --minutes は {ALLOWED_INTERVAL_MINUTES} のいずれかで指定してください",
                file=sys.stderr,
            )
            sys.exit(1)
        state["interval_minutes"] = args.minutes
        save_state(state)
        log(f"interval: {args.minutes}分ごとに設定")
        play_sequence([
            audio_path("system", f"min_{args.minutes:02d}.wav"),
            audio_path("system", "phrase_goto_ni_settei_shimashita.wav"),
        ])
    else:
        if args.hours not in ALLOWED_INTERVAL_HOURS:
            print(
                f"エラー: --hours は {ALLOWED_INTERVAL_HOURS} のいずれかで指定してください",
                file=sys.stderr,
            )
            sys.exit(1)
        state["interval_minutes"] = args.hours * 60
        save_state(state)
        log(f"interval: {args.hours}時間ごとに設定")
        play_sequence([
            audio_path("system", f"hour_{args.hours:02d}.wav"),
            audio_path("system", "phrase_goto_ni_settei_shimashita.wav"),
        ])


def cmd_pause(args):
    state = load_state()
    now = datetime.now()

    specified = [v for v in (args.minutes, args.hours, args.allday) if v]
    if len(specified) != 1:
        print(
            "エラー: --minutes / --hours / --allday のいずれか1つだけを指定してください",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.allday:
        until = now.replace(hour=23, minute=59, second=59, microsecond=0)
        state["paused_until"] = until.isoformat()
        save_state(state)
        log("pause: 終日止める")
        play_sequence([
            audio_path("system", "allday.wav"),
            audio_path("system", "stop.wav"),
        ])
        return

    if args.minutes is not None:
        if args.minutes not in ALLOWED_INTERVAL_MINUTES:
            print(
                f"エラー: --minutes は {ALLOWED_INTERVAL_MINUTES} のいずれかで指定してください",
                file=sys.stderr,
            )
            sys.exit(1)
        until = now + timedelta(minutes=args.minutes)
        state["paused_until"] = until.isoformat()
        save_state(state)
        log(f"pause: {args.minutes}分止める")
        play_sequence([
            audio_path("system", f"min_{args.minutes:02d}.wav"),
            audio_path("system", "stop.wav"),
        ])
        return

    if args.hours is not None:
        if args.hours not in ALLOWED_PAUSE_HOURS:
            print(
                f"エラー: --hours は {ALLOWED_PAUSE_HOURS} のいずれかで指定してください",
                file=sys.stderr,
            )
            sys.exit(1)
        until = now + timedelta(hours=args.hours)
        state["paused_until"] = until.isoformat()
        save_state(state)
        log(f"pause: {args.hours}時間止める")
        play_sequence([
            audio_path("system", f"hour_{args.hours:02d}.wav"),
            audio_path("system", "stop.wav"),
        ])
        return


def cmd_window(args):
    state = load_state()
    try:
        start_h, start_m = parse_hhmm(args.start)
        end_h, end_m = parse_hhmm(args.end)
    except ValueError as e:
        print(f"エラー: {e}", file=sys.stderr)
        sys.exit(1)

    if hhmm_to_minutes(start_h, start_m) >= hhmm_to_minutes(end_h, end_m):
        print("エラー: --end は --start より後の時刻にしてください", file=sys.stderr)
        sys.exit(1)

    state["active_start"] = f"{start_h:02d}:{start_m:02d}"
    state["active_end"] = f"{end_h:02d}:{end_m:02d}"
    save_state(state)
    log(f"window: {state['active_start']}〜{state['active_end']}に設定")

    sequence = [audio_path("hour", f"{start_h:02d}.wav")]
    if start_m != 0:
        sequence.append(audio_path("system", f"min_{start_m:02d}.wav"))
    sequence.append(audio_path("system", "phrase_kara.wav"))
    sequence.append(audio_path("hour", f"{end_h:02d}.wav"))
    if end_m != 0:
        sequence.append(audio_path("system", f"min_{end_m:02d}.wav"))
    sequence.append(audio_path("system", "phrase_made_ni_settei_shimashita.wav"))
    play_sequence(sequence)


def cmd_status(args):
    state = load_state()
    print(f"enabled: {state['enabled']}")
    print(f"interval_minutes: {state['interval_minutes']}")
    print(f"active_start: {state['active_start']}")
    print(f"active_end: {state['active_end']}")
    print(f"paused_until: {state['paused_until']}")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="jihou_ctl.py",
        description="時報システムの制御CLI(オン/オフ・頻度設定・一時停止・稼働時間帯設定)",
    )
    sub = parser.add_subparsers(dest="command")

    p_on = sub.add_parser("on", help="時報を有効化する")
    p_on.set_defaults(func=cmd_on)

    p_off = sub.add_parser("off", help="時報を無効化する")
    p_off.set_defaults(func=cmd_off)

    p_interval = sub.add_parser(
        "interval", help="時報の頻度を設定する(--minutesか--hoursのどちらか一方)"
    )
    p_interval.add_argument("--minutes", type=int, help=f"分単位、{ALLOWED_INTERVAL_MINUTES}のいずれか")
    p_interval.add_argument("--hours", type=int, help=f"時間単位、{ALLOWED_INTERVAL_HOURS}のいずれか")
    p_interval.set_defaults(func=cmd_interval)

    p_pause = sub.add_parser("pause", help="時報を一時的に止める")
    p_pause.add_argument("--minutes", type=int, help=f"分単位、{ALLOWED_INTERVAL_MINUTES}のいずれか")
    p_pause.add_argument("--hours", type=int, help=f"時間単位、{ALLOWED_PAUSE_HOURS}のいずれか")
    p_pause.add_argument("--allday", action="store_true", help="今日いっぱい止める")
    p_pause.set_defaults(func=cmd_pause)

    p_window = sub.add_parser("window", help="時報を鳴らす時間帯を設定する(5分刻み)")
    p_window.add_argument("--start", required=True, help="開始時刻 HH:MM(5分刻み)")
    p_window.add_argument("--end", required=True, help="終了時刻 HH:MM(5分刻み)")
    p_window.set_defaults(func=cmd_window)

    p_status = sub.add_parser("status", help="現在の設定を表示する")
    p_status.set_defaults(func=cmd_status)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    if not getattr(args, "command", None):
        parser.print_help()
        sys.exit(1)
    try:
        # state.jsonの読み込み〜保存を、cron(jihou_chime.py)側の同時実行から
        # ロックで守る(片方の変更が消える・読み込み中の内容が壊れるのを防ぐ)。
        with state_lock():
            args.func(args)
    except FileNotFoundError as e:
        print(f"エラー: {e}", file=sys.stderr)
        sys.exit(1)
    except PlaybackError as e:
        # 設定自体は既にstate.jsonへ保存済みのことが多いため、再生失敗は
        # 致命エラーにせず警告に留める(スピーカー未接続時でも設定変更は成立させる)。
        log(f"警告: {e}")
        print(f"警告: 設定は保存しましたが、音声の再生に失敗しました({e})", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
