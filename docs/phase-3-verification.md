# Phase 3 保存・予約・実行の確認

Phase 2は利用者による実機確認済み。Phase 3はAPI自動試験済みで、2026年11月20日に利用者が予約から実行完了までの実機確認を完了した。
以下は再確認の手順。追加の権限・競合・期限・異常系は自動試験で検証しており、全項目の実機実施を示すものではない。
Phase 2のファームウェア（0.2.1-phase2、protocolVersion=2）をそのまま使う。再書き込みは不要。
Blockly画面はまだないため、Swaggerで参加者と運営者の操作を切り替える。

## 実機確認の記録（2026年11月20日）

- 参加者のプログラム保存・更新、運営者の機体予約、保存版を指定した通常実行を確認した。
- 確認用の命令は右旋回、WAIT 5000ms、MOVE FORWARD 5ステップ、WAIT 5000ms、左旋回、WAIT 200ms。
- 配線の差し直し後、利用者が実機動作の成功を報告した。APIでは6命令のCOMPLETED、errorCode=null、予約FINISHEDを確認した。
- 走行調整値と個体トークンは端末固有の設定に保持し、共有する雛形へ転記しない。
- 旋回角度・走行距離・停止遅延の定量測定値は未記録。配線不調の原因を部品故障と確定したものではない。

## 起動と準備

既存のconfig/local.jsonとDB、ESP32の設定はそのまま保持する。雛形をコピーし直さない。
起動中のロボット用サーバーを終了し、リポジトリルートから次で起動する。
他のアプリが使うサーバーは終了しない。

```sh
cd apps/server
uv run --locked uvicorn app.main:app --host 0.0.0.0 --port 8000
```

端末から `http://192.168.179.45:8000/docs` を開く（MacのLANアドレス変更時は置き換える）。
`GET /api/health` がphase=3を返すことを確認する。別システムが出る場合はlocalhostではなくLANアドレスを使う。
初回起動で旧test_executions表はexecutions表へ移行する。履歴と実行番号を引き継ぎ、DBやNVSの削除はしない。

Authorizeにconfig/local.jsonのstationTokenを入力する。GET /api/robotsでrobot-01の
availability=IDLE、available=true、executionId=nullを確認する。古い実行のSTOPPINGが残る場合は
機体が再登録・停止報告するまで待つ。モーター電源もONにし、走行スペースを確保する。

## 参加者を発行して保存

1. stationTokenで `POST /api/sessions` に `{"label":"参加者A"}` を送る。
   応答のsessionIdとsessionTokenを控える。sessionTokenは発行時のみ表示される。
   これは機体の登録で返る通信sessionIdとは別。
2. SwaggerのAuthorizeでLogoutして、発行したsessionTokenへ切り替える。
3. `POST /api/programs` に次を送る。

```json
{
  "requestId": "c5437249-c2b2-4c90-a2de-f9961ee4ee82",
  "name": "前進して左へ",
  "blocklyWorkspace": null,
  "ast": {
    "version": 1,
    "commands": [
      {"type": "MOVE", "direction": "FORWARD", "steps": 1},
      {"type": "TURN", "direction": "LEFT"},
      {"type": "WAIT", "durationMs": 200}
    ]
  }
}
```

応答のprogramId、version=1、expandedCommandCount=3を確認する。
GET /api/programsと単体GETで同じ内容を取得できることを確認する。
新しいプログラムの作成時はrequestIdを新しいUUIDにする。同一要求の再送時だけ同じ値を使う。

## 予約して実行

1. stationTokenへ戻す。
2. `POST /api/robots/robot-01/reservations` を実行する。

```json
{
  "requestId": "24711219-7c89-45b2-933f-03638d17ff0f",
  "sessionId": "ここを参加者AのsessionIdへ置き換える"
}
```

status=RESERVEDとreservationIdを確認する。GET /api/robotsはavailability=RESERVED、available=falseとなる。
この後、既定60秒以内に次を送る。時間が足りない場合はPOST /api/reservations/{reservation_id}/renewへ
同じ参加者sessionIdを送って明示的に延長する。期限切れなら新しいrequestIdで予約し直す。

```json
{
  "requestId": "c2e6a44e-05f4-43e7-b6bd-dfbca1fc0fce",
  "programId": "ここを保存したprogramIdへ置き換える",
  "programVersion": 1,
  "reservationId": "ここを予約のreservationIdへ置き換える"
}
```

3. 上記を `POST /api/executions` へ送る。QUEUEDとexecutionIdが返る。
4. `GET /api/executions/{execution_id}` でCOMPLETED、currentCommandIndex=3、errorCode=nullを確認する。
   実機の前進・左旋回も確認する。COMPLETEDだけでは配線や物理走行の成功は判定できない。
5. 予約の単体GETでFINISHED、機体がIDLEへ戻った後にavailable=trueとなることを確認する。
6. 参加者AのsessionTokenに切り替え、自分の実行履歴をGET /api/executionsで取得できることを確認する。

新たに走らせる時は予約と実行のrequestIdをそれぞれ新しいUUIDにする。
同じ実行要求を再送すると、完了済みも含め以前の実行が返り、再走行しない。
stationTokenのGET /api/robot-events?robot_id=robot-01で予約・実行状態の遷移も確認できる。

## 追加の確認

- **保存の更新**：参加者AでPUT /api/programs/{program_id}へ、保存内容とexpectedVersion=1を送る。
  requestIdは含めない。version=2となり、同じexpectedVersion=1での更新は409。
  実行時も新しいprogramVersion=2が必要。受付済み実行は後の編集では変わらない。
- **REPEAT**：commandsに `{"type":"REPEAT","count":2,"body":[{"type":"WAIT","durationMs":200}]}` を保存する。
  expandedCommandCount=2、実行後のcurrentCommandIndex=2を確認する。
- **二重予約**：RESERVED中に別requestIdで同じ機体を予約し、409となることを確認する。
  同じ機体への試験APIも409。同じ予約要求の再送は同じ予約を返すだけで延長しない。
- **期限切れ**：未使用RESERVEDで60秒待ち、予約GETがEXPIREDになることを確認する。
  その予約での実行は409。実行中IN_USEは期限を過ぎても解放されない。
- **STOP**：WAIT 5000msを含むプログラムで実行し、所有者のPOST /api/executions/{execution_id}/stopを送る。
  STOP_REQUESTED中は再予約できず、STOPPED確定後に予約FINISHEDとなる。未配信なら即STOPPED。
- **所有者**：参加者Bを発行し、BのトークンではAのプログラム・履歴単体GET・STOPが404となることを確認する。
  BのプログラムとAの予約を組み合わせた実行も409。
- **再起動**：実行中にロボット用サーバーを終了する。機体が通信異常で停止し、再起動後は
  停止確認まで占有が残ることを確認する。保存プログラムと履歴が維持され、自動再走行しないことを確認する。

予約TTLはreservationTtlSeconds（既定60秒）、参加者TTLはparticipantSessionTtlSeconds（既定28800秒）。
設定変更はサーバー再起動で反映する。参加者の期限切れ後は新しい操作が401となる。
運営者がDELETE /api/sessions/{session_id}で明示的に失効すると、未使用予約は失効し、進行中実行へ停止を要求する。
