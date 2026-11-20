export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
  ) {
    super(code);
  }
}
export async function api<T>(
  path: string,
  token: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch("/api" + path, {
      method,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(8000),
    });
  } catch {
    throw new ApiError(0, "NETWORK_ERROR");
  }
  const value = await response.json().catch(() => null);
  if (!response.ok)
    throw new ApiError(
      response.status,
      typeof value?.detail === "string" ? value.detail : "VALIDATION_ERROR",
    );
  return value as T;
}
const messages: Record<string, string> = {
  QUEST_REQUIRED: "ifを使う指示ではクエストの選択が必要です。",
  QUEST_PATH_BLOCKED: "岩またはマップ外へ移動するため送信できません。",
  QUEST_VERSION_CONFLICT: "クエストが更新されました。選び直してください。",
  QUEST_NOT_FOUND: "クエストが見つかりません。",
  NO_COMMANDS: "この条件では実行する動作がありません。",
  EXPANDED_PROGRAM_TOO_LARGE: "実行する命令数が上限64個を超えています。",
  DATABASE_BUSY: "DBが使用中です。少し待ってから更新してください。",
  DATABASE_NOT_FOUND: "DBファイルが存在しません。",
  TABLE_NOT_FOUND: "指定のテーブルが存在しません。",
  NETWORK_ERROR:
    "サーバーに つながりません。実行結果が不明な場合は、同じ内容で再確認します。",
  ROBOT_BUSY: "この機体は実行中です。停止・完了してから送信してください。",
  ROBOT_RESERVED:
    "この機体は別の操作で使用中です。空いている機体を選んでください。",
  ROBOT_NOT_READY: "ロボットとの つうしんを かくにんしてね。",
  RESERVATION_EXPIRED: "よやくの じかんが きれました。もういちど えらんでね。",
  RESERVATION_NOT_AVAILABLE:
    "このよやくは つかえません。ロボットを えらびなおしてね。",
  PROGRAM_VERSION_CONFLICT:
    "ほかの画面でプログラムが変わりました。保存したものを開きなおしてください。",
  MOTION_DISABLED:
    "このロボットは そうこうが ゆうこうに なっていません。先生に きいてね。",
  COMMAND_TOO_LONG: "ひとつの うごきが ながすぎます。すすむマスを へらしてね。",
  EXECUTION_TOO_LONG:
    "プログラムが 30びょうを こえています。うごきを へらしてね。",
  STATION_ACCESS_REQUIRED: "先生のステーショントークンを確認してください。",
  IDEMPOTENCY_CONFLICT:
    "送信内容が一致しません。先生と履歴を確認してください。",
  VALIDATION_ERROR: "プログラムの かずや ないようを かくにんしてね。",
};
export function explain(error: unknown): string {
  return error instanceof ApiError
    ? messages[error.code] ||
        `そうさできませんでした（${error.code}）。先生に きいてね。`
    : error instanceof Error
      ? error.message
      : "もういちど かくにんしてね。";
}
export function readSession<T>(key: string, fallback: T): T {
  try {
    return JSON.parse(sessionStorage.getItem(key) || "null") ?? fallback;
  } catch {
    return fallback;
  }
}

// LAN内HTTPでも利用できる暗号学的乱数。randomUUID()はHTTPS限定のため使わない。
export function newId(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
