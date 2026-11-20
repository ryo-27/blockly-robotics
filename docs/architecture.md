# ロボット教室の実装構成

Phase 1は実機確認済み。Phase 2は実行エンジンと運営者用の試験APIを実装し、2026年11月19日に利用者が実機確認を完了した。
Phase 3は参加者・保存・予約・通常実行を実装し、自動試験済み。2026年11月20日に利用者が予約から実行完了までの実機確認を完了した。
2026年11月21日に利用者から画面での単機実行確認完了の報告を受け、Phase 4の実機確認を完了した。
Phase 5は実装・模擬検証・単機確認済み。複数機での実機確認は2・3台目の完成後に行う。
要求の原本は [設計仕様書](system-design.md)、処理・設定・ファイルの対応は
[システムマップ](system-map/index.html)、通信契約は [protocol.md](protocol.md) を参照する。

## サーバー

MacBook上のFastAPIが、事前登録した個体をBearerトークンで認証する。
SQLiteには個体のトークンのSHA-256だけを保存し、設定ファイルには元の資格情報を保持する。
LAN内HTTPで運用し、単一プロセスを前提にする。React / Blocklyの画面をapps/webに実装し、ビルド後は同じFastAPIから配信する。

- `app/config.py`：個体ID・トークン、走行有効化、キャリブレーションの設定。
- `app/main.py`：認証とHTTPの入口、lifespanで設定・DBを初期化。
- `app/schemas.py`：Phase 1の互換メッセージと一覧の型。
- `app/store.py`：個体と接続の永続化、既存robots表への列追加、接続状態の算出。
- `app/execution_models.py`：MOVE / TURN / WAIT、制御メッセージ、実行上限の検証。
- `app/executions.py`：ExecutionStore。試験・通常実行共通の採番、排他、STOP、許可更新、終端ACKとイベント。
- `app/workshop_models.py`：指示AST・通常実行の型、REPEAT展開とサイズ上限。
- `app/workshop.py`：WorkshopStore。直接送信、機体の利用可否、実行スナップショット。
- `app/workshop_api.py`：直接送信・実行履歴・イベント・DB閲覧のHTTP入口。
- `tests/`：接続・認証・実行管理のAPI試験。

## 永続化と旧データの整理（2026年11月21日）

現在の表は `robots`、`executions`、`robot_events`、`sqlite_sequence`。
参加者・保存プログラム・予約のAPIを撤去し、`app/storage_migration.py`が旧3テーブルを削除する。
全実行の終端を確認してからDBを`data/backups/`に退避し、旧表・旧予約イベントを削除し、実行内の旧参照をnullにする。
実行中・停止確認待ちがあれば、DBや接続状態を変更する前に起動を拒否する。
再起動で旧表を再作成せず、バックアップも追加しない。バックアップは秘密設定と同様Git対象外。
実行履歴・requestIdとsqlite_sequenceは保持し、二重実行と消費済み番号の再利用を防ぐ。
Phase 2のtest_executionsは表名変更でexecutionsへ移行し、削除済み行を含む採番最高値を保持する。

指示のASTはversion=1、REPEATは1〜20回、入れ子3段まで。展開後64命令、AST JSONはUTF-8で32kBまで。
機体のcalibrationで時間上限を検証し、受付時に元のAST・展開命令・calibrationを固定する。
stationTokenで一覧・送信・履歴・STOP・DB閲覧を認証する。機体トークンには運営者の権限を与えない。

## 機体と指示を中心にした画面（2026年11月20日変更）

参加者の学習進行は手元のプリントで管理する。端末は指示編集・作成後の機体選択・送信・停止を担当し、
参加者の発行・切替、名前付き保存一覧、個人別の学習進捗を持たない。

- `apps/web/src/App.tsx`：共通下書き、指示の固定、送信先選択、直接送信と停止。
- `apps/web/src/ManagementPanel.tsx`：機体状態・接続・開発用DB閲覧をまとめた管理パネル。
- `app/inspection.py`：mode=roとquery_onlyによる許可済みテーブルの閲覧、資格情報の非表示、ページ上限。
- `apps/web/src/Editor.tsx`：Blocklyの生成・破棄、サイズ変更とJSON復元、実行ブロックの強調。
- `apps/web/src/blocks.ts`：日本語の独自ブロックとASTの変換・検証。
- `apps/web/src/station.ts`：機体・AST・requestIdの送信スナップショット。同じ要求で結果を再確認する。
- `apps/web/src/api.ts`：同一オリジンのBearer認証、エラーとLAN内HTTP用UUID。
- `app/workshop.py`のcreate_station_execution：参加者・保存・予約を作らず、機体の排他と受付を同じトランザクションで確定。
- `app/web.py`：ビルドがある場合の同一オリジン静的配信。APIとSwaggerを優先する。
- `tests/test_station.py`：直接送信の認証、重複防止、競合、3台独立動作、再起動を検証。

stationTokenは端末の接続設定としてsessionStorageに保持する。機体に属さない作成中の下書きと、機体に固定した未確認の送信内容を同じタブに保持する。
画面は再読込だけで指示を送らず、応答が不明な場合に同じ内容・IDで再確認する。実行中でも他の機体へ切り替えられる。
サーバーはkind=STATIONの実行へ元のAST・展開命令・calibrationを固定し、個人識別子と保存プログラム・予約への参照はnullにする。
機体の排他とSTOPは試験実行と共通。停止確認までは次の実行を割り当てない。
学習履歴は画面で扱わず、通信・停止・二重実行防止の運用記録だけをサーバーへ残す。

旧Program・Session・Reservation APIとテーブルは廃止した。
模擬エージェントは実機と分離したsim-IDだけを扱い、3台でのUI・通信試験に使う。実機の配線や負荷試験の代わりにはならない。

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
廃止したPhase 3の予約→保存プログラム実行→完了の当時の記録は [Phase 3の手順](phase-3-verification.md) を使う。
Phase 4・5の実装と模擬ブラウザ確認は済み。画面からの実機実行と3台実環境運用は
[Phase 4](phase-4-verification.md)・[Phase 5](phase-5-verification.md)の手順で確認する。

## 固定画面と開発用DB閲覧（2026年11月21日）

通常画面は100dvhのFlexレイアウトとmin-height:0で、全面のBlockly編集と下端の送信・停止を収める。
指示の自動一覧と送信前の要約、下部の説明文は表示しない。管理は中央モーダルへ集約する。
指示作成中に機体を決めず、captureInstructions()で指示を固定してから機体選択を開く。
機体選択・確認だけでは実行を作らず、機体名付きボタンの操作で初めてPOSTする。
下書きはworkspaceDraft。旧機体別下書きは最後に編集していたものだけを初回の候補として引き継ぐ。

管理パネル内のDB確認は、既存のWorkshopStore.pathを読み取り専用で参照する。
表名は固定の許可リスト、ページは既定25・最大100行、各テキストは4000文字まで。
token_hashは非表示。SQL実行や編集は提供せず、DB不在時もファイルを作らない。
DBの読取接続はStore.connection()のBEGIN IMMEDIATEを使わず、短いタイムアウトとクエリ期限を持つ。

Blocklyの初期倍率は0.95 ÷ 1.2（約79%）。中央ボタンはscrollCenter()で現在の倍率を維持する。

## クエストとif（2026年11月21日実装）

送信先ロボットは必須、クエストは任意。ifを使う場合はマップが必要なためクエスト選択を必須とする。
if条件は「前に進めるか」と「今いるマスの種類」を用意し、サーバーが開始位置・向きから順に計算する。
分岐・繰り返しを解決した既存の命令列をESP32へ送る。送信前に正誤や経路は表示しない。
岩・マップ外へ入る移動は送信を止めるが、ゴール未達でも実機で試せる。
クリア条件はマップ上で実行終了時にゴール位置へ到達していること。最後の向き・経由地点の順序・最小ブロック数は判定しない。
これは命令どおりに動いた場合の論理判定であり、センサーによる実機の到着確認ではない。

実装は`app/quests.py`、マップの正本は公開設定`config/quests.json`。教材の8マップを基にし、ゴールの向きや経由条件は取り込まない。
調査ポイントはON_CELLの条件用マーカーとして保持する。Q06は開始位置とゴールが同じで、終了位置だけを判定する。
ASTはIFのcondition・body・elseBodyを持ち、IF/REPEATの入れ子は合計3段、構文全体64ノード・32kBまで。
選ばれた分岐の展開結果は64命令・1命令5秒・全体30秒まで。送信時にサーバーが全移動経路を確認し、違反時は実行を保存しない。
各実行にquest_id・quest_version・quest_snapshot・quest_cleared・command_block_indicesを追加保存する。
元AST・展開命令・マップ・論理判定を受付時に固定し、requestIdの照合にはクエストIDと版も含める。
questClearedはCOMPLETEDのときだけ返し、停止・エラー・実行中はnull。ブラウザで命令列の予告や条件解決は行わない。
実行中の強調は、サーバーのcommandBlockIndicesとブラウザの構文順ブロックIDを対応させる。
