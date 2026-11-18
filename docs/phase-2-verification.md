# Phase 2 実行エンジンの確認

Phase 2は実装・自動試験を完了し、2026年11月19日に利用者から実機確認完了の報告を受けた。
停止遅延や走行距離・角度の定量的な測定値は個別に記録していない。
Phase 1の完了記録は [別の試験記録](phase-1-verification.md) に保持する。

## 起動と有効化

1. まずモーター電源を切る。既存サーバーを終了し、READMEのコマンドでPhase 2版を起動する。
   既存のPhase 1 DBには列と `test_executions` 表を自動追加する。削除・初期化は不要。
2. `config/local.json` の試験する個体に `motionEnabled: true` と、実測用の `calibration` を設定する。
   雛形の値は走行試験の暫定値。設定後はサーバーを再起動する。
3. `firmware/include/robot_config.h` に `#define ROBOT_MOTION_ENABLED true` を追加する。
   未定義またはfalseの場合、命令を受信しても走行せず `MOTION_DISABLED` を報告する。
4. サーバーと個体のID・トークン・接続先を一致させ、ビルドして対象機体へ書き込む。
   シリアルモニターは書き込み前に閉じる。
5. `/docs` でstationTokenをAuthorizeし、`GET /api/robots` で `agentProtocol: 2`、
   `motionEnabled: true`、`status: IDLE`、`executionId: null` を確認する。
   `motionEnabled` はサーバーの設定値で、ESP32のビルド設定の値ではない。
6. 車輪を浮かせてモーター電源を入れる。物理電源スイッチをすぐ切れる状態にする。

## 命令送信

`POST /api/robots/robot-01/test-executions` へ次を送る。
requestIdは新しく試験を始めるたびに生成する。同じIDの再送は同じ実行を返し、もう一度動かさない。

```sh
python3 -c 'import uuid; print(uuid.uuid4())'
```

最初は前進1マスだけを送る。後退と左右の方向は別々に確認する。

```json
{
  "requestId": "ここを生成したUUIDに置き換える",
  "commands": [{"type": "MOVE", "direction": "FORWARD", "steps": 1}]
}
```

応答の `executionId` を使い、`GET /api/test-executions/{execution_id}` で経過を見る。
`QUEUED → DISPATCHED → RUNNING → COMPLETED` が通常経路。
短い命令はRUNNINGの報告より先にCOMPLETEDになることがある。
COMPLETEDは命令列の終了であり、物理的なゴール到達や正しい位置の判定ではない。

方向が正しいことを確認してから、床面で1マスの時間と90度旋回時間を実測する。
前後を別の時間で調整できる。反転はinvertLeft / invertRightだけで補正し、命令の意味を逆転させない。
調整後は設定JSONを編集してサーバーを再起動する。キャリブレーションは受付時に実行へ固定される。

順次実行の例：

```json
{
  "requestId": "ここを別のUUIDに置き換える",
  "commands": [
    {"type": "MOVE", "direction": "FORWARD", "steps": 1},
    {"type": "WAIT", "durationMs": 300},
    {"type": "TURN", "direction": "RIGHT"},
    {"type": "MOVE", "direction": "BACKWARD", "steps": 1}
  ]
}
```

## 必須の実機試験

- 起動・未設定・認証失敗で勝手に動かない。
- MOVEの前後、TURNの左右、WAITの停止を確認する。
- 命令列を順番に実行し、各命令の時間で出力が止まる。
- `POST /api/test-executions/{execution_id}/stop` でSTOPPEDへ進む。
  STOP_REQUESTEDは要求しただけの状態であり、停止確認済みとは扱わない。
- 走行中にWi-Fiやサーバーを切る。最後の許可要求の送信開始から1.5秒を目標上限に停止することを測る。
  HTTPをわざと遅延させても、通信タスクと別の実行タスクで停止期限を監視できることを確認する。
- 通信を戻しても旧実行を再開しない。停止の報告・ACKを経て、別requestIdの新しい要求でだけ次を実行する。
- ESP32とサーバーそれぞれを走行中に再起動し、旧実行が再配信されないことを確認する。
- 同じrequestIdの再送、実行中の別要求、未知命令、上限超過が二重実行・上書きにならない。
- 停止を確認する前に同じ機体へ別の実行を割り当てられない。

実行タスクは2ms周期を目標とするが、FreeRTOSのスケジュール・GPIO・実機の惰性を含む
停止遅延の実測保証はまだない。通信の3秒OFFLINE表示は停止時間の保証とは別である。
停電・CPU全体の故障やモーター拘束をソフトウェアだけで検出する設計ではない。

## 自動検証

```sh
cd apps/server
uv run --locked pytest -q
uv run --locked ruff check .
uv run --locked ruff format --check .
```

以下はリポジトリルートで実行する。初回はPlatformIOのビルドでArduinoJsonを取得する。

```sh
apps/server/.venv/bin/pio run --project-dir firmware
bash scripts/test_firmware.sh
python3 scripts/build_system_map.py --check
```

ファームウェアのホスト試験は、実機用と同じ実行・解析コードを仮のモーターと永続化で動かす。
実APIが生成したSTART応答も同じC++パーサーへ渡し、JSONの項目名と型の契約を検証する。
時間境界、タイマーの桁あふれ、順次実行、通信失敗、STOP、期限切れ、重複番号、再起動、
保存失敗、不正JSONを確認する。AddressSanitizerとUndefinedBehaviorSanitizerも使う。
Wi-Fi・GPIO・NVSの実機挙動は別途確認する。

## 実行番号の保持

SQLiteの `execution_sequence` はAUTOINCREMENTで増え続ける。
ESP32はNVSの `robot-executor / consumed` に最大の消費済み番号を保存し、それ以下は拒否する。
起動後も番号を保持し、消費済みの動作を再実行しない。

DBを削除・古いバックアップに戻すと番号が巻き戻り、ESP32が新しい実行も拒否し得る。
この場合、自動でNVSを消したり番号を再利用したりしない。
履歴と個体の対応を確認して、サーバーの採番が消費済み番号より先へ進むよう運営者が復旧する。
Phase 2にはNVSリセットのAPIや管理画面は含めない。

## 2026年11月17日の実装検証

- API試験46件成功。Phase 1互換17件、Phase 2の29件を含む。
- ホスト上の実行・解析試験成功。AddressSanitizer / UndefinedBehaviorSanitizerで検証。
- ESP32向けC++17ビルド成功。
- Phase 2の実機書き込み・走行・停止遅延測定は未実施。

## 2026年11月19日 状態報告の修正

試験受付後の状態報告が422で拒否される報告を受け、ArduinoJson 7.4.2が
const char配列をコピーせず参照保持することを再現した。
0.2.1-phase2では状態報告をcontrol_message.cppへ分け、実行IDをJsonStringでコピーして保持する。
サーバーは422を拒否した項目名と検証の種類を記録し、入力値・資格情報はログへ出さない。

C++側が生成するIDLE・RUNNING・COMPLETED・STOPPED・ERRORの全報告を
PythonのControlRequestへ通す契約試験を追加した。API試験は47件成功。
修正の実機反映には再ビルドと書き込みが必要。サーバーの診断ログ追加には再起動が必要。
停止待ちの旧要求は停止報告・ACKを待ち、新しい試験では別のrequestIdを使う。

## 2026年11月19日 実機確認完了

利用者から「Phase 2が問題ないことを確認できた」との報告を受け、Phase 2を完了とする。
確認過程では状態報告の422を修正し、前後方向の反転設定と左旋回時間の調整を案内した。
実行ID bd435372-ee6d-4c19-b30d-d4f369f83565では3命令のCOMPLETED報告を受信した。
この報告だけで実機の移動を確認したとは扱わず、その後の利用者による実機確認完了を根拠とする。

項目別の測定記録、停止までの時間、最終的な走行距離と旋回角度は未記録。
機体固有の調整値はconfig/local.jsonで管理し、暫定値の雛形へ一律には反映しない。
