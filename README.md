# vv-jihou

VOICEVOX(キャラクター「春日部つむぎ」)で生成した音声パーツを使った、Raspberry Pi向け音声時報システム。

バージョン: **1.1**(`jihou_ctl.py --version`で確認できる)

## 変更履歴

- **1.1**
  - 稼働時間帯が0時をまたげるようになった(例: `window --start 06:00 --end 01:00`)
  - 一時オン`boost`を追加(オフ中・稼働時間帯外・一時停止中でも、期限まで設定の頻度で鳴らす。0時をまたいでも動く)
  - 一時停止・一時オンで「n時m分まで」を指定できるようになった(`pause --until HH:MM`・`boost --until HH:MM`)
- **1.0** — 最初の版

## 構成

- `audio/` — VOICEVOXで生成した音声パーツ
  - `hour/00.wav`〜`27.wav` — 時(0〜23時、24〜27時は予備)
  - `minute/00_choudo.wav`(ちょうどです)、`05.wav`〜`55.wav`(5分刻み)
  - `month/01.wav`〜`12.wav`、`day/01.wav`〜`31.wav`
  - `weekday/mon.wav`〜`sun.wav`(「〜曜日」、末尾に「です」を含まないbare音声)
  - `special/noon.wav`(正午です)、`holiday_bare.wav`(祝日)、`kokuminnokyujitsu_bare.wav`(休日、国民の休日用)
  - `system/` — オン/オフ・エラー・起動・設定変更等のシステムフレーズ、頻度設定/一時停止/
    稼働時間帯設定用のbare時間数(1〜12,24,48時間)・bare分数(5〜60分)
    - v1.1で追加: `phrase_made.wav`(「まで」)・`phrase_narashimasu.wav`(「鳴らします」)。
      無い場合は、その部分を省いて読み上げる(設定の変更自体は行われる、`state/jihou.log`に警告)
- `bin/jihou_ctl.py` — オン/オフ・頻度設定・一時停止・一時オン・稼働時間帯設定のCLI(`--help`対応)
- `bin/jihou_chime.py` — cronから定期実行し、鳴らすべきタイミングか判定して再生する
- `bin/jihou_common.py` — 共通ユーティリティ(state.json読み書き・音声再生・パス解決)
- `state/state.example.json` — 初期設定のテンプレート(実行時に書き換わる`state/state.json`は
  git管理対象外。セットアップ時にこのテンプレートをコピーして使う)
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

### オン/オフ機能

`jihou_ctl.py on` / `off` で有効/無効を切り替え、即座に「時報をオンにしました」/
「時報をオフにしました」を再生する。`on` は設定済みの一時停止も解除するため、
予定より早く再開したい場合にも使える。`off` は設定中の一時オンも取り消す。

### 頻度設定機能

`--minutes 5|10|15|30` または `--hours 1|2|3|6|12` で設定変更。
例:「15分ごとに設定しました」「2時間ごとに設定しました」。

### 一時停止機能

`--minutes 5|10|15|30` / `--hours 1〜12,24,48のいずれか` / `--until HH:MM` / `--allday` で一時停止。
例:「30分止めます」「24時間止めます」「1時30分まで止めます」「終日止めます」。

- `--until HH:MM`(5分刻み)は、その時刻の時報から再開する(その時刻の時報は鳴る)。
  指定した時刻が今日もう過ぎていれば、翌日のその時刻とみなす(23時に`--until 01:30`なら翌1時30分)
- `--allday` は今日の23:59:59まで止める。0時をまたぐ時間帯の中で実行した場合は、
  その時間帯の終わり(終了時刻の時報・「おやすみなさい」を含む)まで止める
- 一時停止を設定すると、設定中の一時オンは取り消される

### 一時オン機能(v1.1)

`jihou_ctl.py boost` で、オフ中・稼働時間帯外・一時停止中でも、期限まで設定の頻度で鳴らす。
期限を過ぎると、元の設定(オン/オフ・稼働時間帯)に戻る。
`--minutes 5|10|15|30` / `--hours 1〜12,24,48のいずれか` / `--until HH:MM` のいずれか1つで期限を指定する。
例:「30分鳴らします」「2時間鳴らします」「1時まで鳴らします」。

- `--until HH:MM` は、その時刻の時報まで鳴らす(その時刻の時報も鳴る)。過ぎた時刻なら翌日
- 「おはようございます」「おやすみなさい」は、通常の稼働時間帯の境界でだけ付ける
  (一時オンで時間帯外に鳴らす分には付けない)
- 一時オンを設定すると、設定中の一時停止は取り消される

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
