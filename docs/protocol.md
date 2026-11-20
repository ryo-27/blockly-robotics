# ロボット通信プロトコル

Phase 4・5版。機体との通信仕様はPhase 2から変更なし。HTTP/JSON、登録と旧HeartbeatはprotocolVersion=1、実行制御は2。
全ロボットAPIに `Authorization: Bearer <個体token>` を付ける。
運営者APIにはstationTokenを使う。参加者APIは廃止した。

## 現在の操作：機体へ指示を直接送信

端末は参加者や学習進行を管理しない。運営者のstationTokenで `GET /api/station/status` を確認し、
`POST /api/robots/{robot_id}/executions` へ機体の指示ASTを送る。参加者・Program・Reservationは不要。

```json
{"requestId":"42e30ee4-3202-4084-9186-ccfbf38685b2","ast":{"version":1,"commands":[{"type":"MOVE","direction":"FORWARD","steps":1},{"type":"TURN","direction":"LEFT"}]}}
```

応答はkind=STATION。sessionId・programId・programVersion・reservationIdはnull。
同じrequestId・機体・ASTの再送は同じ実行を返し、内容や機体が異なる再送は409 IDEMPOTENCY_CONFLICT。
元のASTはstation_astに保存し、REPEATの展開後の命令と機体のcalibrationを受付時に固定する。
ASTのサイズ・構造・命令数・時間制限は従来と同じ。
機体の排他検査と作成はBEGIN IMMEDIATE内で確定する。未完了の実行へ割り込まない。
状態取得と停止には既存の `/api/executions/{execution_id}` と `/stop` を使う。
これは端末・機体の運用記録であり、参加者の学習進捗ではない。
参加者・保存・予約APIは2026年11月21日に廃止した。

## 登録と旧版互換

`POST /api/robot-agent/register`

```json
{"protocolVersion":1,"robotId":"robot-01","bootId":"898ce134-9a2c-46ef-813d-e30a96cd11fe","firmwareVersion":"0.2.1-phase2"}
```

bootIdは起動ごとに生成するUUID。同じbootIdの再試行は同じsessionIdを返す。
別bootIdまたはサーバー再起動後は新しいsessionIdとなり、旧実行はSTOP_REQUESTEDにする。
応答はversion=1、sessionId、status=IDLE、pollIntervalMs=500、motorsEnabled=false。
この登録応答は走行許可を与えない。

Phase 1の `POST /api/robot-agent/heartbeat` はIDLEだけを受ける旧版互換として残す。
旧版のHeartbeatでは実行制御の接続確認時刻を更新せず、走行要求を受付可能にはしない。
旧版の同一sequenceは200を返すが接続期限を延ばさず、古いsequenceは409。

## 試験実行の受付

`POST /api/robots/{robot_id}/test-executions` はステーション認証を要求する。

```json
{"requestId":"8ac044d5-b600-4938-8997-06dcd654027d","commands":[{"type":"MOVE","direction":"FORWARD","steps":1},{"type":"TURN","direction":"RIGHT"},{"type":"WAIT","durationMs":200}]}
```

同じrequestId・個体・命令の再送は同じ実行を返し、内容が異なれば409。
1機体の未完了実行は1つ。受付にはサーバーのmotionEnabled=true、直近3秒未満のversion=2制御通信、
機体の報告がIDLE、未完了実行なしが必要。
未接続・旧版のみ・走行中・停止未確認は409、走行無効は403、未知の個体は404。

- MOVE：FORWARD / BACKWARD、stepsは厳密な整数1〜10。
- TURN：LEFT / RIGHT、1回で90度相当。
- WAIT：durationMsは厳密な整数1〜5000。
- 命令数1〜64、1命令最大5000ms、合計最大30000ms。
- 未知の型・フィールド、bool・小数の数値、REPEATなどは422。
- キャリブレーションから時間をサーバーで計算して検証し、ESP32でも再検証する。

`GET /api/test-executions/{execution_id}` が状態・命令位置・時間見積・エラーを返す。
`POST /api/test-executions/{execution_id}/stop` が停止を要求する。
QUEUEDは未配信なので即STOPPED、それ以外の進行中はSTOP_REQUESTEDとして停止報告を待つ。
通常操作は直接送信APIを使う。実行中の機体への試験実行は409 ROBOT_BUSY。

## 実行制御と状態報告

`POST /api/robot-agent/control` が命令の受領、状態報告、許可更新、STOP、終端ACKを兼ねる。
ESP32は登録後に250msを基準として順番に送る。旧Heartbeatとは併用しない。

```json
{"protocolVersion":2,"bootId":"898ce134-9a2c-46ef-813d-e30a96cd11fe","sessionId":"7c7fa27f-a553-4861-9e86-e34149106408","sequence":1,"status":"IDLE","currentCommandIndex":0}
```

RUNNING / COMPLETED / STOPPED / ERRORではexecutionIdを付ける。
ERRORにはerrorCodeが必須。STOPPEDは理由をerrorCodeで報告できる。
currentCommandIndexは0起点、完了後は命令数となる。
IDLEはexecutionId、errorCodeを持たない。

応答はversion=2、同じsessionIdとsequence、action、leaseMsを持つ。

- IDLE：実行なし。leaseMs=0。
- START：未配信の命令列とcalibration、executionId、executionSequenceを1回だけ渡す。leaseMs=1500。
- CONTINUE：同じ実行の許可更新。executionId、executionSequence、leaseMs=1500。
- STOP：走行許可なし。executionId、executionSequence、leaseMs=0。
- ACK：終端報告の確認。executionId、leaseMs=0。ESP32はその後IDLEへ戻る。

STARTを返した時点でDISPATCHEDとなり、同じSTARTは再配信しない。
次の報告がまだIDLEなら、配信の成否が不明なのでSTOPを送り、停止報告まで占有を保つ。
通常はDISPATCHED → RUNNING → COMPLETED、停止時はSTOP_REQUESTED → STOPPED。
機体の検証失敗やNVS保存失敗はERRORとなる。終端報告の再送はACKだけを返す。

制御通信のsequenceは1から2147483647までの厳密な整数で増やす。
同じ番号・古い番号は409 STALE_SEQUENCE、セッション不一致は409 REGISTER_REQUIRED。
誤った個体の実行報告、不正な命令位置、未知の実行は409。
入力の型や状態の組み合わせが不正なら422。
制御通信の422はサーバーログに拒否した項目名と検証の種類を出す。入力値や資格情報は記録しない。
ESP32は状態スナップショットの文字列をJSONへコピーし、送信時まで保持する。
旧登録・新制御の両方で個体認証を行い、本文のrobotIdはトークンの個体と一致させる。

## 許可期限と停止

ESP32は自分のHTTP要求の**送信開始時刻＋1500ms**を許可期限とする。
受信時刻から数え直さず、遅延応答でも期限を延ばさない。
失効後のCONTINUEを受けても停止済みの実行を再開しない。
HTTP失敗、応答不正、Wi-Fi切断は実行タスクへ失敗世代を通知し、走行中ならSTOPPEDへ固定する。

HTTPの接続と読み取りタイムアウトは各750ms。これを動作停止の仕組みには使わない。
HTTPはcore 0、実行・GPIOはcore 1の別タスクで動かし、実行タスクは2ms周期を目標とする。
STOPの通信が届かなくてもローカルの許可期限で止まる。
実機での停止遅延は未測定であり、期限＋スケジュール遅延・GPIO・惰性を含めた試験が必要。

成功時の次回通信は250ms後。失敗時は500ms、1秒、2秒、4秒、8秒、最大10秒へ伸ばす。
Wi-Fi再接続は自動再接続に加え10秒ごとに試す。401・403・409なら登録をやり直す。
再登録や通信復旧は旧実行の再開を許可しない。

## 一覧と保存

`GET /api/robots` はstatus=IDLE/OFFLINEに加え、agentProtocol、motionEnabled、
executionId、executionStatusを返す。statusは接続の表示であり実行状態とは別。
last_seenから3秒以上、通信記録なし、時計が過去へ動いた場合はOFFLINE。
走行中かどうか・停止確認はexecutionStatusで判断する。

robots表に個体設定と接続、executions表に試験・通常実行を保存する。
旧test_executions表は起動時に名前を変更して移行し、採番の最高値を保持する。
サーバー再起動時は旧セッションを無効にし、進行中実行をSTOP_REQUESTEDにする。
通信が切れたことだけでは未完了実行を解放しない。
SQLiteのトランザクションと機体ごとの未完了実行に対する一意索引で競合を防ぐ。

executionSequenceはDBのAUTOINCREMENTを使い、ESP32は最大消費済み番号をNVSへ出力前に保存する。
それ以下は再起動後も拒否する。DBの削除・巻き戻し時の扱いは [実機試験手順](phase-2-verification.md) を参照。

`GET /api/health` は認証なしでphase=5の起動情報を返す。
`/docs` と `/openapi.json` から通信型を確認できる。

## ASTの制約・実行履歴・イベント

REPEATは整数1〜20回、IFと合わせて入れ子3段。構文全体64ノード・展開後64命令まで。ASTのversionは厳密な整数1。
ASTのJSONはUTF-8で32,000バイトまで。構造は入力時、所要時間は機体が決まる受付時に検証する。
`GET /api/executions/{execution_id}`、`GET /api/executions`、`POST /api/executions/{execution_id}/stop` はstationTokenが必要。
一覧はrobot_id、limit、offsetを指定できる。停止と状態遷移は試験実行と共通で、1機体1未完了実行に制限する。
過去のWORKSHOP実行も履歴として残すが、新規作成用の `POST /api/executions` は撤去した。
旧sessionId・programId・programVersion・reservationIdはnullを返す。

`GET /api/robot-events` はstationTokenが必要。robot_id、execution_id、after_id、limit（既定100、最大500）で絞る。
eventId昇順で実行状態・命令位置の変化を返す。旧予約イベントは移行時に削除する。

`GET /api/robots` はstationTokenが必要。availabilityはOFFLINE / UNAVAILABLE / IDLE / READY / RUNNING / STOPPING。
available=trueは接続・制御・走行有効化を満たし、未完了実行がないIDLEのみ。reservationIdはnull。
READYはQUEUEDまたはDISPATCHED。通信断や停止要求だけでは占有を解放しない。
一覧取得後にも状態は変わるため、実行受付時に機体の状態と占有を再確認する。

## Phase 4・5の画面からの操作

管理パネルの接続設定にstationTokenを入力する。ブロックで指示を作ってから送信先を選ぶ。
共通の下書きはsessionStorageへ保持し、サーバーへ保存プログラムや参加者を作成しない。
画面は機体・AST・requestIdを固定して直接送信する。応答紛失後も同じ要求で結果を照合し、再読込では自動再送しない。
機体の状態画面では接続・現在の実行とSTOPを表示する。個人別の履歴・課題達成・進捗は扱わない。
機体へのprotocolVersion=2と停止制御は維持する。

## 開発用のDB閲覧

すべてstationTokenが必要。個体tokenは利用できない。

- `GET /api/station/database`：DBの存在、ファイル名、readOnly=true、許可したテーブルと件数。
- `GET /api/station/database/{table}?limit=25&offset=0`：列名・型・主キー、最新行、総件数とページ位置。
  limitは1〜100、offsetは0以上。未知の表は404、読取待ち・時間超過は503 DATABASE_BUSY。

閲覧対象はrobots、executions、robot_events、sqlite_sequence。
各token_hashは非表示。テキストは先頭4000文字までとし、行詳細はブラウザでJSONを展開表示する。
ファイルはmode=ro・PRAGMA query_only=ONで開く。更新・削除・任意SQLのエンドポイントは持たない。
DBがない場合、概要はavailable=falseを返し、内容取得は404 DATABASE_NOT_FOUND。閲覧ではファイルを作成しない。

## クエストと条件分岐

`GET /api/quests`はstationTokenが必要。id・version・titleのみを返し、マップや予測経路は返さない。
直接送信の本文にquestId・questVersionを指定できる。両方を省略すれば自由走行、IFを使う場合は両方必須。

```json
{
  "requestId": "a5d4e354-b92b-4e13-b722-eb5884e0e1fd",
  "questId": "Q01",
  "questVersion": 1,
  "ast": {"version": 1, "commands": [
    {"type": "IF", "condition": {"type": "CAN_MOVE_FORWARD"},
     "body": [{"type": "MOVE", "direction": "FORWARD", "steps": 1}],
     "elseBody": [{"type": "TURN", "direction": "RIGHT"}]}
  ]}
}
```

もう一方の条件は`{"type":"ON_CELL","cellType":"GOAL"}`。cellTypeはGOAL・CHECKPOINT・START・PATH。
前進可否は現在位置から向いている方向の隣接1マスを判定する。PATHはゴール・調査ポイント・開始位置以外の通行可能マス。
各動作の後に位置・向きを更新し、繰り返し内でも毎回条件を評価する。elseBodyは省略可能。bodyは1命令以上必要。
移動は後退と途中の各マスも検査し、岩・マップ外に入る場合は422 QUEST_PATH_BLOCKED。実行は作成しない。
選択結果が0命令なら422 NO_COMMANDS。存在しないクエストは404 QUEST_NOT_FOUND、版不一致は409 QUEST_VERSION_CONFLICT。
最終位置がゴールならクリア。向き・経由・命令数の最適性はクリア条件に含めない。未達でも送信できる。
実行応答はquestId・questVersion・questCleared・commandBlockIndicesを追加する。questClearedは完了報告後だけboolean、それ以外はnull。
消費済み実行の再送は、カタログ変更後も受付時のスナップショットを維持して同じ実行を返す。
機体へのSTARTは従来のMOVE・TURN・WAITだけで、ファームウェアの更新は不要。
