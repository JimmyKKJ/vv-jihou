#!/usr/bin/env python3
"""時報システムの制御CLI(オン/オフ・頻度設定・一時停止・一時オン・稼働時間帯設定)。

使い方:
  jihou_ctl.py on
  jihou_ctl.py off
  jihou_ctl.py interval --minutes 5|10|15|20|30
  jihou_ctl.py interval --hours 1|2|3|6|12
  jihou_ctl.py pause --minutes 5|10|15|20|30
  jihou_ctl.py pause --hours 1〜12|24|48
  jihou_ctl.py pause --until HH:MM   (5分刻み、HH:MMの時報から再開。過ぎた時刻なら翌日)
  jihou_ctl.py pause --allday
  jihou_ctl.py boost --minutes 5|10|15|20|30
  jihou_ctl.py boost --hours 1〜12|24|48
  jihou_ctl.py boost --until HH:MM   (5分刻み、HH:MMの時報まで鳴らす。過ぎた時刻なら翌日)
  jihou_ctl.py window --start HH:MM --end HH:MM (5分刻み、終了が開始より前なら0時をまたぐ)
  jihou_ctl.py late-hours on|off   (0時〜3時台を24時〜27時と読むか)
  jihou_ctl.py status
  jihou_ctl.py schedule add --start <時刻> --end <時刻> --action stop|ring
  jihou_ctl.py schedule update --id <番号> [--start <時刻>] [--end <時刻>] [--action stop|ring]
  jihou_ctl.py schedule remove --id <番号>
  jihou_ctl.py schedule list
  jihou_ctl.py schedule clear
      (時刻は HH:MM〈5分刻み〉か YYYY-MM-DDTHH:MM。始まりの時刻に読み上げなしで実行し、
       終わりの時刻で元に戻る。詳細は schedule --help / schedule add --help)

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
    SCHEDULE_ACTIONS,
    __version__,
    audio_path,
    crosses_midnight,
    describe_schedule_entry,
    find_overlap,
    hhmm_to_minutes,
    in_window,
    load_schedule,
    load_state,
    log,
    optional_audio,
    parse_hhmm,
    play_sequence,
    resolve_schedule_span,
    resolve_until,
    retime_schedule_span,
    save_schedule,
    save_state,
    spoken_hour,
    state_lock,
    window_length,
    window_offset,
)


def fail(message):
    print(f"エラー: {message}", file=sys.stderr)
    sys.exit(1)


def time_parts(hh, mm, late_hours=False):
    """「n時m分」の音声パーツ(0分は時だけ)。late_hoursなら0時〜3時台を24時〜27時で読む。"""
    parts = [audio_path("hour", f"{spoken_hour(hh, late_hours):02d}.wav")]
    if mm != 0:
        parts.append(audio_path("system", f"min_{mm:02d}.wav"))
    return parts


def resolve_span(args, now, command, state):
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
        time_parts(hh, mm, state["late_hours"]),
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

    until, sequence, label = resolve_span(args, now, "pause", state)
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

    until, sequence, label = resolve_span(args, now, "boost", state)
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

    sequence = time_parts(start_h, start_m, state["late_hours"])
    sequence.append(audio_path("system", "phrase_kara.wav"))
    sequence += time_parts(end_h, end_m, state["late_hours"])
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
    print(f"late_hours: {state['late_hours']}")
    entries = load_schedule()
    if not entries:
        print("schedule: なし")
    for entry in entries:
        print(f"schedule: {describe_schedule_entry(entry)}")


def schedule_voice(ending):
    """「スケジュールを」+「追加しました/更新しました/削除しました」。音声が無ければ省いて読む。"""
    return [
        optional_audio("system", "phrase_schedule_wo.wav"),
        optional_audio("system", ending),
    ]


def cmd_schedule_add(args):
    entries = load_schedule()
    now = datetime.now()
    try:
        start, end = resolve_schedule_span(now, args.start, args.end)
    except ValueError as e:
        fail(str(e))
    overlap = find_overlap(entries, start, end)
    if overlap is not None:
        fail(f"他の予約と時間が重なっています: {describe_schedule_entry(overlap)}")

    entry = {
        "id": max((e["id"] for e in entries), default=0) + 1,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "action": args.action,
        "created_at": now.replace(microsecond=0).isoformat(),
    }
    entries.append(entry)
    save_schedule(entries)
    log(f"schedule add: {describe_schedule_entry(entry)}")
    print(f"予約しました: {describe_schedule_entry(entry)}")
    if start <= now:
        print("(始まりの時刻を過ぎているため、次の5分刻みの時刻から効きます)")
    play_sequence(schedule_voice("phrase_tsuika_shimashita.wav"))


def cmd_schedule_update(args):
    if args.start is None and args.end is None and args.action is None:
        fail("--start / --end / --action のいずれかを指定してください")
    entries = load_schedule()
    now = datetime.now()
    entry = next((e for e in entries if e["id"] == args.id), None)
    if entry is None:
        fail(f"番号 {args.id} の予約はありません(schedule list で確かめてください)")
    try:
        start, end = retime_schedule_span(now, entry, args.start, args.end)
    except ValueError as e:
        fail(str(e))
    overlap = find_overlap(entries, start, end, ignore_id=entry["id"])
    if overlap is not None:
        fail(f"他の予約と時間が重なっています: {describe_schedule_entry(overlap)}")

    before = describe_schedule_entry(entry)
    entry["start"] = start.isoformat()
    entry["end"] = end.isoformat()
    if args.action is not None:
        entry["action"] = args.action
    save_schedule(entries)
    log(f"schedule update: {before} → {describe_schedule_entry(entry)}")
    print(f"更新しました: {describe_schedule_entry(entry)}")
    play_sequence(schedule_voice("phrase_koushin_shimashita.wav"))


def cmd_schedule_remove(args):
    entries = load_schedule()
    entry = next((e for e in entries if e["id"] == args.id), None)
    if entry is None:
        fail(f"番号 {args.id} の予約はありません(schedule list で確かめてください)")
    entries.remove(entry)
    save_schedule(entries)
    log(f"schedule remove: {describe_schedule_entry(entry)}")
    print(f"削除しました: {describe_schedule_entry(entry)}")
    play_sequence(schedule_voice("phrase_sakujo_shimashita.wav"))


def cmd_schedule_clear(args):
    entries = load_schedule()
    if not entries:
        print("予約はありません")
        return
    save_schedule([])
    log(f"schedule clear: {len(entries)}件を削除")
    print(f"全ての予約({len(entries)}件)を削除しました")
    play_sequence(schedule_voice("phrase_sakujo_shimashita.wav"))


def cmd_schedule_list(args):
    entries = load_schedule()
    if not entries:
        print("予約はありません")
        return
    for entry in entries:
        print(describe_schedule_entry(entry))


def cmd_late_hours(args):
    state = load_state()
    state["late_hours"] = args.mode == "on"
    save_state(state)
    if state["late_hours"]:
        message = "0時〜3時台を24時〜27時と読みます(0時ちょうどは日付を読まず「24時ちょうど」)"
    else:
        message = "0時〜3時台を通常どおり0時〜3時と読みます"
    log(f"late-hours: {args.mode}")
    # この設定の切り替えを読み上げる音声は無いため、表示だけにする
    print(message)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="jihou_ctl.py",
        description="時報システムの制御CLI(オン/オフ・頻度設定・一時停止・一時オン・稼働時間帯設定・24時〜27時の読み)",
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

    p_late = sub.add_parser(
        "late-hours", help="0時〜3時台を24時〜27時と読むかを設定する(時報・操作の読み上げとも)"
    )
    p_late.add_argument("mode", choices=["on", "off"], help="on: 24時〜27時で読む / off: 0時〜3時で読む")
    p_late.set_defaults(func=cmd_late_hours)

    p_status = sub.add_parser("status", help="現在の設定と予約を表示する")
    p_status.set_defaults(func=cmd_status)

    time_help = "HH:MM(5分刻み、24:00〜27:55も可)か YYYY-MM-DDTHH:MM"
    p_schedule = sub.add_parser(
        "schedule",
        help="時間を指定して、時報の停止・鳴動を予約する(始まりの時刻に自動で実行、終わりの時刻で元に戻る)",
        description=(
            "時間(始まりと終わり)を指定して、時報の停止・鳴動を予約する。"
            "始まりの時刻(5分刻み)に読み上げなしで実行する。"
            "停止は pause --until <終わり>、鳴動は boost --until <終わり> と同じ動きになり、"
            "終わりの時刻の時報から元の設定に戻る(鳴動は、終わりの時刻の時報まで鳴らす)。"
            "実行した予約は一覧から消える(実行後に止める・戻すには pause / boost / on / off を使う)。"
        ),
    )
    p_schedule.set_defaults(schedule_parser=p_schedule)
    schedule_sub = p_schedule.add_subparsers(dest="schedule_command")

    p_s_add = schedule_sub.add_parser(
        "add",
        help="予約を追加する",
        description=(
            "予約を追加する。--start と --end は、両方を HH:MM か、両方を YYYY-MM-DDTHH:MM で指定する。"
            "HH:MM の場合、終わりは今より後で最も近いその時刻、始まりは終わりより前で最も近いその時刻になる"
            "(終わりが始まりより前なら0時をまたぐ。始まりが過ぎていて終わりが先なら、次の5分刻みから効く)。"
            "他の予約と時間が重なる場合はエラーになる。"
        ),
    )
    p_s_add.add_argument("--start", required=True, help=f"始まり。{time_help}")
    p_s_add.add_argument("--end", required=True, help=f"終わり。{time_help}")
    p_s_add.add_argument("--action", required=True, choices=SCHEDULE_ACTIONS, help="stop: 停止 / ring: 鳴動")
    p_s_add.set_defaults(func=cmd_schedule_add)

    p_s_update = schedule_sub.add_parser(
        "update",
        help="予約を更新する(指定した項目だけ変える)",
        description=(
            "予約を更新する。--start / --end / --action のうち、指定した項目だけを変える。"
            "HH:MM は、今の予約の始まりの日付のまま時刻だけを差し替える(終わりが始まり以前になれば翌日)。"
            "日付も変えるときは YYYY-MM-DDTHH:MM で指定する。"
        ),
    )
    p_s_update.add_argument("--id", required=True, type=int, help="予約の番号(schedule list で表示)")
    p_s_update.add_argument("--start", help=f"始まり。{time_help}")
    p_s_update.add_argument("--end", help=f"終わり。{time_help}")
    p_s_update.add_argument("--action", choices=SCHEDULE_ACTIONS, help="stop: 停止 / ring: 鳴動")
    p_s_update.set_defaults(func=cmd_schedule_update)

    p_s_remove = schedule_sub.add_parser("remove", help="予約を1件削除する")
    p_s_remove.add_argument("--id", required=True, type=int, help="予約の番号(schedule list で表示)")
    p_s_remove.set_defaults(func=cmd_schedule_remove)

    p_s_list = schedule_sub.add_parser("list", help="予約の一覧を表示する(読み上げなし)")
    p_s_list.set_defaults(func=cmd_schedule_list)

    p_s_clear = schedule_sub.add_parser("clear", help="全ての予約を削除する")
    p_s_clear.set_defaults(func=cmd_schedule_clear)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    if not getattr(args, "command", None):
        parser.print_help()
        sys.exit(1)
    if args.command == "schedule" and not getattr(args, "schedule_command", None):
        args.schedule_parser.print_help()
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
