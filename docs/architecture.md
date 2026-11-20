# ロボット教室の実装構成

Phase 1は実機確認済み。Phase 2は実行エンジンと運営者用の試験APIを実装し、2026年11月19日に利用者が実機確認を完了した。
Phase 3は参加者・保存・予約・通常実行を実装し、自動試験済み。2026年11月20日に利用者が予約から実行完了までの実機確認を完了した。
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
- `app/executions.py`：ExecutionStore。試験・通常実行共通の採番、排他、STOP、許可更新、終端ACKとイベント。
- `app/workshop_models.py`：参加者・Program AST・予約・通常実行の型、REPEAT展開とサイズ上限。
- `app/workshop.py`：WorkshopStore。所有者認証、保存のバージョン、予約期限、実行スナップショット。
- `app/workshop_api.py`：参加者・プログラム・予約・実行履歴・イベントのHTTP入口。
- `tests/`：接続・認証・実行管理のAPI試験。

## 保存と参加者の権限

現在の表は `robots`、`participant_sessions`、`programs`、`reservations`、`executions`、`robot_events`。
Phase 2の `test_executions` は表名変更で `executions` に移行し、削除済み行を含むAUTOINCREMENTの最高値を保持する。
既存の実行番号を維持するため、DBやESP32のNVSを消さない。両方の表が存在する不整合は起動を拒否する。
試験と通常実行は同じ表・部分一意索引を使い、機体ごとの未完了実行を1つへ制限する。

stationTokenは参加者セッションの発行・失効、機体予約、通常実行開始、全件確認に使う。
参加者のsessionTokenは発行時だけ返し、DBにはSHA-256を保存する。有効期限は既定8時間。
参加者は自分のプログラム作成・更新・取得、自分の予約確認・解放、実行履歴とSTOPを操作できる。
ロボット用トークンではこれらを操作できない。予約と実行開始はステーションのみが行う。

ProgramはBlockly workspace（保存のみ）とversion=1のASTを保持する。REPEATは1〜20回、入れ子3段まで。
展開後64命令まで、ASTはUTF-8 JSON 32kB、workspaceは200kBまで。任意コードは実行しない。
PUTはexpectedVersionが一致した時だけversionを進め、同時編集による上書きを防ぐ。
通常実行はprogramVersionも照合し、展開済み命令と機体のcalibrationを固定する。
保存後の編集は受付済み実行へ影響しない。実行時間は機体のcalibrationで受付時に検証する。

## 予約から実行完了

RESERVEDの期限は既定60秒。更新APIで明示的に延長できる。
未使用の予約はAPI参照・変更時に失効を判定し、常駐の期限監視タスクは持たない。
開始時には所有者・プログラムの版・予約・機体の状態を同じBEGIN IMMEDIATE内で照合する。
通常実行の作成とIN_USEへの変更は一括で確定する。予約中の機体へ試験APIで割り込むこともできない。

IN_USEは時間経過や通信断で解放せず、COMPLETED / STOPPED / ERRORの確定後にFINISHEDへ変える。
未配信QUEUEDの取消しは即STOPPEDとできる。配信済みはSTOP_REQUESTEDで機体の停止報告を待つ。
参加者の明示的な失効は未使用予約を失効させ、実行中なら停止を要求する。
参加者トークンの自然失効は新しい操作を拒否するが、受付済み実行は既存の走行許可期限で管理する。
サーバー再起動後も停止待ちの占有を保持する。状態と命令位置の変化、予約の遷移はrobot_eventsへ保存する。

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
Phase 3の予約→保存プログラム実行→完了の確認は [Phase 3の手順](phase-3-verification.md) を使う。
Phase 4でBlockly UI、Phase 5で3台の実環境運用を検証する。
