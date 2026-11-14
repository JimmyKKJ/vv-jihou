# vv-jihou

VOICEVOX(キャラクター「春日部つむぎ」)で生成した音声パーツを使った、Raspberry Pi向け音声時報システム。

バージョン: **1.3**(`jihou_ctl.py --version`で確認できる)

## 変更履歴

- **1.3**
  - `schedule`を追加。時間(始まりと終わり)を指定して、時報の停止・鳴動を予約できる
    (`schedule add --start 10:30 --end 12:00 --action stop`)。始まりの時刻に読み上げなしで実行し、
    終わりの時刻で元の設定に戻る
  - 音声「スケジュールを」「追加しました」「更新しました」「削除しました」を追加(予約の操作の読み上げ用)。
    「変更しました」も入れてあるが、今は使っていない
  - `status`に予約の一覧を表示する

- **1.2**
  - `late-hours on|off`を追加。オンにすると、0時〜3時台を24時〜27時と読む(時報・操作の読み上げとも)。
    0時ちょうどは日付を読まず「24時ちょうどです」とする
  - 時刻の指定(`window`・`--until`)で、24:00〜27:55も受け付ける(翌日の0:00〜3:55として扱う)

- **1.1.2**
  - 音声「まで止めます」を追加し、`pause --until`はこの1本で読む
  - 一時停止・一時オンの読み上げの先頭に「時報を」を付ける(例:「時報を1時30分まで止めます」「時報を30分鳴らします」)
- **1.1.1**
  - 1.1の読み上げ用の音声3本(「まで」「鳴らします」「まで鳴らします」)を追加
  - `boost --until`は「まで鳴らします」を1本の音声で読む
- **1.1**
  - 稼働時間帯が0時をまたげるようになった(例: `window --start 06:00 --end 01:00`)
  - 一時オン`boost`を追加(オフ中・稼働時間帯外・一時停止中でも、期限まで設定の頻度で鳴らす。0時をまたいでも動く)
  - 一時停止・一時オンで「n時m分まで」を指定できるようになった(`pause --until HH:MM`・`boost --until HH:MM`)
- **1.0** — 最初の版

## 構成

- `audio/` — VOICEVOXで生成した音声パーツ
  - `hour/00.wav`〜`27.wav` — 時(0〜23時、24〜27時は`late-hours on`のときに使う)
  - `minute/00_choudo.wav`(ちょうどです)、`05.wav`〜`55.wav`(5分刻み)
  - `month/01.wav`〜`12.wav`、`day/01.wav`〜`31.wav`
  - `weekday/mon.wav`〜`sun.wav`(「〜曜日」、末尾に「です」を含まないbare音声)
  - `special/noon.wav`(正午です)、`holiday_bare.wav`(祝日)、`kokuminnokyujitsu_bare.wav`(休日、国民の休日用)
  - `system/` — オン/オフ・エラー・起動・設定変更等のシステムフレーズ、頻度設定/一時停止/
    稼働時間帯設定用のbare時間数(1〜12,24,48時間)・bare分数(5〜60分)
    - v1.1で追加: `phrase_made.wav`(「まで」)・`phrase_narashimasu.wav`(「鳴らします」)・
      `phrase_made_narashimasu.wav`(「まで鳴らします」、`boost --until`用)。
      v1.1.2で追加: `phrase_made_tomemasu.wav`(「まで止めます」、`pause --until`用)。
      `phrase_made.wav`は今は使っていない(他の読み上げ用に残している)。
      無い場合は、その部分を省いて読み上げる(設定の変更自体は行われる、`state/jihou.log`に警告)
    - v1.3で追加(予約の操作の読み上げ用): `phrase_schedule_wo.wav`(「スケジュールを」)・
      `phrase_tsuika_shimashita.wav`(「追加しました」)・`phrase_koushin_shimashita.wav`(「更新しました」)・
      `phrase_sakujo_shimashita.wav`(「削除しました」)。無い場合は、その部分を省いて読み上げる。
      `phrase_henkou_shimashita.wav`(「変更しました」)は今は使っていない(他の読み上げ用に残している)
- `bin/jihou_ctl.py` — オン/オフ・頻度設定・一時停止・一時オン・稼働時間帯設定・予約のCLI(`--help`対応)
- `bin/jihou_chime.py` — cronから定期実行し、始まりの時刻が来た予約を適用してから、
  鳴らすべきタイミングか判定して再生する
- `bin/jihou_common.py` — 共通ユーティリティ(state.json・schedule.jsonの読み書き・音声再生・パス解決)
- `state/state.example.json` — 初期設定のテンプレート(実行時に書き換わる`state/state.json`は
  git管理対象外。セットアップ時にこのテンプレートをコピーして使う)
- `state/schedule.json` — 予約の一覧(git管理対象外。`schedule add`が最初に作るので、用意は要らない)
- `install/README.md` — Raspberry Pi側のセットアップ手順(venv・cron登録)

## 時報の仕様

### 通常時(稼働時間帯内、設定した頻度ごと)

`[時]` + `[分]` を再生する。分がちょうど0の時は必ず「ちょうどです」を使う。

例外:
- **正午(12時0分)**: `special/noon.wav`(正午です)のみを再生し、通常の時+分構成は行わない
- **日付が変わる瞬間(0時0分)**: 通常の時報の前に日付アナウンスを追加する
  - `[月]` + `[日]` + `[曜日]`
    + その日が祝日なら`special/holiday_bare.wav`(祝日)、
      「国民の休日」(祝日と祝日に挟まれた平日)なら`special/kokuminnokyujitsu_bare.wav`(休日)、
      それ以外(通常の土日を含む)は何も挿入しない
    + `[0時]` + `[ちょうどです]`
  - 祝日/国民の休日の判定は [`jpholiday`](https://pypi.org/project/jpholiday/) ライブラリ
    (オフライン動作、ネット接続不要)を使う

### 稼働時間帯の境界

- その日の最初のチャイム(稼働開始時刻以降で最初に頻度に一致する時刻)の前に「おはようございます」
- その日の最後のチャイム(稼働終了時刻以前で最後に頻度に一致する時刻)の後に「おやすみなさい」
- 終了時刻が開始時刻より前なら、0時をまたぐ時間帯として扱う(例: 06:00〜01:00は、
  6時から翌1時まで鳴り、「おやすみなさい」は翌1時の時報の後)

### 24時〜27時の読み(v1.2)

`jihou_ctl.py late-hours on` で、0時〜3時台を24時〜27時と読む(既定はオフ)。

- 時報: 1時30分は「25時30分」。4時以降は通常どおり(28時以降の音声は無い)
- 0時ちょうど: 日付のアナウンスをせず「24時ちょうどです」だけを読む(まだ前の日の続きとして扱う)
- 操作の読み上げ: `window`・`pause --until`・`boost --until`の時刻も24時〜27時で読む
  (例:「6時から25時までに設定しました」「時報を25時30分まで止めます」)
- 切り替えを読み上げる音声は無いため、`late-hours`は画面への表示だけ行う
- 時刻の指定では、設定に関わらず`25:30`のような24:00〜27:55も使える(翌日の1:30等として扱う)

### オン/オフ機能

`jihou_ctl.py on` / `off` で有効/無効を切り替え、即座に「時報をオンにしました」/
「時報をオフにしました」を再生する。`on` は設定済みの一時停止も解除するため、
予定より早く再開したい場合にも使える。`off` は設定中の一時オンも取り消す。

### 頻度設定機能

`--minutes 5|10|15|30` または `--hours 1|2|3|6|12` で設定変更。
例:「15分ごとに設定しました」「2時間ごとに設定しました」。

### 一時停止機能

`--minutes 5|10|15|30` / `--hours 1〜12,24,48のいずれか` / `--until HH:MM` / `--allday` で一時停止。
例:「時報を30分止めます」「時報を24時間止めます」「時報を1時30分まで止めます」「時報を終日止めます」。

- `--until HH:MM`(5分刻み)は、その時刻の時報から再開する(その時刻の時報は鳴る)。
  指定した時刻が今日もう過ぎていれば、翌日のその時刻とみなす(23時に`--until 01:30`なら翌1時30分)
- `--allday` は今日の23:59:59まで止める。0時をまたぐ時間帯の中で実行した場合は、
  その時間帯の終わり(終了時刻の時報・「おやすみなさい」を含む)まで止める
- 一時停止を設定すると、設定中の一時オンは取り消される

### 一時オン機能(v1.1)

`jihou_ctl.py boost` で、オフ中・稼働時間帯外・一時停止中でも、期限まで設定の頻度で鳴らす。
期限を過ぎると、元の設定(オン/オフ・稼働時間帯)に戻る。
`--minutes 5|10|15|30` / `--hours 1〜12,24,48のいずれか` / `--until HH:MM` のいずれか1つで期限を指定する。
例:「時報を30分鳴らします」「時報を2時間鳴らします」「時報を1時まで鳴らします」。

- `--until HH:MM` は、その時刻の時報まで鳴らす(その時刻の時報も鳴る)。過ぎた時刻なら翌日
- 「おはようございます」「おやすみなさい」は、通常の稼働時間帯の境界でだけ付ける
  (一時オンで時間帯外に鳴らす分には付けない)
- 一時オンを設定すると、設定中の一時停止は取り消される

### 予約機能(v1.3)

`jihou_ctl.py schedule add --start <時刻> --end <時刻> --action stop|ring` で、時間を指定して
時報の停止(`stop`)・鳴動(`ring`)を予約する。

- **実行**: cronで5分ごとに動く`jihou_chime.py`が、始まりの時刻に予約を適用する(読み上げはしない)。
  適用した予約は一覧から消える
  - 停止 = `pause --until <終わり>`と同じ。始まりの時刻の時報から止まり、終わりの時刻の時報から鳴る
    (例: 10:30〜12:00なら、10:30の時報は鳴らず、12:00の時報は鳴る)
  - 鳴動 = `boost --until <終わり>`と同じ。オフ中・稼働時間帯外・一時停止中でも、始まりの時刻の時報から
    終わりの時刻の時報まで鳴らす
  - 終わりの時刻を過ぎると、元の設定(オン/オフ・稼働時間帯)に戻る
  - 適用した後の操作(`pause`・`boost`・`on`・`off`)は、今までどおり後から出した指示が効く
    (予約による停止を早く切り上げるなら`on`、予約による鳴動を早く止めるなら`off`か`pause`)
- **時刻の指定**: `--start`と`--end`は、両方を`HH:MM`(5分刻み、24:00〜27:55も可)か、
  両方を`YYYY-MM-DDTHH:MM`で指定する
  - `HH:MM`の場合、終わりは「今より後で最も近いその時刻」、始まりは「終わりより前で最も近いその時刻」。
    終わりが始まりより前なら0時をまたぐ(`--start 23:00 --end 25:00`は23時〜翌1時)
  - 始まりが過ぎていて終わりが先の予約(11:00に`--start 10:30 --end 12:00`)は、次の5分刻みの時刻から効く
  - 他の予約と時間が重なる指定はエラーになる(終わりと始まりが同じ時刻なら重ならない)
- **更新・削除**: `schedule update --id <番号> [--start] [--end] [--action]`は、指定した項目だけを変える。
  `HH:MM`は今の予約の始まりの日付のまま時刻だけを差し替え、日付も変えるときは`YYYY-MM-DDTHH:MM`で指定する。
  `schedule remove --id <番号>`で1件、`schedule clear`で全て削除する。番号は`schedule list`か`status`で見る
- **読み上げ**: 追加は「スケジュールを追加しました」、更新は「スケジュールを更新しました」、
  削除は「スケジュールを削除しました」(`list`と、予約が無いときの`clear`は読み上げない)
- **止まっていた場合**: 機器が止まっていて始まりの時刻を過ぎた予約は、終わりの時刻の前なら次の起動時に適用し、
  終わりの時刻も過ぎていれば適用せずに消す(どちらも`state/jihou.log`に書く)
- `state/schedule.json`が壊れていた場合は、`state/schedule.broken.json`へ退避し、予約なしとして扱う

### 稼働時間帯設定機能

`--start HH:MM --end HH:MM`(5分刻み)で設定変更。終了を開始より前にすると0時をまたぐ
(開始と終了を同じ時刻にはできない)。
例:「7時から22時までに設定しました」「6時から1時までに設定しました」。

## 使い方

```
python bin/jihou_ctl.py --help
python bin/jihou_ctl.py on
python bin/jihou_ctl.py interval --minutes 15
python bin/jihou_ctl.py pause --hours 2
python bin/jihou_ctl.py pause --until 13:00     # 13時の時報から再開
python bin/jihou_ctl.py boost --until 01:00     # 翌1時の時報まで鳴らす
python bin/jihou_ctl.py window --start 07:00 --end 22:00
python bin/jihou_ctl.py window --start 06:00 --end 01:00   # 0時をまたぐ
python bin/jihou_ctl.py late-hours on                     # 0時〜3時台を24時〜27時と読む
python bin/jihou_ctl.py window --start 06:00 --end 25:00   # 25:00は翌1:00と同じ
python bin/jihou_ctl.py schedule add --start 10:30 --end 12:00 --action stop   # 10:30〜12:00は止める
python bin/jihou_ctl.py schedule add --start 23:00 --end 25:00 --action ring   # 23時〜翌1時は鳴らす
python bin/jihou_ctl.py schedule add --start 2026-11-20T10:30 --end 2026-11-20T12:00 --action stop
python bin/jihou_ctl.py schedule list
python bin/jihou_ctl.py schedule update --id 1 --end 12:30
python bin/jihou_ctl.py schedule remove --id 1
python bin/jihou_ctl.py status
python bin/jihou_ctl.py --version

# cronから定期実行する想定(現在時刻で判定)
python bin/jihou_chime.py

# 動作確認用(指定時刻・日時で判定)
python bin/jihou_chime.py --at 07:00

# 0時の日付アナウンス(祝日/国民の休日パターン)を確認する場合は、稼働時間帯に
# 0時を含めておく必要がある(既定は06:30〜23:00のため、そのままでは無音になる)
python bin/jihou_ctl.py window --start 06:30 --end 00:30
python bin/jihou_chime.py --at 2026-09-22T00:00
python bin/jihou_ctl.py window --start 06:30 --end 23:00  # 確認後は既定値(06:30〜23:00)に戻す

# テスト(Linux。fcntlを使うためWindowsでは動かない)
python -m unittest discover tests
```

Raspberry Pi実機でのセットアップ手順は [`install/README.md`](install/README.md) を参照。

## ライセンス

コード(`bin/`以下等)は[MITライセンス](LICENSE)。

`audio/`配下の音声データはVOICEVOX(キャラクター「春日部つむぎ」)で生成したもので、
別途VOICEVOXおよびキャラクター利用規約が適用される。利用にあたっては以下のクレジット
表記が必要:

> VOICEVOX:春日部つむぎ

商用・非商用問わず利用可能だが、誹謗中傷目的や公式イラストを使った商品化等は禁止されて
いる。詳細・最新情報は[春日部つむぎ公式HP・利用規約](https://tsukushinyoki10.wixsite.com/ktsumugiofficial/%E5%88%A9%E7%94%A8%E8%A6%8F%E7%B4%84)・
[VOICEVOX公式サイト](https://voicevox.hiroshiba.jp/)を必ず確認すること。
