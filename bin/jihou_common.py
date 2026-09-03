"""vv-jihou の共通ユーティリティ(state.json の読み書き・音声再生・パス解決)。"""

import contextlib
import fcntl
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUDIO_DIR = os.path.join(REPO_ROOT, "audio")
STATE_PATH = os.path.join(REPO_ROOT, "state", "state.json")
LOG_PATH = os.path.join(REPO_ROOT, "state", "jihou.log")
LOCK_PATH = os.path.join(REPO_ROOT, "state", ".state.lock")

DEFAULT_STATE = {
    "enabled": False,
    "interval_minutes": 15,
    "active_start": "06:30",
    "active_end": "23:00",
    "paused_until": None,
}

# 頻度設定・一時停止で選べる値(録音済みの音声パーツに対応するもののみ)
ALLOWED_INTERVAL_MINUTES = [5, 10, 15, 30]
ALLOWED_INTERVAL_HOURS = [1, 2, 3, 6, 12]
ALLOWED_PAUSE_HOURS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 24, 48]


def _valid_hhmm(value):
    if not isinstance(value, str):
        return False
    try:
        h, m = value.split(":")
        h, m = int(h), int(m)
    except ValueError:
        return False
    return 0 <= h <= 23 and 0 <= m <= 59


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
        if hhmm_to_minutes(sh, sm) >= hhmm_to_minutes(eh, em):
            log(
                "警告: active_startがactive_end以降になっているため既定値に修復します"
                f"(元の値: {state['active_start']} / {state['active_end']})"
            )
            state["active_start"] = DEFAULT_STATE["active_start"]
            state["active_end"] = DEFAULT_STATE["active_end"]
            changed = True

    paused_until = state.get("paused_until")
    if paused_until is not None:
        try:
            parsed_paused_until = datetime.fromisoformat(paused_until)
        except (TypeError, ValueError):
            log(f"警告: paused_untilの値が不正なためクリアします(元の値: {paused_until!r})")
            state["paused_until"] = None
            changed = True
        else:
            if parsed_paused_until.tzinfo is not None:
                # 内部では常にnaive(タイムゾーン無し、ローカル時刻)で扱う規約のため、
                # tz付きの値はdatetime.now()との比較でTypeErrorになり続けてしまう。
                log(f"警告: paused_untilにタイムゾーン情報が含まれていたためクリアします(元の値: {paused_until!r})")
                state["paused_until"] = None
                changed = True

    if not isinstance(state.get("enabled"), bool):
        log(f"警告: enabledの値が不正なためFalseに修復します(元の値: {state.get('enabled')!r})")
        state["enabled"] = False
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
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp_path = STATE_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, STATE_PATH)


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


def play_sequence(paths):
    """複数のwavを順番に再生する(aplayは再生完了までブロックするので単に直列に呼ぶだけでよい)。

    aplay自体の実行失敗(デバイスエラー等)はPlaybackErrorに包んで送出する。呼び出し側は
    このエラーを捕捉して、生のトレースバックを出さずに扱うこと(設定変更自体は
    play_sequence呼び出し前に既に保存済みであることが多いため、致命エラー扱いにしない)。
    """
    for path in paths:
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
    """'HH:MM' 形式をパースし、5分刻みでなければエラーにする。"""
    try:
        dt = datetime.strptime(value, "%H:%M")
    except ValueError:
        raise ValueError(f"時刻は HH:MM 形式で指定してください(指定値: {value})")
    if dt.minute % 5 != 0:
        raise ValueError(f"時刻は5分刻みで指定してください(指定値: {value})")
    return dt.hour, dt.minute


def hhmm_to_minutes(hh, mm):
    return hh * 60 + mm


def now_minutes_of_day(now=None):
    now = now or datetime.now()
    return now.hour * 60 + now.minute
