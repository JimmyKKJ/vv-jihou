#!/usr/bin/env python3
"""時報システムの制御CLI(オン/オフ・頻度設定・一時停止・一時オン・稼働時間帯設定)。

使い方:
  jihou_ctl.py on
  jihou_ctl.py off
  jihou_ctl.py interval --minutes 5|10|15|30
  jihou_ctl.py interval --hours 1|2|3|6|12
  jihou_ctl.py pause --minutes 5|10|15|30
  jihou_ctl.py pause --hours 1〜12|24|48
  jihou_ctl.py pause --until HH:MM   (5分刻み、HH:MMの時報から再開。過ぎた時刻なら翌日)
  jihou_ctl.py pause --allday
  jihou_ctl.py boost --minutes 5|10|15|30
  jihou_ctl.py boost --hours 1〜12|24|48
  jihou_ctl.py boost --until HH:MM   (5分刻み、HH:MMの時報まで鳴らす。過ぎた時刻なら翌日)
  jihou_ctl.py window --start HH:MM --end HH:MM (5分刻み、終了が開始より前なら0時をまたぐ)
  jihou_ctl.py status

  -h, --help  この使い方を表示
  --version   バージョンを表示
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
    __version__,
    audio_path,
    crosses_midnight,
    hhmm_to_minutes,
    in_window,
    load_state,
    log,
    optional_audio,
    parse_hhmm,
    play_sequence,
    resolve_until,
    save_state,
    state_lock,
    window_length,
    window_offset,
)


def fail(message):
    print(f"エラー: {message}", file=sys.stderr)
    sys.exit(1)


def time_parts(hh, mm):
    """「n時m分」の音声パーツ(0分は時だけ)。"""
    parts = [audio_path("hour", f"{hh:02d}.wav")]
    if mm != 0:
        parts.append(audio_path("system", f"min_{mm:02d}.wav"))
    return parts


def resolve_span(args, now, command):
    """pause/boostの --minutes/--hours/--until から、(期限の日時, 読み上げの前半, ログ用の説明)を返す。"""
    specified = [v for v in (args.minutes, args.hours, args.until) if v is not None]
    if len(specified) != 1:
        options = "--minutes / --hours / --until" + (" / --allday" if command == "pause" else "")
        fail(f"{options} のいずれか1つだけを指定してください")

    if args.minutes is not None:
        if args.minutes not in ALLOWED_INTERVAL_MINUTES:
            fail(f"--minutes は {ALLOWED_INTERVAL_MINUTES} のいずれかで指定してください")
        return (
            now + timedelta(minutes=args.minutes),
            [audio_path("system", f"min_{args.minutes:02d}.wav")],
            f"{args.minutes}分",
        )

    if args.hours is not None:
        if args.hours not in ALLOWED_PAUSE_HOURS:
            fail(f"--hours は {ALLOWED_PAUSE_HOURS} のいずれかで指定してください")
        return (
            now + timedelta(hours=args.hours),
            [audio_path("system", f"hour_{args.hours:02d}.wav")],
            f"{args.hours}時間",
        )

    try:
        hh, mm = parse_hhmm(args.until)
    except ValueError as e:
        fail(str(e))
    until = resolve_until(now, hh, mm)
    # 「まで止めます」「まで鳴らします」は1本ずつ録った音声で読むため、「まで」は呼び出し側で付ける
    return (
        until,
        time_parts(hh, mm),
        f"{until.strftime('%Y-%m-%d %H:%M')}まで",
    )


def jihou_wo():
    """一時停止・一時オンの読み上げの先頭に付ける「時報を」。"""
    return audio_path("system", "phrase_jihou_wo.wav")


def cmd_on(args):
    state = load_state()
    state["enabled"] = True
    # 一時停止を取り消す明示的なCLIがないため、onは有効化と同時に
    # paused_untilもクリアする。これにより長時間の一時停止後でも手動で再開できる。
    state["paused_until"] = None
    save_state(state)
    log("on: 時報を有効化")
    play_sequence([audio_path("system", "on.wav")])


def cmd_off(args):
    state = load_state()
    state["enabled"] = False
    # 明示的なオフは、設定中の一時オンも取り消す
    state["boost_until"] = None
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


def allday_until(now, state):
    """--alldayの期限。0時をまたぐ時間帯の中にいれば、その時間帯の終了の1分後
    (終了時刻の時報・おやすみも鳴らさない)。それ以外は今日の23:59:59。"""
    start_h, start_m = map(int, state["active_start"].split(":"))
    end_h, end_m = map(int, state["active_end"].split(":"))
    start_min = hhmm_to_minutes(start_h, start_m)
    end_min = hhmm_to_minutes(end_h, end_m)
    now_min = hhmm_to_minutes(now.hour, now.minute)
    if crosses_midnight(start_min, end_min) and in_window(now_min, start_min, end_min):
        remaining = window_length(start_min, end_min) - window_offset(now_min, start_min)
        return now.replace(second=0, microsecond=0) + timedelta(minutes=remaining + 1)
    return now.replace(hour=23, minute=59, second=59, microsecond=0)


def cmd_pause(args):
    state = load_state()
    now = datetime.now()

    if args.allday:
        if any(v is not None for v in (args.minutes, args.hours, args.until)):
            fail("--minutes / --hours / --until / --allday のいずれか1つだけを指定してください")
        state["paused_until"] = allday_until(now, state).isoformat()
        state["boost_until"] = None
        save_state(state)
        log(f"pause: 終日止める({state['paused_until']}まで)")
        play_sequence([
            jihou_wo(),
            audio_path("system", "allday.wav"),
            audio_path("system", "stop.wav"),
        ])
        return

    until, sequence, label = resolve_span(args, now, "pause")
    state["paused_until"] = until.isoformat()
    # 後から出した指示を優先し、設定中の一時オンは取り消す
    state["boost_until"] = None
    save_state(state)
    log(f"pause: {label}止める")
    if args.until is not None:
        # 「まで」と「止めます」をつなげて録った音声のほうが自然に聞こえる
        ending = optional_audio("system", "phrase_made_tomemasu.wav")
    else:
        ending = audio_path("system", "stop.wav")
    play_sequence([jihou_wo()] + sequence + [ending])


def cmd_boost(args):
    state = load_state()
    now = datetime.now()

    until, sequence, label = resolve_span(args, now, "boost")
    state["boost_until"] = until.isoformat()
    # 後から出した指示を優先し、設定中の一時停止は取り消す
    state["paused_until"] = None
    save_state(state)
    log(f"boost: {label}鳴らす")
    if args.until is not None:
        # 「まで」と「鳴らします」をつなげて録った音声のほうが自然に聞こえる
        ending = optional_audio("system", "phrase_made_narashimasu.wav")
    else:
        ending = optional_audio("system", "phrase_narashimasu.wav")
    play_sequence([jihou_wo()] + sequence + [ending])


def cmd_window(args):
    state = load_state()
    try:
        start_h, start_m = parse_hhmm(args.start)
        end_h, end_m = parse_hhmm(args.end)
    except ValueError as e:
        fail(str(e))

    if hhmm_to_minutes(start_h, start_m) == hhmm_to_minutes(end_h, end_m):
        fail("--start と --end に同じ時刻は指定できません")

    state["active_start"] = f"{start_h:02d}:{start_m:02d}"
    state["active_end"] = f"{end_h:02d}:{end_m:02d}"
    save_state(state)
    overnight = "(0時をまたぐ)" if crosses_midnight(
        hhmm_to_minutes(start_h, start_m), hhmm_to_minutes(end_h, end_m)
    ) else ""
    log(f"window: {state['active_start']}〜{state['active_end']}{overnight}に設定")

    sequence = time_parts(start_h, start_m)
    sequence.append(audio_path("system", "phrase_kara.wav"))
    sequence += time_parts(end_h, end_m)
    sequence.append(audio_path("system", "phrase_made_ni_settei_shimashita.wav"))
    play_sequence(sequence)


def cmd_status(args):
    state = load_state()
    start_h, start_m = map(int, state["active_start"].split(":"))
    end_h, end_m = map(int, state["active_end"].split(":"))
    overnight = crosses_midnight(hhmm_to_minutes(start_h, start_m), hhmm_to_minutes(end_h, end_m))
    print(f"version: {__version__}")
    print(f"enabled: {state['enabled']}")
    print(f"interval_minutes: {state['interval_minutes']}")
    print(f"active_start: {state['active_start']}")
    print(f"active_end: {state['active_end']}" + ("(翌日、0時をまたぐ)" if overnight else ""))
    print(f"paused_until: {state['paused_until']}")
    print(f"boost_until: {state['boost_until']}")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="jihou_ctl.py",
        description="時報システムの制御CLI(オン/オフ・頻度設定・一時停止・一時オン・稼働時間帯設定)",
    )
    parser.add_argument("--version", action="version", version=f"vv-jihou {__version__}")
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
    p_pause.add_argument(
        "--until", help="HH:MM(5分刻み)の時報まで止める(その時報から再開。過ぎた時刻なら翌日)"
    )
    p_pause.add_argument(
        "--allday", action="store_true",
        help="今日いっぱい止める(0時をまたぐ時間帯の中なら、その時間帯の終わりまで)",
    )
    p_pause.set_defaults(func=cmd_pause)

    p_boost = sub.add_parser(
        "boost", help="一時的に時報を鳴らす(オフ中・時間帯外・一時停止中でも、期限まで設定の頻度で鳴らす)"
    )
    p_boost.add_argument("--minutes", type=int, help=f"分単位、{ALLOWED_INTERVAL_MINUTES}のいずれか")
    p_boost.add_argument("--hours", type=int, help=f"時間単位、{ALLOWED_PAUSE_HOURS}のいずれか")
    p_boost.add_argument(
        "--until", help="HH:MM(5分刻み)の時報まで鳴らす(その時報も鳴らす。過ぎた時刻なら翌日)"
    )
    p_boost.set_defaults(func=cmd_boost)

    p_window = sub.add_parser(
        "window", help="時報を鳴らす時間帯を設定する(5分刻み、終了が開始より前なら0時をまたぐ)"
    )
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
