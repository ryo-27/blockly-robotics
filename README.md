# Blockly Robotics

子ども向けロボットプログラミング教室のローカル実行システム。
タブレット・MacBook・ESP32を同じWi-Fiに接続して利用します。

設計の原本は [システム設計仕様書](docs/system-design.md)、今回の実装方針は
[アーキテクチャ](docs/architecture.md)、通信契約は [プロトコル](docs/protocol.md) にあります。

Phase 1の通信基盤は実機確認済みです。Phase 2ではMOVE・TURN・WAITの順次実行、
個体別キャリブレーション、STOP、通信異常時の停止、再実行防止と運営者向け試験APIを追加しました。
Phase 2は2026年11月19日に利用者が実機確認を完了しました。予約・プログラム保存・Blocklyは後続フェーズです。

[Phase 1の記録](docs/phase-1-verification.md) と [Phase 2の試験手順](docs/phase-2-verification.md) を参照してください。

## システムマップ

[閲覧ページ](docs/system-map/index.html) で、全体構成、起動と設定、登録と制御通信、実行と停止、
接続状態と復旧、ファイル索引を確認できます。要素を選ぶと、設定の意味と該当コードを表示します。
[読み方と更新方法](docs/system-map/README.md) も参照してください。

## APIサーバーの起動

Python 3.11以上、uvを使用します。以下はリポジトリのルートで実行します。

```sh
uv sync --project apps/server --locked
cp config/local.example.json config/local.json
```

`config/local.json` の `stationToken` と各ロボットの `token` を、個別に生成した
ランダムな値に変更してください。生成には次を必要な回数実行します。

```sh
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

設定ファイルの例の値をそのまま運用しないでください。ロボットを増やす場合は
`robots` に個体を追加し、サーバーを再起動します。Robot ID・トークンは各個体で異なる値にします。
設定ファイルとESP32の秘密設定はgitの対象から除外しています。

```sh
cd apps/server
uv run --locked uvicorn app.main:app --host 0.0.0.0 --port 8000
```

ロボットの接続確認中は単一プロセスで動かします。`--workers` や複数サーバーから
同じDBを共有する構成には未対応です。開発時の `--reload` は再登録を発生させます。

- `GET /api/health`：起動確認（認証不要）
- `GET /api/robots`：個体一覧（`Authorization: Bearer <stationToken>` が必要）
- `/docs`：API仕様と試験用フォーム。右上のAuthorizeでトークンを入力できます。

既定のDBはルートの `data/workshop.sqlite3` です。
`WORKSHOP_CONFIG` と `WORKSHOP_DB` 環境変数でそれぞれの保存先を変更できます。
起動時に既知の接続をすべてOFFLINEに戻し、個体情報は保持します。
設定から除いたロボットは一覧・認証の対象から外れます。

## ESP32ファームウェア

```sh
cp firmware/include/robot_config.example.h firmware/include/robot_config.h
```

`robot_config.h` を個体に合わせて編集します。

- `ROBOT_CONFIGURED`：設定後に `true` に変更
- `ROBOT_ID` / `ROBOT_TOKEN`：サーバーの個体設定と同じ値
- `WIFI_SSID` / `WIFI_PASSWORD`：L12のWi-Fi設定（ESP32が利用できる2.4GHz）
- `SERVER_URL`：MacBookのLAN内URL（末尾の `/` なし、`localhost` は使わない）

次はルートから実行します。PlatformIOは上記の `uv sync` で導入されます。

```sh
apps/server/.venv/bin/pio run --project-dir firmware
```

USBで対象のESP32を接続したら、ポートを確認して指定します。

```sh
apps/server/.venv/bin/pio device list
apps/server/.venv/bin/pio run --project-dir firmware --target upload --upload-port /dev/cu.YOUR_DEVICE
apps/server/.venv/bin/pio device monitor --project-dir firmware --port /dev/cu.YOUR_DEVICE
```

`robot_config.h` がない場合もテンプレートでビルドできますが、Wi-Fi通信は開始しません。
走行には、サーバー設定の `motionEnabled: true` とESP32設定の
`#define ROBOT_MOTION_ENABLED true` の両方が必要です。未定義・falseでは走行しません。
個体別の反転と時間はサーバーの `calibration` で設定し、実行受付時に固定します。
設定雛形は暫定値です。実機の方向と1マス・90度の時間を確認して調整します。

運営者のSwagger操作から `POST /api/robots/{robot_id}/test-executions` へ命令を送り、
`GET /api/test-executions/{execution_id}` で状態を確認し、
`POST /api/test-executions/{execution_id}/stop` で停止を要求できます。
具体的な送信例と安全確認は [Phase 2の試験手順](docs/phase-2-verification.md) にあります。

HTTP通信とモーターの実行・停止監視は別タスクです。許可が1.5秒で失効すると停止します。
停止遅延の実測値はまだないため、実機で試験するまでは設計上の期限として扱います。

## 検証

```sh
cd apps/server
uv run --locked pytest -q
uv run --locked ruff check .
uv run --locked ruff format --check .
```

リポジトリルートでファームウェアのホスト試験とマップの同期を確認できます。

```sh
bash scripts/test_firmware.sh
python3 scripts/build_system_map.py --check
```

ホスト試験にはC++17対応の `c++` と、PlatformIOビルドで取得したArduinoJsonが必要です。
実機の手順は [Phase 2 実行試験](docs/phase-2-verification.md) を参照してください。
