# Phase 1 ロボット通信プロトコル

HTTP/JSON、protocolVersionは1。数値・時間は暫定の初期設定であり実機で確認する。
すべてのロボットAPIに `Authorization: Bearer <個体のtoken>` を付ける。
URLやログにトークンを含めない。

## 登録

`POST /api/robot-agent/register`

```json
{
  "protocolVersion": 1,
  "robotId": "robot-01",
  "bootId": "898ce134-9a2c-46ef-813d-e30a96cd11fe",
  "firmwareVersion": "0.1.0-phase1"
}
```

bootIdはESP32起動ごとに乱数で生成するUUID。再試行では同じ値を使う。
認証トークンの個体とrobotIdが一致しなければ403。未知の個体・無効なトークンは401。
登録は事前設定済み個体の接続処理であり、任意の個体を新規作成するAPIではない。

成功は200で次を返す。

```json
{
  "protocolVersion": 1,
  "sessionId": "7c7fa27f-a553-4861-9e86-e34149106408",
  "status": "IDLE",
  "pollIntervalMs": 500,
  "motorsEnabled": false
}
```

同じbootIdの登録再試行は現在のsessionIdを返す。別bootIdではセッションを交換する。
サーバー再起動はセッションを無効化し、同じbootIdでも新しいsessionIdを返す。
Phase 1の登録要求は到着順に扱う。走行の追加前に古い登録要求と実行中の再登録の扱いを詳細化する。

## Heartbeat

`POST /api/robot-agent/heartbeat`

```json
{
  "protocolVersion": 1,
  "sessionId": "7c7fa27f-a553-4861-9e86-e34149106408",
  "bootId": "898ce134-9a2c-46ef-813d-e30a96cd11fe",
  "sequence": 1,
  "status": "IDLE"
}
```

500ms間隔を基準とする。sequenceはセッションごとに1から始める整数で、送信ごとに増やす。
上限は2147483647。ファームウェアは到達時にbootIdを再生成して登録をやり直す。
サーバーは登録と同形の200応答を返す。

- 新しいsequence：接続時刻を更新する。
- 同じsequence：200を返すが接続時刻を更新しない。
- より古いsequence：409 `STALE_SEQUENCE`。
- bootIdまたはsessionId不一致：409 `REGISTER_REQUIRED`。
- 不正な型・未知フィールド・未対応バージョン・IDLE以外の状態：422。

登録とHeartbeatの更新はSQLiteの単一トランザクションで行う。
サーバーの応答を受けてから次を送る方式を採用し、同一個体の並列HTTP要求は行わない。

## 障害と接続表示

ESP32は通信失敗時に1秒、2秒、4秒、8秒、以後最大10秒の間隔で再試行し、成功時は500msに戻す。
接続・読み取りタイムアウトは各1秒。Wi-Fi自体の再接続は10秒ごとに試みる。
401・403・409を受けたらセッションを破棄して登録をやり直す。
応答のバージョン・状態・sessionId・motorsEnabledを検証する。異常時もモーターは停止を維持する。
HTTP通信全体の最悪時間を保証する設計ではなく、Phase 1の停止中に限った実装である。

サーバーの最後の正常通信から3秒未満はIDLE、3秒以上または未登録はOFFLINE。
サーバーの時計が過去へ移動した場合も安全側にOFFLINEとする。
OFFLINEは接続状態を示し、物理的な停止や位置を計測した結果ではない。

## 実行ステーション向け

`GET /api/robots` はステーション用トークンで認証し、個体一覧を返す。
各個体は `id`, `displayName`, `status`, `lastSeenAt`（UTC日時またはnull）、
`firmwareVersion`（未登録ならnull）を持つ。資格情報は返さない。

`GET /api/health` は認証なしで起動状態を返す。
`/docs` と `/openapi.json` はスキーマ参照用に公開する。ロボット一覧・登録内容は含めない。
命令取得・実行許可・STOP・予約・実行管理はこのPhaseには含まれない。
