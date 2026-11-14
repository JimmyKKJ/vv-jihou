"""vv-jihou の共通ユーティリティ(state.json の読み書き・音声再生・パス解決)。"""

import contextlib
import fcntl
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta

__version__ = "1.4"

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUDIO_DIR = os.path.join(REPO_ROOT, "audio")
STATE_PATH = os.path.join(REPO_ROOT, "state", "state.json")
# 停止・鳴動の予約(schedule add で書き、jihou_chime.py が始まりの時刻に適用して消す)
SCHEDULE_PATH = os.path.join(REPO_ROOT, "state", "schedule.json")
SCHEDULE_BROKEN_PATH = os.path.join(REPO_ROOT, "state", "schedule.broken.json")
SCHEDULE_ACTIONS = ("stop", "ring")
SCHEDULE_ACTION_LABELS = {"stop": "停止", "ring": "鳴動"}
LOG_PATH = os.path.join(REPO_ROOT, "state", "jihou.log")
LOCK_PATH = os.path.join(REPO_ROOT, "state", ".state.lock")

DEFAULT_STATE = {
    "enabled": False,
    "interval_minutes": 15,
    "active_start": "06:30",
    "active_end": "23:00",
    "paused_until": None,
    # 一時オン(boost)の期限。この日時の時報までは、オフ・稼働時間帯外・一時停止中でも鳴らす
    "boost_until": None,
    # 0時〜3時台を24時〜27時と読むか(時報・操作の読み上げとも)
    "late_hours": False,
}

# 24時〜27時の音声(hour/24.wav〜27.wav)がある、0時〜3時台
LATE_HOURS_LAST = 3

MINUTES_PER_DAY = 24 * 60

# 頻度設定・一時停止で選べる値(録音済みの音声パーツに対応するもののみ)
ALLOWED_INTERVAL_MINUTES = [5, 10, 15, 20, 30]
ALLOWED_INTERVAL_HOURS = [1, 2, 3, 6, 12]
ALLOWED_PAUSE_HOURS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 24, 48]


def _valid_hhmm(value):
    """state.json 内の時刻がCLIと同じHH:MM・5分刻みの制約を満たすか返す。"""
    if not isinstance(value, str) or re.fullmatch(r"\d{2}:\d{2}", value) is None:
        return False
    try:
        h, m = value.split(":")
        h, m = int(h), int(m)
    except ValueError:
        return False
    return 0 <= h <= 23 and 0 <= m <= 59 and m % 5 == 0


def sanitize_state(state):
    """壊れた/手編集で不正になったstateを既定値へ自動修復する。戻り値は(state, 修復したか)。"""
    changed = False

    allowed_intervals = {m for m in ALLOWED_INTERVAL_MINUTES} | {h * 60 for h in ALLOWED_INTERVAL_HOURS}
    if state.get("interval_minutes") not in allowed_intervals:
        log(f"警告: interval_minutesの値が不正なため既定値({DEFAULT_STATE['interval_minutes']})に修復します(元の値: {state.get('interval_minutes')!r})")
        state["interval_minutes"] = DEFAULT_STATE["interval_minutes"]
        changed = True

    if not _valid_hhmm(state.get("active_start")) or not _valid_hhmm(state.get("active_end")):
        log(
            "警告: active_start/active_endの値が不正なため既定値に修復します"
            f"(元の値: {state.get('active_start')!r} / {state.get('active_end')!r})"
        )
        state["active_start"] = DEFAULT_STATE["active_start"]
        state["active_end"] = DEFAULT_STATE["active_end"]
        changed = True
    else:
        sh, sm = (int(x) for x in state["active_start"].split(":"))
        eh, em = (int(x) for x in state["active_end"].split(":"))
        # active_start > active_end は0時をまたぐ時間帯(例: 06:00〜01:00)として有効。
        # 開始と終了が同じ時刻だけは長さが決まらないため不正とする。
        if hhmm_to_minutes(sh, sm) == hhmm_to_minutes(eh, em):
            log(
                "警告: active_startとactive_endが同じ時刻のため既定値に修復します"
                f"(元の値: {state['active_start']} / {state['active_end']})"
            )
            state["active_start"] = DEFAULT_STATE["active_start"]
            state["active_end"] = DEFAULT_STATE["active_end"]
            changed = True

    for key in ("paused_until", "boost_until"):
        value = state.get(key)
        if value is None:
            continue
        try:
            parsed = datetime.fromisoformat(value)
        except (TypeError, ValueError):
            log(f"警告: {key}の値が不正なためクリアします(元の値: {value!r})")
            state[key] = None
            changed = True
        else:
            if parsed.tzinfo is not None:
                # 内部では常にnaive(タイムゾーン無し、ローカル時刻)で扱う規約のため、
                # tz付きの値はdatetime.now()との比較でTypeErrorになり続けてしまう。
                log(f"警告: {key}にタイムゾーン情報が含まれていたためクリアします(元の値: {value!r})")
                state[key] = None
                changed = True

    for key in ("enabled", "late_hours"):
        if not isinstance(state.get(key), bool):
            log(f"警告: {key}の値が不正なためFalseに修復します(元の値: {state.get(key)!r})")
            state[key] = False
            changed = True

    return state, changed


@contextlib.contextmanager
def state_lock():
    """state.json読み書きの排他制御。cron(jihou_chime.py)と手動操作(jihou_ctl.py)が
    同時に動いても、片方の変更が消える(lost update)・読み込み中の内容が壊れる、
    といった競合を防ぐ。呼び出し側は load_state()〜save_state() までをこの中で行うこと。
    """
    os.makedirs(os.path.dirname(LOCK_PATH), exist_ok=True)
    with open(LOCK_PATH, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def load_state():
    if not os.path.exists(STATE_PATH):
        return dict(DEFAULT_STATE)
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            state = json.load(f)
        if not isinstance(state, dict):
            raise ValueError("state.jsonの内容がオブジェクトではありません")
    except (json.JSONDecodeError, ValueError, OSError) as e:
        log(f"警告: state.jsonの読み込みに失敗したため既定値で復旧します({e})")
        merged = dict(DEFAULT_STATE)
        save_state(merged)
        return merged

    merged = dict(DEFAULT_STATE)
    merged.update(state)
    merged, changed = sanitize_state(merged)
    if changed:
        save_state(merged)
    return merged


def save_state(state):
    """一時ファイルに書いてから置き換える(電源断等で書き込み途中のファイルが
    残ってstate.jsonが壊れることを防ぐ、os.replaceは同一ファイルシステム内で原子的)。"""
    _write_json_atomic(STATE_PATH, state)


def _write_json_atomic(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)


def _valid_schedule_entry(entry):
    """予約1件が {id: 正の整数, start/end: tz無しの日時(start < end), action: stop|ring} か返す。"""
    if not isinstance(entry, dict):
        return False
    if not isinstance(entry.get("id"), int) or isinstance(entry.get("id"), bool) or entry["id"] < 1:
        return False
    if entry.get("action") not in SCHEDULE_ACTIONS:
        return False
    try:
        start = datetime.fromisoformat(entry.get("start"))
        end = datetime.fromisoformat(entry.get("end"))
    except (TypeError, ValueError):
        return False
    if start.tzinfo is not None or end.tzinfo is not None:
        return False
    return start < end


def load_schedule(repair=True):
    """予約の一覧(始まりの早い順)を返す。ファイルが無ければ空。

    壊れている(JSONでない・一覧でない・不正な項目がある)ときは空として扱い、ログに書く。
    repair が真なら、壊れたファイルを schedule.broken.json へ退避する(中身を捨てない)。
    呼び出し側は state_lock() の中で呼ぶこと。
    """
    if not os.path.exists(SCHEDULE_PATH):
        return []
    try:
        with open(SCHEDULE_PATH, "r", encoding="utf-8") as f:
            entries = json.load(f)
        if not isinstance(entries, list) or not all(_valid_schedule_entry(e) for e in entries):
            raise ValueError("schedule.jsonの内容が予約の一覧ではありません")
        if len({e["id"] for e in entries}) != len(entries):
            raise ValueError("schedule.jsonに同じ番号の予約があります")
    except (json.JSONDecodeError, ValueError, OSError) as e:
        if repair:
            log(f"警告: schedule.jsonの読み込みに失敗したため、{SCHEDULE_BROKEN_PATH} へ退避して空として扱います({e})")
            try:
                os.replace(SCHEDULE_PATH, SCHEDULE_BROKEN_PATH)
            except OSError as move_error:
                log(f"警告: schedule.jsonの退避に失敗しました({move_error})")
        return []
    return sorted(entries, key=lambda e: e["start"])


def save_schedule(entries):
    _write_json_atomic(SCHEDULE_PATH, sorted(entries, key=lambda e: e["start"]))


def apply_pause(state, until):
    """一時停止を設定する(pause と同じ状態変更)。後から出した指示を優先し、一時オンは取り消す。"""
    state["paused_until"] = until.isoformat()
    state["boost_until"] = None


def apply_boost(state, until):
    """一時オンを設定する(boost と同じ状態変更)。後から出した指示を優先し、一時停止は取り消す。"""
    state["boost_until"] = until.isoformat()
    state["paused_until"] = None


def describe_schedule_entry(entry):
    start = datetime.fromisoformat(entry["start"])
    end = datetime.fromisoformat(entry["end"])
    return (
        f"#{entry['id']} {SCHEDULE_ACTION_LABELS[entry['action']]} "
        f"{start.strftime('%Y-%m-%d %H:%M')}〜{end.strftime('%Y-%m-%d %H:%M')}"
    )


def apply_due_schedule(state, entries, now):
    """始まりの時刻が来た予約を state に適用する(ファイルは書かない)。

    - 始まり <= now < 終わり: 停止なら一時停止、鳴動なら一時オンを「終わりまで」で設定する
    - 終わり <= now(止まっていて過ぎた予約): 適用しない
    どちらも一覧から外す。戻り値は (残る予約, 適用した予約, 適用せず外した予約)。
    """
    remaining, applied, expired = [], [], []
    for entry in sorted(entries, key=lambda e: e["start"]):
        start = datetime.fromisoformat(entry["start"])
        end = datetime.fromisoformat(entry["end"])
        if end <= now:
            expired.append(entry)
        elif start <= now:
            if entry["action"] == "stop":
                apply_pause(state, end)
            else:
                apply_boost(state, end)
            applied.append(entry)
        else:
            remaining.append(entry)
    return remaining, applied, expired


def find_overlap(entries, start, end, ignore_id=None):
    """[start, end) と時間が重なる予約を返す(無ければNone)。終わりと始まりが同じ時刻は重ならない。"""
    for entry in entries:
        if entry["id"] == ignore_id:
            continue
        other_start = datetime.fromisoformat(entry["start"])
        other_end = datetime.fromisoformat(entry["end"])
        if start < other_end and other_start < end:
            return entry
    return None


def parse_time_spec(value):
    """予約の時刻の指定を読む。'HH:MM' は ("hhmm", (時, 分))、'YYYY-MM-DDTHH:MM' は ("datetime", 日時)。

    どちらも5分刻み。HH:MM は 24:00〜27:55 も受け付ける(翌日の0:00〜3:55)。
    """
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", value or ""):
        try:
            parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M")
        except ValueError:
            raise ValueError(f"日時は YYYY-MM-DDTHH:MM 形式で指定してください(指定値: {value})") from None
        if parsed.minute % 5 != 0:
            raise ValueError(f"時刻は5分刻みで指定してください(指定値: {value})")
        return "datetime", parsed
    return "hhmm", parse_hhmm(value)


def resolve_schedule_span(now, start_value, end_value):
    """予約の始まり・終わりの指定を日時にする。

    両方 HH:MM: 終わりは、今より後で最も近いその時刻。始まりは、終わりより前で最も近いその時刻
      (終わりが始まりより前の指定は0時をまたぐ。始まりが過ぎていて終わりが先なら進行中の予約)
    両方 YYYY-MM-DDTHH:MM: そのまま使う
    """
    start_kind, start = parse_time_spec(start_value)
    end_kind, end = parse_time_spec(end_value)
    if start_kind != end_kind:
        raise ValueError("--start と --end は、両方を HH:MM か、両方を YYYY-MM-DDTHH:MM で指定してください")
    if start_kind == "hhmm":
        if start == end:
            raise ValueError("--start と --end に同じ時刻は指定できません")
        end_at = resolve_until(now, *end)
        start_at = end_at.replace(hour=start[0], minute=start[1])
        if start_at >= end_at:
            start_at -= timedelta(days=1)
        return start_at, end_at
    if start >= end:
        raise ValueError("--end は --start より後の日時を指定してください")
    if end <= now:
        raise ValueError("--end が過ぎています(これから終わる時間を指定してください)")
    return start, end


def retime_schedule_span(now, entry, start_value, end_value):
    """予約の更新用。指定された側だけを差し替えた (始まり, 終わり) を返す(Noneは変えない)。

    HH:MM は、今の予約の始まりの日付を基準に時刻だけを差し替える(終わりが始まり以前になれば翌日)。
    YYYY-MM-DDTHH:MM は、その日時をそのまま使う。
    """
    start = datetime.fromisoformat(entry["start"])
    end = datetime.fromisoformat(entry["end"])
    end_is_clock = end_value is None  # 終わりを時刻として持ち、始まりの日付から決め直すか
    if start_value is not None:
        kind, value = parse_time_spec(start_value)
        start = value if kind == "datetime" else start.replace(hour=value[0], minute=value[1])
    end_clock = (end.hour, end.minute)
    if end_value is not None:
        kind, value = parse_time_spec(end_value)
        if kind == "datetime":
            end = value
        else:
            end_clock = value
            end_is_clock = True
    if end_is_clock and (start_value is not None or end_value is not None):
        end = start.replace(hour=end_clock[0], minute=end_clock[1])
        if end <= start:
            end += timedelta(days=1)
    if start >= end:
        raise ValueError("終わりは始まりより後にしてください")
    if end <= now:
        raise ValueError("終わりの時刻が過ぎています(これから終わる時間を指定してください)")
    return start, end


def log(message):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {message}\n")


def audio_path(*parts):
    path = os.path.join(AUDIO_DIR, *parts)
    if not os.path.exists(path):
        raise FileNotFoundError(f"音声ファイルが見つかりません: {path}")
    return path


class PlaybackError(Exception):
    """aplayの実行に失敗した(バイナリが無い/デバイスエラー等)ことを表す。"""


def optional_audio(*parts):
    """v1.1で追加した音声パーツ用。ファイルが無ければ警告をログに書いてNoneを返す
    (音声を用意する前でも、設定の変更自体は成立させるため)。"""
    try:
        return audio_path(*parts)
    except FileNotFoundError as e:
        log(f"警告: {e}(この部分を省いて再生します)")
        return None


def play_sequence(paths):
    """複数のwavを順番に再生する(aplayは再生完了までブロックするので単に直列に呼ぶだけでよい)。

    aplay自体の実行失敗(デバイスエラー等)はPlaybackErrorに包んで送出する。呼び出し側は
    このエラーを捕捉して、生のトレースバックを出さずに扱うこと(設定変更自体は
    play_sequence呼び出し前に既に保存済みであることが多いため、致命エラー扱いにしない)。
    """
    for path in paths:
        if path is None:
            # optional_audio() が見つけられなかったパーツは飛ばす
            continue
        try:
            subprocess.run(["aplay", "-q", path], check=True)
        except OSError as e:
            # FileNotFoundError(aplayが無い)・PermissionError(実行権限が無い)・
            # その他exec失敗系のOSErrorをまとめて扱う。
            raise PlaybackError(f"aplayの実行に失敗しました: {e}") from e
        except subprocess.CalledProcessError as e:
            raise PlaybackError(f"再生に失敗しました({path}): {e}") from e


def play_error():
    try:
        subprocess.run(["aplay", "-q", audio_path("system", "error.wav")], check=False)
    except OSError:
        # ここでの失敗はベストエフォート(エラー音を鳴らそうとしただけ)のため、
        # さらに上位へ例外を伝播させない(OSErrorはFileNotFoundError等を含む)。
        pass


def parse_hhmm(value):
    """'HH:MM' 形式をパースし、5分刻みでなければエラーにする。

    24:00〜27:55 も受け付け、翌日の0:00〜3:55として返す(24時〜27時の読みと揃えるため)。
    """
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", value or "")
    if match is None:
        raise ValueError(f"時刻は HH:MM 形式で指定してください(指定値: {value})")
    hour, minute = int(match.group(1)), int(match.group(2))
    if not (0 <= hour <= 24 + LATE_HOURS_LAST and 0 <= minute <= 59):
        raise ValueError(f"時刻は 00:00〜{24 + LATE_HOURS_LAST}:55 の範囲で指定してください(指定値: {value})")
    if minute % 5 != 0:
        raise ValueError(f"時刻は5分刻みで指定してください(指定値: {value})")
    return hour % 24, minute


def hhmm_to_minutes(hh, mm):
    return hh * 60 + mm


def now_minutes_of_day(now=None):
    now = now or datetime.now()
    return now.hour * 60 + now.minute


def spoken_hour(hour, late_hours):
    """読み上げに使う「時」。late_hoursなら0時〜3時台を24時〜27時にする。"""
    if late_hours and hour <= LATE_HOURS_LAST:
        return hour + 24
    return hour


def window_length(start_min, end_min):
    """稼働時間帯の長さ(分)。終了が開始より前なら0時をまたぐ(翌日の終了時刻まで)とみなす。"""
    return (end_min - start_min) % MINUTES_PER_DAY


def window_offset(now_min, start_min):
    """稼働開始時刻から数えた経過分(0時をまたいでも正の値になる)。"""
    return (now_min - start_min) % MINUTES_PER_DAY


def in_window(now_min, start_min, end_min):
    """now_min(0時からの分)が稼働時間帯(開始・終了の両端を含む)に入るか。"""
    return window_offset(now_min, start_min) <= window_length(start_min, end_min)


def crosses_midnight(start_min, end_min):
    return end_min < start_min


def resolve_until(now, hh, mm):
    """「HH:MMまで」を日時にする。今日のHH:MMがnowより後ならそれ、そうでなければ翌日のHH:MM。"""
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target
