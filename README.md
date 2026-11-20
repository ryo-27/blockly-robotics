# Blockly Robotics

子ども向けロボットプログラミング教室のローカル実行システム。
タブレット・MacBook・ESP32を同じWi-Fiに接続して利用します。

設計の原本は [システム設計仕様書](docs/system-design.md)、今回の実装方針は
[アーキテクチャ](docs/architecture.md)、通信契約は [プロトコル](docs/protocol.md) にあります。

Phase 1の通信基盤は実機確認済みです。Phase 2ではMOVE・TURN・WAITの順次実行、
個体別キャリブレーション、STOP、通信異常時の停止、再実行防止と運営者向け試験APIを追加しました。
Phase 2は2026年11月19日に利用者が実機確認を完了しました。Phase 3では参加者セッション、プログラム保存、REPEAT展開、予約、通常実行と履歴を追加しました。
Phase 3は2026年11月20日に利用者が予約から実行完了までの実機確認を完了しました。Phase 4のBlockly画面とPhase 5の3台管理も実装し、模擬機体で検証しました。
2026年11月21日に利用者が画面からの単機実行を確認し、Phase 4の実機確認を完了しました。
Phase 5は実装・模擬検証・単機確認済みで、2・3台目の完成後に複数機の実機確認を行います。
画面からの実機実行と実機3台の独立走行は、これから確認します。

[Phase 1の記録](docs/phase-1-verification.md) と [Phase 2の試験手順](docs/phase-2-verification.md)、[Phase 3の確認手順](docs/phase-3-verification.md) を参照してください。

## システムマップ

[閲覧ページ](docs/system-map/index.html) で、全体構成、起動と設定、登録と制御通信、実行と停止、
指示の送信、接続状態と復旧、ファイル索引を確認できます。要素を選ぶと、設定の意味と該当コードを表示します。
[読み方と更新方法](docs/system-map/README.md) も参照してください。

## Blockly画面（Phase 4・5）

参加者の進行は手元のプリントで管理し、端末は機体と指示だけを設定します。
日本語ブロックで前進・後退・左右・待機・くりかえしを作り、選択した機体へ直接送信できます。
指示を組み終えてから送信先の機体を選びます。下書きはこのタブに保持し、ページ全体をスクロールしない画面で操作できます。
ブロックを読んで動きを考えられるよう、自動生成の指示一覧は表示しません。
「管理」の中央モーダルに機体の状態・接続設定・開発用DB確認をまとめています。DBは読み取り専用で、テーブル・行・JSONを閲覧できます。

```sh
npm ci --prefix apps/web
npm run build --prefix apps/web
```

ビルド後にAPIを再起動すると、MacのLANアドレスのルート `/` で画面を配信します。
例：`http://192.168.179.45:8000/`。送信前に「管理 → 接続設定」でstationTokenを入力します。参加者登録は不要です。
画面は同じオリジンのAPIを使い、秘密のトークンはビルドへ埋め込みません。

[Phase 4の起動・画面確認](docs/phase-4-verification.md) と [Phase 5の3台運用・模擬試験](docs/phase-5-verification.md) を参照してください。
既存の個体設定とDBはそのまま使い、雛形を上書きしないでください。

## APIサーバーの起動

Python 3.11以上、uvを使用します。以下はリポジトリのルートで実行します。

```sh
uv sync --project apps/server --locked
cp -n config/local.example.json config/local.json
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
- `GET /api/robots`：個体一覧（stationTokenが必要）
- `/docs`：API仕様と試験用フォーム。右上のAuthorizeでトークンを入力できます。

既定のDBはルートの `data/workshop.sqlite3` です。
`WORKSHOP_CONFIG` と `WORKSHOP_DB` 環境変数でそれぞれの保存先を変更できます。
起動時に既知の接続をすべてOFFLINEに戻し、個体情報・実行履歴は保持します。
Phase 2のDBは起動時に共通のexecutions表へ自動移行します。実行番号も引き継ぐためDBの削除は不要です。
設定から除いたロボットは一覧・認証の対象から外れます。

## 不要になった参加者管理の整理

2026年11月21日に参加者・保存プログラム・予約の旧APIと3テーブルを廃止しました。
現在は `POST /api/robots/{robot_id}/executions` へASTを直接送ります。
起動時に旧表があれば、全実行が終端であることを確認し、`data/backups/`へDBを退避して削除します。
旧予約イベントと実行内の旧参照も消去します。バックアップには削除前の内容が含まれ、Git対象外です。
実行履歴・requestId・採番の最高値は二重実行防止に必要なため保持します。
旧TTL設定キーは既存ファイルを読み込むためだけに受け付け、現在の運用には使いません。
[Phase 3の記録](docs/phase-3-verification.md) は当時の検証記録です。

## ESP32ファームウェア

```sh
cp -n firmware/include/robot_config.example.h firmware/include/robot_config.h
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

## クエスト・if

応用ブロックに「もし〜なら／そうでなければ」を追加しました。条件は「前に進めるか」と「今いるマスの種類」です。
送信画面で機体と任意のクエストを選びます。if使用時はクエスト必須です。
マップ上の開始位置・向きに機体を置いて実行してください。送信前には正誤や予測経路を表示しません。
岩・マップ外への移動は送信を止め、ゴール未達なら実行できます。完了後にマップ上のクリア判定を表示します。
マップの正本は`config/quests.json`。教材8マップを基にし、ゴールの向き・経由・最小ブロック数は判定しません。
変更時はversionを上げてサーバーと画面の選択を揃えてください。過去の実行は受付時のマップを保持します。
サーバーが分岐と繰り返しを解決するため、ESP32の再書き込みは不要です。
