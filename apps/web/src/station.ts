import { api, newId } from "./api";
import type { Ast, Execution, Quest } from "./types";
export type InstructionSnapshot = { ast: Ast; blockIds: string[] };
export function captureInstructions(
  ast: Ast,
  blockIds: string[],
): InstructionSnapshot {
  return { ast: structuredClone(ast), blockIds: [...blockIds] };
}
export type SendIntent = {
  robotId: string;
  requestId: string;
  ast: Ast;
  blockIds: string[];
  questId?: string;
  questVersion?: number;
  blockIndexVersion?: 2;
  executionId?: string;
};
export function prepareSend(
  robotId: string,
  ast: Ast,
  blockIds: string[],
  quest?: Quest,
): SendIntent {
  return {
    ...(quest ? { questId: quest.id, questVersion: quest.version } : {}),
    blockIndexVersion: 2,
    robotId,
    requestId: newId(),
    ast: structuredClone(ast),
    blockIds: [...blockIds],
  };
}
// タイムアウトの再確認でも、送信内容とIDを変えない。
export async function sendInstructions(
  token: string,
  intent: SendIntent,
): Promise<Execution> {
  return api<Execution>(
    `/robots/${encodeURIComponent(intent.robotId)}/executions`,
    token,
    "POST",
    {
      requestId: intent.requestId,
      ast: intent.ast,
      ...(intent.questId
        ? { questId: intent.questId, questVersion: intent.questVersion }
        : {}),
    },
  );
}
