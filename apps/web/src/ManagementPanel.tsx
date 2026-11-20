import { useEffect, useRef, useState } from "react";
import {
  Bot,
  Database,
  KeyRound,
  Radio,
  RefreshCw,
  Square,
  X,
  ChevronLeft,
  ChevronRight,
} from "lucide-react";
import { api, explain } from "./api";
import type { Robot } from "./types";
export type ManagementTab = "robots" | "connection" | "database";
type Overview = {
  available: boolean;
  fileName: string;
  readOnly: boolean;
  tables: { name: string; label: string; rowCount: number }[];
};
type Row = Record<string, string | number | null>;
type TableData = {
  table: string;
  label: string;
  rowCount: number;
  columns: {
    name: string;
    type: string;
    primaryKey: boolean;
    redacted: boolean;
  }[];
  rows: Row[];
  offset: number;
  limit: number;
  textLimit: number;
};
function cellText(value: unknown): string {
  return value === null ? "NULL" : String(value);
}
function detailedRow(row: Row) {
  return Object.fromEntries(
    Object.entries(row).map(([key, value]) => {
      if (typeof value === "string" && /^[\[{]/.test(value)) {
        try {
          return [key, JSON.parse(value)];
        } catch {
          /* 長いテキストは先頭だけを表示する。 */
        }
      }
      return [key, value];
    }),
  );
}
function DatabaseBrowser({ token }: { token: string }) {
  const [overview, setOverview] = useState<Overview | null>(null),
    [table, setTable] = useState("executions"),
    [data, setData] = useState<TableData | null>(null),
    [offset, setOffset] = useState(0),
    [revision, setRevision] = useState(0),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(false),
    [selected, setSelected] = useState<number | null>(null);
  useEffect(() => {
    let live = true;
    setError("");
    api<Overview>("/station/database", token)
      .then((value) => {
        if (live) {
          setOverview(value);
          if (!value.tables.some((t) => t.name === table))
            setTable(value.tables[0]?.name || "");
        }
      })
      .catch((e) => {
        if (live) setError(explain(e));
      });
    return () => {
      live = false;
    };
  }, [token, revision]);
  useEffect(() => {
    if (!table || !overview?.available) return;
    let live = true;
    setLoading(true);
    setSelected(null);
    setError("");
    api<TableData>(
      `/station/database/${table}?limit=25&offset=${offset}`,
      token,
    )
      .then((value) => {
        if (live) setData(value);
      })
      .catch((e) => {
        if (live) {
          setData(null);
          setError(explain(e));
        }
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [table, offset, token, overview?.available, revision]);
  return (
    <section className="database-browser">
      <div className="database-heading">
        <div>
          <h3>データベース</h3>
          <p>
            {overview?.fileName || "SQLite"}{" "}
            <span className="readonly-label">読み取り専用</span>
          </p>
        </div>
        <button onClick={() => setRevision((v) => v + 1)} disabled={loading}>
          <RefreshCw size={16} />
          更新
        </button>
      </div>
      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}
      {overview && !overview.available && (
        <p className="empty">DBファイルが存在しません。</p>
      )}
      <div className="db-table-tabs" aria-label="テーブル">
        {overview?.tables.map((t) => (
          <button
            key={t.name}
            className={table === t.name ? "selected" : ""}
            aria-pressed={table === t.name}
            onClick={() => {
              setTable(t.name);
              setOffset(0);
              setData(null);
            }}
          >
            <span>{t.label}</span>
            <small>{t.rowCount}件</small>
          </button>
        ))}
      </div>
      {loading && (
        <p className="manage-note" role="status">
          読み込んでいます…
        </p>
      )}
      {data && (
        <>
          <div className="db-pagination">
            <code>{data.table}</code>
            <span>
              {data.rowCount
                ? `${data.offset + 1}–${Math.min(data.offset + data.rows.length, data.rowCount)} / ${data.rowCount}件`
                : "0件"}
            </span>
            <button
              aria-label="前の25件"
              disabled={loading || offset === 0}
              onClick={() => setOffset(Math.max(0, offset - 25))}
            >
              <ChevronLeft size={17} />
            </button>
            <button
              aria-label="次の25件"
              disabled={loading || offset + data.rows.length >= data.rowCount}
              onClick={() => setOffset(offset + 25)}
            >
              <ChevronRight size={17} />
            </button>
          </div>
          <div className="db-scroll">
            <table>
              <thead>
                <tr>
                  <th>詳細</th>
                  {data.columns.map((c) => (
                    <th key={c.name}>
                      <code>{c.name}</code>
                      <small>
                        {c.type}
                        {c.primaryKey ? " · PK" : ""}
                        {c.redacted ? " · 非表示" : ""}
                      </small>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.rows.map((row, i) => (
                  <tr key={i} className={selected === i ? "selected" : ""}>
                    <td>
                      <button
                        aria-label={`${offset + i + 1}行目の詳細`}
                        onClick={() => setSelected(selected === i ? null : i)}
                      >
                        表示
                      </button>
                    </td>
                    {data.columns.map((c) => (
                      <td key={c.name}>
                        <span
                          title={
                            c.redacted ? "認証情報は表示しません" : undefined
                          }
                        >
                          {cellText(row[c.name]).slice(0, 140)}
                          {cellText(row[c.name]).length > 140 ? "…" : ""}
                        </span>
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
            {!data.rows.length && (
              <p className="empty">このテーブルにデータはありません。</p>
            )}
          </div>
          {selected !== null && data.rows[selected] && (
            <section className="db-row-detail">
              <h4>{offset + selected + 1}行目の内容</h4>
              <p className="manage-note">
                JSON形式の値は展開して表示します。長いテキストは各値の先頭
                {data.textLimit}文字までです。
              </p>
              <pre>
                {JSON.stringify(detailedRow(data.rows[selected]), null, 2)}
              </pre>
            </section>
          )}
        </>
      )}
    </section>
  );
}
export function ManagementPanel({
  tab,
  setTab,
  station,
  robots,
  statusText,
  halting,
  busy,
  tokenInput,
  setTokenInput,
  onConnect,
  onDisconnect,
  onRefresh,
  onStop,
  onClose,
  pending,
  onRetry,
  error,
}: {
  tab: ManagementTab;
  setTab: (v: ManagementTab) => void;
  station: string;
  robots: Robot[];
  statusText: Record<string, string>;
  halting: boolean;
  busy: boolean;
  tokenInput: string;
  setTokenInput: (v: string) => void;
  onConnect: () => void;
  onDisconnect: () => void;
  onRefresh: () => void;
  onStop: (ids: string[]) => void;
  onClose: () => void;
  pending: { robotId: string; requestId: string }[];
  onRetry: (id: string) => void;
  error: string;
}) {
  const closeButton = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const previous = document.activeElement;
    closeButton.current?.focus();
    return () => {
      requestAnimationFrame(() => {
        if (previous instanceof HTMLElement && previous.isConnected)
          previous.focus();
      });
    };
  }, []);
  return (
    <div
      className="overlay management-overlay"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        className="management-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="management-title"
      >
        <header className="management-header">
          <div>
            <span>運用・開発</span>
            <h2 id="management-title">管理</h2>
          </div>
          <button ref={closeButton} aria-label="管理を閉じる" onClick={onClose}>
            <X size={22} />
          </button>
        </header>
        <nav className="management-tabs" aria-label="管理メニュー">
          <button
            className={tab === "robots" ? "selected" : ""}
            onClick={() => setTab("robots")}
          >
            <Radio size={17} />
            機体の状態
          </button>
          <button
            className={tab === "connection" ? "selected" : ""}
            onClick={() => setTab("connection")}
          >
            <KeyRound size={17} />
            接続設定
          </button>
          <button
            className={tab === "database" ? "selected" : ""}
            onClick={() => setTab("database")}
          >
            <Database size={17} />
            DB確認
          </button>
        </nav>
        <div className="management-body">
          {error && (
            <p className="inline-error" role="alert">
              {error}
            </p>
          )}
          {tab === "connection" ? (
            <section className="connection-form">
              <h3>端末の接続設定</h3>
              <label>
                ステーショントークン
                <input
                  type="password"
                  autoComplete="off"
                  value={tokenInput}
                  onChange={(e) => setTokenInput(e.target.value)}
                />
              </label>
              <button
                className="primary"
                disabled={busy || !tokenInput.trim()}
                onClick={onConnect}
              >
                {busy ? "確認中…" : "接続する"}
              </button>
              {station && (
                <>
                  <p className="connected-message">
                    この端末は接続設定済みです。
                  </p>
                  <button
                    className="subtle-button"
                    disabled={busy}
                    onClick={onDisconnect}
                  >
                    接続を解除する
                  </button>
                </>
              )}
            </section>
          ) : !station ? (
            <section className="empty">
              <p>接続設定をすると確認できます。</p>
              <button onClick={() => setTab("connection")}>
                接続設定を開く
              </button>
            </section>
          ) : tab === "database" ? (
            <DatabaseBrowser token={station} />
          ) : (
            <>
              <div className="management-actions">
                <button onClick={onRefresh}>
                  <RefreshCw size={16} />
                  更新
                </button>
                <button
                  className="danger"
                  disabled={halting || !robots.some((r) => r.executionId)}
                  onClick={() =>
                    onStop(
                      robots.flatMap((r) =>
                        r.executionId ? [r.executionId] : [],
                      ),
                    )
                  }
                >
                  <Square size={16} />
                  全台に停止要求
                </button>
              </div>
              <div className="managed-robots">
                {robots.map((r, i) => (
                  <article
                    key={r.id}
                    className={`managed-robot robot-${i % 3}`}
                  >
                    <span className="robot-avatar">
                      <Bot size={25} />
                    </span>
                    <div>
                      <h3>{r.displayName}</h3>
                      <p>{statusText[r.availability] || r.availability}</p>
                      <code>
                        {r.id} · {r.firmwareVersion || "未接続"}
                      </code>
                    </div>
                    {r.executionId && (
                      <button
                        className="danger"
                        disabled={halting}
                        onClick={() => onStop([r.executionId!])}
                      >
                        <Square size={16} />
                        停止
                      </button>
                    )}
                  </article>
                ))}
              </div>
              {!robots.length && (
                <p className="empty">登録されている機体を確認しています。</p>
              )}
              {pending.length > 0 && (
                <section className="pending-requests">
                  <h3>結果が未確認の送信</h3>
                  {pending.map((p) => (
                    <div key={p.robotId}>
                      <span>
                        {robots.find((r) => r.id === p.robotId)?.displayName ||
                          p.robotId}
                      </span>
                      <button
                        disabled={busy}
                        onClick={() => onRetry(p.robotId)}
                      >
                        前の送信結果を確認
                      </button>
                    </div>
                  ))}
                </section>
              )}
            </>
          )}
        </div>
      </section>
    </div>
  );
}
