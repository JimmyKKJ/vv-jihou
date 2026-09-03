# vv-jihou

VOICEVOX(春日部つむぎ)で生成した音声パーツを使った、Raspberry Pi向け音声時報システム。

- `audio/` — VOICEVOXで生成した音声パーツ(時・分・月・日・曜日・祝日/国民の休日・
  正午・システムフレーズ)
- `bin/jihou_ctl.py` — オン/オフ・頻度設定・一時停止・稼働時間帯設定のCLI
- `bin/jihou_chime.py` — cronから定期実行し、鳴らすべきタイミングか判定して再生する
- `state/state.json` — 現在の設定(有効/無効・頻度・稼働時間帯・一時停止状態)
- `install/README.md` — Raspberry Pi側のセットアップ手順

詳細な仕様・設計判断は `life` リポジトリの会話ログ、または各スクリプトの
docstring/コメントを参照。
