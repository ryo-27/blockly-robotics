# Phase 1 実装設計

2026年11月17日。設計仕様書1.0に沿った最初の実装。
原本の要求と未確定項目を保持し、ここに初期実装の決定を記録する。

## コードと開発環境

実装開始時のリポジトリは設計仕様書だけで、走行試験コード・依存関係・既存DBはない。
MacにPython 3.14、uv、Node.js 26、pnpmがあり、PlatformIOはPythonの開発依存に追加した。
ESP32のUSBシリアルポートは確認できていない。

## 技術と配置

フロントエンドはReact、TypeScript、Vite、Tailwind CSS、Google Blocklyを採用しPhase 4で作成する。
APIはFastAPIとPydantic、永続化はPython標準のSQLite、ESP32はPlatformIOとArduinoを使う。
Python依存は `apps/server/uv.lock`、ESP32のプラットフォーム・JSONライブラリは
`firmware/platformio.ini` でバージョンを固定する。

- `apps/server/app/config.py`：個体設定と資格情報の検証
- `apps/server/app/schemas.py`：HTTPメッセージの型
- `apps/server/app/store.py`：SQLiteのトランザクションと接続状態
- `apps/server/app/main.py`：認証とAPI
- `apps/server/tests/`：通信・認証・再接続の自動試験
- `firmware/src/main.cpp`：Wi-Fi接続、登録、Heartbeat、再試行
- `firmware/src/motor_control.cpp`：GPIO初期化と停止
- `config/local.json` / `firmware/include/robot_config.h`：ローカル秘密設定（コミットしない）

APIは[FastAPIのlifespan](https://fastapi.tiangolo.com/advanced/testing-events/)で起動時に設定とDBを初期化する。
ESP32は[PlatformIOのEspressif 32](https://docs.platformio.org/en/stable/platforms/espressif32.html)
の `esp32dev` をボード設定に使う。Freenove実機のUSBポートと書き込みは別途確認する。
Wi-Fi接続には[Arduino ESP32 Wi-Fi API](https://docs.espressif.com/projects/arduino-esp32/en/latest/api/wifi.html)を使う。

## 通信と接続状態

ESP32から登録・Heartbeatを送信するHTTP/JSON方式。詳細は [protocol.md](protocol.md)。
Robot IDはサーバー側の事前設定で許可し、ロボットごとに異なるBearerトークンで認証する。
一覧の取得には実行ステーション用トークンを使う。将来のスタッフ・管理者権限とは分離して拡張する。
DBには個体のトークンのSHA-256だけを保存する。設定ファイルとESP32の秘密ヘッダーには元の値が必要。
通信はLAN内HTTPを前提とし、トークンの暗号化転送は提供しない。

登録成功で `OFFLINE → IDLE`、最後の正常な通信から3秒以上で `IDLE → OFFLINE`。
Heartbeatを受け直せば `OFFLINE → IDLE`。サーバー再起動時は全個体をOFFLINEに戻し、
セッションを無効化して新規登録を要求する。ロボット再起動時はbootIdが変わり旧セッションを無効化する。
切断状態は一覧取得時に時刻から算出する。Phase 1に予約・実行はない。

後続フェーズでは接続状態と利用状態を分け、表示にOFFLINEを重ねる。
利用状態の基本遷移は `IDLE → RESERVED → READY → RUNNING → COMPLETED` とする案。
実行中の停止要求はSTOPPED、異常はERRORへ遷移し、明示的な確認後にIDLEへ戻す。
通信断では実行権を解放せず不明状態として保持し、停止確認とスタッフによる復旧を必要とする。
これはPhase 2・3の詳細設計でAPI・DBとともに確定する。

## 安全停止

Phase 1はGPIOを初期化してLOW/LOWの空転停止を維持し、走行するコードを提供しない。
未設定・認証失敗・切断・復旧・再起動でも走行しない。物理モーター電源スイッチを残す。
起動処理が始まるまでのGPIOの電気状態はソフトウェアだけで保証できないため、実機で確認する。

Phase 2ではモーター制御を非ブロッキングにし、HTTP処理を独立したタスクへ移す。
STOP、ローカル時刻による実行許可の失効、命令時間・全体時間の上限を監視する。
通信エラー時は停止を固定し、通信復旧だけでは再開しない。
現在の接続監視の3秒は走行時の停止時間の保証ではない。
停止遅延の上限はPhase 2の設計と実機測定で決め、実行許可はその後に実装する。

## Phase 1の順序と完了条件

1. FastAPIの起動、個体設定、認証、SQLiteを実装する。
2. ESP32のWi-Fi接続・登録・Heartbeat・バックオフ付き再試行を実装する。
3. 認証拒否、重複Heartbeat、切断検出、再接続、再起動を自動試験する。
4. ファームウェアをビルドする。
5. L12とESP32実機で [接続試験](phase-1-verification.md) を行う。

実機がUSBシリアルによる命令送信なしでWi-Fiを通じて登録・再接続できた時点で完了とする。
