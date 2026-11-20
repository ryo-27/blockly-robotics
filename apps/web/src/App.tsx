import { useCallback, useEffect, useRef, useState } from "react";
import {
  Bot,
  Play,
  Square,
  Settings,
  Blocks,
  Check,
  RotateCcw,
  X,
  WifiOff,
  ShieldCheck,
} from "lucide-react";
import { api, ApiError, explain, readSession } from "./api";
import { needsQuest } from "./blocks";
import { Editor, type Draft } from "./Editor";
import { ManagementPanel, type ManagementTab } from "./ManagementPanel";
import {
  captureInstructions,
  prepareSend,
  sendInstructions,
  type InstructionSnapshot,
  type SendIntent,
} from "./station";
import {
  active,
  type Execution,
  type Quest,
  type Robot,
  type WorkspaceState,
} from "./types";
const statusText: Record<string, string> = {
  IDLE: "送信できます",
  OFFLINE: "接続されていません",
  UNAVAILABLE: "設定を確認してください",
  RESERVED: "使用中です",
  READY: "送信・準備中",
  RUNNING: "動いています",
  STOPPING: "停止を確認しています",
};
const resultText: Record<string, string> = {
  QUEUED: "指示を受け付けました",
  DISPATCHED: "機体に送りました",
  RUNNING: "実行中",
  STOP_REQUESTED: "停止を確認しています",
  COMPLETED: "実行が終わりました",
  STOPPED: "停止しました",
  ERROR: "機体を確認してください",
};
function initialWorkspace(): WorkspaceState | null {
  const oldRobot = readSession("selectedRobot", "");
  return readSession(
    "workspaceDraft",
    oldRobot ? readSession(`instructions:${oldRobot}`, null) : null,
  );
}
function App() {
  const [station, setStation] = useState(() => readSession("stationToken", ""));
  const [quests, setQuests] = useState<Quest[]>([]),
    [chosenQuest, setChosenQuest] = useState("");
  const [robots, setRobots] = useState<Robot[]>([]),
    [draft, setDraft] = useState<Draft | null>(null),
    [initial, setInitial] = useState<WorkspaceState | null>(initialWorkspace),
    [editorKey, setEditorKey] = useState(0),
    [advanced, setAdvanced] = useState(true);
  const [intents, setIntentsState] = useState<Record<string, SendIntent>>(() =>
      readSession("stationIntents", {}),
    ),
    [results, setResultsState] = useState<Record<string, Execution>>(() =>
      readSession("stationResults", {}),
    );
  const intentsRef = useRef(intents),
    resultsRef = useRef(results);
  intentsRef.current = intents;
  resultsRef.current = results;
  const [error, setError] = useState(""),
    [network, setNetwork] = useState(false),
    [busy, setBusy] = useState(false),
    [halting, setHalting] = useState(false);
  const [panel, setPanel] = useState<"manage" | "send" | null>(null),
    [managementTab, setManagementTab] = useState<ManagementTab>("robots"),
    [tokenInput, setTokenInput] = useState("");
  const [confirmation, setConfirmation] = useState<InstructionSnapshot | null>(
      null,
    ),
    [chosen, setChosen] = useState(""),
    [lastRobot, setLastRobot] = useState("");
  const sendLock = useRef(false),
    haltLock = useRef(false);
  const setIntent = useCallback((robotId: string, value: SendIntent | null) => {
    const next = { ...intentsRef.current };
    if (value) next[robotId] = value;
    else delete next[robotId];
    intentsRef.current = next;
    setIntentsState(next);
    sessionStorage.setItem("stationIntents", JSON.stringify(next));
  }, []);
  const setResult = useCallback(
    (value: Execution) => {
      const next = { ...resultsRef.current, [value.robotId]: value };
      resultsRef.current = next;
      setResultsState(next);
      sessionStorage.setItem("stationResults", JSON.stringify(next));
      if (
        !active(value.status) &&
        intentsRef.current[value.robotId]?.executionId === value.executionId
      )
        setIntent(value.robotId, null);
    },
    [setIntent],
  );
  const refresh = useCallback(async () => {
    if (!station) return;
    try {
      setRobots(await api<Robot[]>("/robots", station));
      const ids = [
        ...new Set([
          ...Object.values(intentsRef.current).flatMap((x) =>
            x.executionId ? [x.executionId] : [],
          ),
          ...Object.values(resultsRef.current)
            .filter((x) => active(x.status))
            .map((x) => x.executionId),
        ]),
      ];
      await Promise.all(
        ids.map(async (id) =>
          setResult(await api<Execution>(`/executions/${id}`, station)),
        ),
      );
      setNetwork(false);
    } catch {
      setNetwork(true);
    }
  }, [station, setResult]);
  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), 1000);
    return () => clearInterval(timer);
  }, [refresh]);
  useEffect(() => {
    if (!panel) return;
    const escape = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !sendLock.current) setPanel(null);
    };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [panel]);
  const updateDraft = useCallback((value: Draft) => {
    setDraft(value);
    sessionStorage.setItem("workspaceDraft", JSON.stringify(value.workspace));
  }, []);
  const openDestination = () => {
    setError("");
    if (!draft?.ast) return;
    if (!station) {
      setManagementTab("connection");
      setPanel("manage");
      return;
    }
    setConfirmation(captureInstructions(draft.ast, draft.blockIds));
    setChosen("");
    setChosenQuest("");
    void api<Quest[]>("/quests", station)
      .then(setQuests)
      .catch((e) => setError(explain(e)));
    setPanel("send");
  };
  const run = async (robotId: string, retry = false) => {
    if (sendLock.current) return;
    sendLock.current = true;
    setBusy(true);
    setError("");
    try {
      if (!station) throw new Error("管理から端末の接続を設定してください。");
      let intent = intentsRef.current[robotId];
      if (retry) {
        if (!intent) throw new Error("未確認の送信はありません。");
      } else {
        if (intent)
          throw new Error("前の送信結果を管理画面で確認してください。");
        if (
          !confirmation ||
          network ||
          !robots.find((r) => r.id === robotId)?.available
        )
          throw new Error("送信できる機体を選んでください。");
        const quest = quests.find((q) => q.id === chosenQuest);
        if (needsQuest(confirmation.ast) && !quest)
          throw new Error("ifを使う指示ではクエストを選択してください。");
        intent = prepareSend(
          robotId,
          confirmation.ast,
          confirmation.blockIds,
          quest,
        );
        setIntent(robotId, intent);
      }
      if (!intent) return;
      try {
        const execution = await sendInstructions(station, intent);
        setIntent(robotId, { ...intent, executionId: execution.executionId });
        setResult(execution);
        setLastRobot(robotId);
        setPanel(null);
        await refresh();
      } catch (e) {
        if (e instanceof ApiError && e.status >= 400 && e.status < 500)
          setIntent(robotId, null);
        throw e;
      }
    } catch (e) {
      setError(explain(e));
    } finally {
      sendLock.current = false;
      setBusy(false);
    }
  };
  const halt = async (ids: string[]) => {
    if (haltLock.current) return;
    haltLock.current = true;
    setHalting(true);
    setError("");
    try {
      const outcomes = await Promise.allSettled(
        [...new Set(ids)].map((id) =>
          api<Execution>(`/executions/${id}/stop`, station, "POST"),
        ),
      );
      outcomes.forEach((r) => {
        if (r.status === "fulfilled") setResult(r.value);
      });
      await refresh();
      if (outcomes.some((r) => r.status === "rejected"))
        throw new Error(
          "停止要求が届いていない機体があります。モーターの電源スイッチで止めてください。",
        );
    } catch (e) {
      setError(explain(e));
    } finally {
      haltLock.current = false;
      setHalting(false);
    }
  };
  const connect = async () => {
    if (sendLock.current) return;
    sendLock.current = true;
    setBusy(true);
    setError("");
    try {
      const token = tokenInput.trim();
      await api("/station/status", token);
      setStation(token);
      sessionStorage.setItem("stationToken", JSON.stringify(token));
      setTokenInput("");
      setNetwork(false);
      setPanel(null);
    } catch (e) {
      setError(explain(e));
    } finally {
      sendLock.current = false;
      setBusy(false);
    }
  };
  const reset = () => {
    if (!confirm("指示をつくりなおしますか？")) return;
    setInitial(null);
    setDraft(null);
    setEditorKey((k) => k + 1);
    setError("");
  };
  const liveRobots = robots.filter((r) => r.executionId),
    pending = Object.values(intents).filter((i) => !i.executionId),
    last = results[lastRobot];
  const lastIntent = intents[lastRobot];
  const highlight =
    last?.status === "RUNNING" &&
    lastIntent?.executionId === last.executionId &&
    JSON.stringify(draft?.ast) === JSON.stringify(lastIntent.ast)
      ? lastIntent.blockIds[
          lastIntent.blockIndexVersion === 2
            ? (last.commandBlockIndices?.[last.currentCommandIndex] ??
              last.currentCommandIndex)
            : last.currentCommandIndex
        ] || null
      : null;
  const robot = robots.find((r) => r.id === chosen);
  return (
    <div className="workstation">
      <main className="workstation-grid" inert={panel !== null}>
        <section className="workspace-panel">
          <div className="workspace-toolbar">
            <h1>
              <Blocks size={19} />
              指示をつくる
            </h1>
            <div>
              <label className="advanced-toggle">
                <input
                  type="checkbox"
                  checked={advanced}
                  onChange={(e) => setAdvanced(e.target.checked)}
                />
                応用ブロック
              </label>
              <button
                className="icon-button"
                title="指示をつくりなおす"
                aria-label="指示をつくりなおす"
                onClick={reset}
              >
                <RotateCcw size={18} />
              </button>
              <span className="toolbar-divider" aria-hidden="true" />
              <button
                className="manage-button"
                aria-label="管理を開く"
                title="管理"
                onClick={() => {
                  setError("");
                  setManagementTab(station ? "robots" : "connection");
                  setPanel("manage");
                }}
              >
                <Settings size={19} />
              </button>
            </div>
          </div>
          <div className="editor-surface">
            <Editor
              key={editorKey}
              initial={initial}
              advanced={advanced}
              onChange={updateDraft}
              highlight={highlight}
            />
          </div>
          <div className="workspace-actions">
            <div className="run-feedback" aria-live="polite">
              {network && (
                <p className="connection-warning">
                  <WifiOff size={16} />
                  サーバーとの接続を確認してください
                </p>
              )}
              {pending.length > 0 && (
                <button
                  className="pending-alert"
                  onClick={() => {
                    setManagementTab("robots");
                    setPanel("manage");
                  }}
                >
                  未確認の送信があります。管理で確認
                </button>
              )}
              {last && (
                <p
                  className={last.status === "COMPLETED" ? "done-message" : ""}
                >
                  {last.status === "COMPLETED" ? (
                    <Check size={17} />
                  ) : (
                    <Bot size={17} />
                  )}
                  <span>
                    {robots.find((r) => r.id === last.robotId)?.displayName ||
                      last.robotId}
                    <small>
                      {resultText[last.status] || last.status}
                      {last.questCleared === true
                        ? " · クエストクリア（マップ上）"
                        : last.questCleared === false
                          ? " · ゴール未到達（マップ上）"
                          : ""}
                      {last.errorCode ? ` · ${last.errorCode}` : ""}
                    </small>
                  </span>
                </p>
              )}
            </div>
            <div className="main-actions">
              <button
                className="primary send-select"
                disabled={!draft?.ast || busy}
                onClick={openDestination}
              >
                <Play size={20} fill="currentColor" />
                ロボットを選んで送る
              </button>
              <button
                className="stop-all"
                aria-label="動いているすべての機体を停止"
                title="動いているすべての機体を停止"
                disabled={!liveRobots.length || halting}
                onClick={() => void halt(liveRobots.map((r) => r.executionId!))}
              >
                <Square size={17} fill="currentColor" />
                停止
              </button>
            </div>
          </div>
        </section>
      </main>
      {error && !panel && (
        <div className="toast-error" role="alert">
          {error}
          <button aria-label="エラーを閉じる" onClick={() => setError("")}>
            <X size={18} />
          </button>
        </div>
      )}
      {panel === "manage" && (
        <ManagementPanel
          tab={managementTab}
          setTab={setManagementTab}
          station={station}
          robots={robots}
          statusText={statusText}
          halting={halting}
          busy={busy}
          tokenInput={tokenInput}
          setTokenInput={setTokenInput}
          onConnect={() => void connect()}
          onDisconnect={() => {
            sessionStorage.removeItem("stationToken");
            setStation("");
            setRobots([]);
          }}
          onRefresh={() => void refresh()}
          onStop={(ids) => void halt(ids)}
          onClose={() => setPanel(null)}
          pending={pending}
          onRetry={(id) => void run(id, true)}
          error={error}
        />
      )}
      {panel === "send" && confirmation && (
        <div className="overlay">
          <section
            className="destination-panel"
            role="dialog"
            aria-modal="true"
            aria-labelledby="destination-title"
          >
            <div className="dialog-heading">
              <div>
                <span>指示ができたら</span>
                <h2 id="destination-title">どのロボットに送る？</h2>
              </div>
              <button
                className="icon-button"
                aria-label="機体選択を閉じる"
                disabled={busy}
                onClick={() => setPanel(null)}
              >
                <X size={23} />
              </button>
            </div>
            <p className="destination-note">
              送信できる機体を選んでください。ここで選ぶまで、指示は送られません。
            </p>
            <div className="destination-robots">
              {robots.map((r, i) => (
                <button
                  key={r.id}
                  disabled={!r.available || !!intents[r.id] || network || busy}
                  aria-pressed={chosen === r.id}
                  className={`destination-robot robot-${i % 3} ${chosen === r.id ? "selected" : ""}`}
                  onClick={() => setChosen(r.id)}
                >
                  <span className="robot-avatar">
                    <Bot size={31} />
                  </span>
                  <span>
                    <strong>{r.displayName}</strong>
                    <small>
                      {intents[r.id] && !intents[r.id].executionId
                        ? "前の送信結果が未確認です"
                        : statusText[r.availability] || r.availability}
                    </small>
                  </span>
                  <span className="choice">
                    {chosen === r.id && <Check size={17} />}
                  </span>
                </button>
              ))}
              {!robots.length && (
                <p className="empty">登録された機体を確認しています。</p>
              )}
            </div>
            <label className="quest-choice">
              <span>
                クエスト{" "}
                <small>
                  {confirmation && needsQuest(confirmation.ast)
                    ? "if使用時は必須"
                    : "任意"}
                </small>
              </span>
              <select
                aria-label="実行するクエスト"
                value={chosenQuest}
                disabled={busy}
                onChange={(e) => setChosenQuest(e.target.value)}
              >
                <option value="">クエストなし</option>
                {quests.map((q) => (
                  <option key={q.id} value={q.id}>
                    {q.id} · {q.title}
                  </option>
                ))}
              </select>
            </label>
            <p className="safety-hint">
              <ShieldCheck size={18} />
              ロボットの周りに十分なスペースがありますか？
            </p>
            {error && (
              <p className="inline-error" role="alert">
                {error}
              </p>
            )}
            <button
              className="primary confirm-send"
              disabled={
                !robot?.available ||
                !!intents[chosen] ||
                network ||
                busy ||
                (!!confirmation &&
                  needsQuest(confirmation.ast) &&
                  !quests.some((q) => q.id === chosenQuest))
              }
              onClick={() => void run(chosen)}
            >
              <Play size={20} fill="currentColor" />
              {busy
                ? "送信しています…"
                : robot
                  ? `${robot.displayName}へ送って動かす`
                  : "機体を選んでください"}
            </button>
          </section>
        </div>
      )}
    </div>
  );
}
export default App;
