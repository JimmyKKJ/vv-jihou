"""vv-jihou の共通ユーティリティ(state.json の読み書き・音声再生・パス解決)。"""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUDIO_DIR = os.path.join(REPO_ROOT, "audio")
STATE_PATH = os.path.join(REPO_ROOT, "state", "state.json")
LOG_PATH = os.path.join(REPO_ROOT, "state", "jihou.log")

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


def load_state():
    if not os.path.exists(STATE_PATH):
        return dict(DEFAULT_STATE)
    with open(STATE_PATH, "r", encoding="utf-8") as f:
        state = json.load(f)
    merged = dict(DEFAULT_STATE)
    merged.update(state)
    return merged


def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")


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


def play_sequence(paths):
    """複数のwavを順番に再生する(aplayは再生完了までブロックするので単に直列に呼ぶだけでよい)。"""
    for path in paths:
        subprocess.run(["aplay", "-q", path], check=True)


def play_error():
    try:
        subprocess.run(["aplay", "-q", audio_path("system", "error.wav")], check=False)
    except FileNotFoundError:
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
