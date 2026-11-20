export type Command =
  | { type: "MOVE"; direction: "FORWARD" | "BACKWARD"; steps: number }
  | { type: "TURN"; direction: "LEFT" | "RIGHT" }
  | { type: "WAIT"; durationMs: number };
export type Condition =
  | { type: "CAN_MOVE_FORWARD" }
  | { type: "ON_CELL"; cellType: "GOAL" | "CHECKPOINT" | "START" | "PATH" };
export type Quest = { id: string; version: number; title: string };
export type AstCommand =
  | Command
  | { type: "REPEAT"; count: number; body: AstCommand[] }
  | {
      type: "IF";
      condition: Condition;
      body: AstCommand[];
      elseBody: AstCommand[];
    };
export type Ast = { version: 1; commands: AstCommand[] };
export type WorkspaceState = Record<string, unknown>;
export type Robot = {
  id: string;
  displayName: string;
  status: string;
  availability: string;
  available: boolean;
  executionId: string | null;
  executionStatus: string | null;
  reservationId: string | null;
  firmwareVersion: string | null;
};
export type Execution = {
  questId?: string | null;
  questVersion?: number | null;
  questCleared?: boolean | null;
  commandBlockIndices?: number[] | null;
  executionId: string;
  robotId: string;
  status: string;
  currentCommandIndex: number;
  errorCode: string | null;
  estimatedDurationMs: number;
  createdAt: number;
};
export const active = (s: string) =>
  ["QUEUED", "DISPATCHED", "RUNNING", "STOP_REQUESTED"].includes(s);
