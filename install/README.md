# セットアップ手順

対象: Raspberry Pi(または`aplay`が使える他のLinux機、Debian/Raspberry Pi OS系を想定)+
スピーカー。事前に `aplay -l` でスピーカーが再生デバイスとして認識されていることを
確認しておく(3.5mmヘッドホン出力・USBオーディオいずれでも、`aplay`から鳴らせれば動く)。
**Python 3.9以降が必須**(依存する`jpholiday`パッケージがPython 3.9以上を要求するため。
コード自体はf-string等の標準機能のみ使用。実際の動作確認はPython 3.14で実施、
Raspberry Pi実機での確認はまだ)。

## 1. リポジトリの取得

```
git clone https://github.com/JimmyKKJ/vv-jihou.git ~/vv-jihou
cd ~/vv-jihou
```

## 2. 初期状態ファイルの用意

`state/state.json`は実行時に書き換わる状態ファイルのためgit管理対象外(.gitignore)にしている。
テンプレート(`state/state.example.json`)からコピーして作る。

```
cp state/state.example.json state/state.json
```

## 3. Python環境(venv)

Debian 12(bookworm)・13(trixie)以降はPEP668によりsystem pipへの直接installを拒否するため、
専用venvを作る
(古いRaspberry Pi OSでは venv なしの直接pip installでも動く場合があるが、環境を汚さない
ため venv を推奨)。`venv`モジュールはDebian/Raspberry Pi OSの素の状態では入っておらず
別パッケージ(`python3-venv`)が必要なため、無ければ先にインストールする。

```
sudo apt install -y python3-venv   # 未インストールの場合のみ
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 4. 動作確認

```
.venv/bin/python bin/jihou_ctl.py --help
.venv/bin/python bin/jihou_ctl.py on
.venv/bin/python bin/jihou_ctl.py status
.venv/bin/python bin/jihou_chime.py --at 07:00
```

## 5. cron登録

`crontab -e` で以下を追加(パスは実際のclone先・ユーザー名に合わせて書き換える):

```
*/5 * * * * /path/to/vv-jihou/.venv/bin/python /path/to/vv-jihou/bin/jihou_chime.py
@reboot sleep 30 && /usr/bin/aplay -q /path/to/vv-jihou/audio/system/startup.wav && /usr/bin/aplay -q /path/to/vv-jihou/audio/system/voice_intro.wav
```

(`@reboot` の `sleep 30` はオーディオデバイスの初期化待ち)

## 6. 制御コマンド例

```
.venv/bin/python bin/jihou_ctl.py off
.venv/bin/python bin/jihou_ctl.py interval --minutes 15
.venv/bin/python bin/jihou_ctl.py interval --hours 2
.venv/bin/python bin/jihou_ctl.py pause --minutes 30
.venv/bin/python bin/jihou_ctl.py pause --hours 2
.venv/bin/python bin/jihou_ctl.py pause --allday
.venv/bin/python bin/jihou_ctl.py window --start 07:00 --end 22:00
```
