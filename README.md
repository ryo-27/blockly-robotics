# Blockly Robotics

子ども向けロボットプログラミング教室のローカル実行システム。
タブレット・MacBook・ESP32を同じWi-Fiに接続して利用します。

設計の原本は [システム設計仕様書](docs/system-design.md)、今回の実装方針は
[アーキテクチャ](docs/architecture.md)、通信契約は [プロトコル](docs/protocol.md) にあります。

Phase 1の通信基盤は実装・実機確認済みです。個体別認証、登録、Heartbeat、
SQLiteによる接続情報の保存、通信断検出・再登録に対応します。
モーターの走行命令、予約・実行管理、Blockly画面は後続フェーズです。
確認結果と今回の検証対象外の項目は [Phase 1 接続試験](docs/phase-1-verification.md) に記録しています。

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
今回のファームウェアは全状態でGPIO25/26/27/14をLOWに保ちます。
HTTPは停止中にのみ実行する実装です。走行制御の追加前に、ネットワーク処理を
独立させて停止監視を妨げない構造に変更します。

## 検証

```sh
cd apps/server
uv run --locked pytest -q
uv run --locked ruff check .
uv run --locked ruff format --check .
```

実機の手順は [Phase 1 接続試験](docs/phase-1-verification.md) を参照してください。
