# ロボット通信プロトコル

Phase 3版。機体との通信仕様はPhase 2から変更なし。HTTP/JSON、登録と旧HeartbeatはprotocolVersion=1、実行制御は2。
全ロボットAPIに `Authorization: Bearer <個体token>` を付ける。
運営者APIにはstationToken、参加者APIには発行済みsessionTokenを使う。

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
Phase 3では別の通常実行APIを使う。予約中の機体への試験実行は409 ROBOT_RESERVED。

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

`GET /api/health` は認証なしでphase=3の起動情報を返す。
`/docs` と `/openapi.json` から通信型を確認できる。

## Phase 3：参加者・保存・予約

すべてBearer認証。stationTokenとsessionTokenはSwaggerのAuthorizeで切り替える。
参加者のsessionIdと、機体の登録で返す通信sessionIdは別の識別子。
参加者トークンは期限切れ・失効後に401。他の参加者の単体データ取得は404、権限が異なる操作は403。

- stationToken：POST /api/sessions（label）、GET /api/sessions、DELETE /api/sessions/{session_id}。
  発行応答だけsessionTokenを含む。失効すると未使用予約を失効し、進行中実行へ停止要求する。
- sessionToken：POST /api/programs、PUT /api/programs/{program_id}。作成はrequestId、更新はexpectedVersionが必須。
- いずれか：GET /api/programs と /api/programs/{program_id}。参加者は自分の保存だけを取得できる。
  一覧はlimit（既定25、最大100）とoffsetでページ指定。stationTokenだけsession_idで所有者を指定できる。

```json
{
  "requestId": "4a5dfb92-768e-4f5c-a03d-e183412ceb75",
  "name": "2回くり返す",
  "blocklyWorkspace": null,
  "ast": {"version": 1, "commands": [
    {"type": "REPEAT", "count": 2, "body": [
      {"type": "MOVE", "direction": "FORWARD", "steps": 1},
      {"type": "TURN", "direction": "LEFT"}
    ]}
  ]}
}
```

応答はprogramId、sessionId、version、expandedCommandCount、createdAt、updatedAtと保存内容。
REPEATのcountは厳密な整数1〜20、入れ子3段、展開後64命令まで。ASTのversionは厳密な整数1。
ASTのJSONは32,000バイト、workspaceは200,000バイトまで（UTF-8、保存検証用のJSON表現）。
通常命令の制限は試験APIと同じ。保存時は構造を検証し、時間上限は機体が決まる実行受付時に検証する。
更新時はrequestIdを除いてexpectedVersionを指定する。古い版は409 PROGRAM_VERSION_CONFLICT。
作成再送は同一所有者の同一requestId・内容・version=1なら同じ保存を返す。更新後の作成再送は409。

- stationToken：POST /api/robots/{robot_id}/reservations（requestId、参加者sessionId）。
- いずれか：GET /api/reservations と /api/reservations/{reservation_id}、DELETEで未使用予約を解放。
- stationToken：POST /api/reservations/{reservation_id}/renew（所有者sessionId）で未使用予約を延長。

予約応答はreservationId、robotId、sessionId、status、createdAt、expiresAt、executionId、requestId。
既定60秒、設定reservationTtlSecondsは30〜300秒。再送は同じ予約を返すだけで期限を延長しない。
RESERVEDだけが失効・解放・更新の対象。IN_USEは終端確認まで保持し、単なる期限経過では解放しない。
別要求による二重予約は409。期限の失効判定は参照・変更APIのトランザクション内で行う。

## Phase 3：通常実行・履歴・イベント

stationTokenで `POST /api/executions`。プログラム所有者と予約所有者は一致が必要。

```json
{
  "requestId": "65a9ca94-475b-436d-abda-187e48f39b27",
  "programId": "f833a20f-b8d6-4c46-86c4-25ff4bdb3c04",
  "programVersion": 1,
  "reservationId": "cc22ad01-124a-4710-b7d0-d276f18730f5"
}
```

識別子は保存・予約の実際の応答に置き換える。同じrequestId・プログラムID・版・予約IDの再送は
期限経過や完了後も同じ実行を返す。内容が異なる再送は409 IDEMPOTENCY_CONFLICT。
期限切れ予約は409 RESERVATION_EXPIRED、所有者不一致は409 PROGRAM_RESERVATION_MISMATCH。
時間超過は422、走行無効は403。いずれも実行を作らず、別の機体へ勝手に割り当てない。

通常実行応答は従来の実行状態にkind=WORKSHOP、sessionId、programId、programVersion、reservationId、startedAtを追加。
commandsとcalibrationは受付時にDBへ固定。機体へはREPEATを含まない従来のprotocolVersion=2を配信する。
`GET /api/executions/{execution_id}`、`GET /api/executions`、`POST /api/executions/{execution_id}/stop` は
stationTokenまたは所有者のsessionTokenを使う。一覧はrobot_id、limit、offsetを指定できる。
停止処理と状態遷移は試験実行と共通。通常・試験を合わせて1機体1未完了実行に制限する。

`GET /api/robot-events` はstationTokenのみ。robot_id、execution_id、after_id、limit（既定100、最大500）で絞る。
eventIdの昇順で予約・実行状態・命令位置の変化を返す。payloadには状態や理由を含め、秘密トークンは含めない。

`GET /api/robots` は両方のクライアントトークンで利用可能。
従来のstatusに加えavailability、available、reservationIdを返す。
availabilityはOFFLINE / UNAVAILABLE / IDLE / RESERVED / READY / RUNNING / STOPPING。
available=trueは、接続・制御・走行有効化を満たし、予約と未完了実行がないIDLEのみ。
READYはQUEUEDまたはDISPATCHED。接続が切れた場合は表示をOFFLINEにしても予約占有は保持する。
一覧表示後に状態が変わる場合があるため、最終的な受付可否は予約・実行APIが再確認する。
