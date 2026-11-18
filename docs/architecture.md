# ロボット教室の実装構成

Phase 1は実機確認済み。Phase 2は実行エンジンと運営者用の試験APIを実装し、2026年11月19日に利用者が実機確認を完了した。
要求の原本は [設計仕様書](system-design.md)、処理・設定・ファイルの対応は
[システムマップ](system-map/index.html)、通信契約は [protocol.md](protocol.md) を参照する。

## サーバー

MacBook上のFastAPIが、事前登録した個体をBearerトークンで認証する。
SQLiteには個体のトークンのSHA-256だけを保存し、設定ファイルには元の資格情報を保持する。
LAN内HTTPで運用し、単一プロセスを前提にする。React / BlocklyはPhase 4で追加する。

- `app/config.py`：個体ID・トークン、走行有効化、キャリブレーションの設定。
- `app/main.py`：認証とHTTPの入口、lifespanで設定・DBを初期化。
- `app/schemas.py`：Phase 1の互換メッセージと一覧の型。
- `app/store.py`：個体と接続の永続化、既存robots表への列追加、接続状態の算出。
- `app/execution_models.py`：MOVE / TURN / WAIT、制御メッセージ、実行上限の検証。
- `app/executions.py`：ExecutionStore。試験実行、採番、排他、STOP、許可更新、終端ACK。
- `tests/`：接続・認証・実行管理のAPI試験。

現在の表は `robots` と `test_executions`。
未完了実行を機体ごとに1つへ制限する部分一意索引を持つ。
本番のProgram / Reservation / Session管理や参加者認証はPhase 3以降に追加する。

## ESP32

共通ファームウェアをPlatformIO / Arduino / C++17でビルドする。
個体ごとのWi-Fi・トークン・接続先はgit対象外のrobot_config.hに置き、変更後は再書き込みする。

- `main.cpp`：停止出力を初期化し、実行環境を起動する。
- `robot_settings.h`：秘密設定の選択と走行ゲート。既存ヘッダーで未定義なら走行無効。
- `robot_runtime.cpp`：core 0のnetworkTaskとcore 1のmotorTask、キューと共有スナップショット。
- `control_message.cpp`：状態報告を構築し、実行IDなどの文字列をJSONへコピーして保持する。
- `program_parser.cpp`：JSON命令とキャリブレーションを検証し、固定長の命令列へ変換する。
- `executor.cpp`：Arduinoに依存しない時間制御・許可期限・状態遷移。再実行を防ぐJournalを使う。
- `motor_control.cpp`：GPIO25/26/27/14。反転補正をここへ一元化し、方向切替前に停止出力を挟む。
- `test/executor_test.cpp`：実行・解析コードをMac上の仮の出力・保存先で検証する。

[FreeRTOSのcore指定タスク](https://docs.espressif.com/projects/esp-idf/en/v4.4/esp32/api-reference/system/freertos.html)
でHTTPとGPIOの処理を分離する。
[Preferences API](https://docs.espressif.com/projects/arduino-esp32/en/latest/api/preferences.html)で
消費済み実行番号をNVSへ保存し、保存に失敗した場合は走行を開始しない。

## 実行と停止

新ファームウェアは登録後にprotocolVersion=2の制御通信を送り、命令配信・状態報告・許可更新を統合する。
Phase 1用Heartbeatは互換として残すが、走行受付の接続確認には使わない。

運営者の試験要求をQUEUEDとして保存し、1回だけSTARTを配信してDISPATCHEDにする。
RUNNING報告で許可を更新し、COMPLETED / STOPPED / ERRORを保存してACKを返す。
STOP要求や再起動ではSTOP_REQUESTEDにし、停止報告までは次を割り当てない。
未配信のQUEUEDだけは、その場で取消してSTOPPEDにできる。

許可期限は機体の要求送信開始時刻から1500ms。失効後の応答や通信復旧で再開しない。
通信失敗の世代番号が変わると古いキューフレームを破棄し、実行タスクで停止する。
NVS保存中にも期限を過ぎ得るため、GPIO出力の直前にも期限と失敗世代を確認する。
モーター制御は非ブロッキング、1命令最大5秒、全体最大30秒。
タイマーの桁あふれは32bit差分で扱う。

サーバーと機体の両方で走行を明示的に有効化する。既存設定は既定で無効のまま。
サーバーで受付時に個体別キャリブレーションを固定し、機体側でも数値と時間上限を検証する。
GPIO方向の補正と命令の前後・左右を二重に反転しない。

接続一覧の3秒OFFLINE判定は表示のためで、実機の停止確認や位置判定ではない。
実行タスクの2ms周期と1.5秒の許可期限は設計値であり、実機の停止遅延は別途測定する。
物理電源スイッチを残し、モーター拘束をセンサーなしで確実に検出できるとは仮定しない。

## 検証と今後

API試験・ホスト上のファームウェア試験・ESP32向けビルドで検証する。
実機確認の完了報告と再試験の手順は [Phase 2の試験記録](phase-2-verification.md) に保持する。
本番用の予約、編集プログラムの保存、AST・REPEAT展開、権限の細分化は後続フェーズ。
