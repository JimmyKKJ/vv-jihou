# Raspberry Pi セットアップ手順

対象: jimbase-station (Raspberry Pi 3 Model A+, Debian GNU/Linux 13 trixie)、
スピーカーは3.5mmヘッドホン出力(`aplay -l` で `card 0: Headphones` として認識済み)。

## 1. リポジトリの配置

`life` リポジトリの `vv-jihou/` submoduleとして管理しているため、Pi側では
このリポジトリ単体をcloneして使う(life全体を同期する必要はない)。

```
git clone https://github.com/<owner>/vv-jihou.git ~/vv-jihou
```

## 2. Python環境(venv)

Debian trixieはPEP668によりsystem pipへの直接installを拒否するため、専用venvを作る。

```
cd ~/vv-jihou
python3 -m venv .venv
.venv/bin/pip install jpholiday
```

## 3. 動作確認

```
.venv/bin/python bin/jihou_ctl.py --help
.venv/bin/python bin/jihou_ctl.py on
.venv/bin/python bin/jihou_ctl.py status
.venv/bin/python bin/jihou_chime.py --at 07:00
```

## 4. cron登録

`crontab -e` で以下を追加(パスは実際のclone先に合わせる):

```
*/5 * * * * /home/jimmy-kkj/vv-jihou/.venv/bin/python /home/jimmy-kkj/vv-jihou/bin/jihou_chime.py
@reboot sleep 30 && /usr/bin/aplay -q /home/jimmy-kkj/vv-jihou/audio/system/startup.wav && /usr/bin/aplay -q /home/jimmy-kkj/vv-jihou/audio/system/voice_intro.wav
```

(`@reboot` の `sleep 30` はオーディオデバイスの初期化待ち)

## 5. 制御コマンド例

```
.venv/bin/python bin/jihou_ctl.py off
.venv/bin/python bin/jihou_ctl.py interval --minutes 15
.venv/bin/python bin/jihou_ctl.py interval --hours 2
.venv/bin/python bin/jihou_ctl.py pause --minutes 30
.venv/bin/python bin/jihou_ctl.py pause --hours 2
.venv/bin/python bin/jihou_ctl.py pause --allday
.venv/bin/python bin/jihou_ctl.py window --start 07:00 --end 22:00
```
