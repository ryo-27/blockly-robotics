import * as Blockly from "blockly/core";
import * as Ja from "blockly/msg/ja";
import type { Ast, AstCommand, Condition, WorkspaceState } from "./types";
Blockly.setLocale(
  Object.fromEntries(
    Object.entries(Ja).filter(
      (entry): entry is [string, string] => typeof entry[1] === "string",
    ),
  ),
);
Blockly.common.defineBlocksWithJsonArray([
  {
    type: "robot_start",
    message0: "はじめる",
    nextStatement: null,
    colour: "#d89522",
  },
  {
    type: "robot_forward",
    message0: "まえに %1 マス すすむ",
    args0: [
      {
        type: "field_number",
        name: "STEPS",
        value: 1,
        min: 1,
        max: 10,
        precision: 1,
      },
    ],
    previousStatement: null,
    nextStatement: null,
    colour: "#247eb0",
  },
  {
    type: "robot_backward",
    message0: "うしろに %1 マス すすむ",
    args0: [
      {
        type: "field_number",
        name: "STEPS",
        value: 1,
        min: 1,
        max: 10,
        precision: 1,
      },
    ],
    previousStatement: null,
    nextStatement: null,
    colour: "#247eb0",
  },
  {
    type: "robot_left",
    message0: "ひだりを むく ↶",
    previousStatement: null,
    nextStatement: null,
    colour: "#7857b3",
  },
  {
    type: "robot_right",
    message0: "みぎを むく ↷",
    previousStatement: null,
    nextStatement: null,
    colour: "#7857b3",
  },
  {
    type: "robot_wait",
    message0: "%1 びょう まつ",
    args0: [
      {
        type: "field_number",
        name: "SECONDS",
        value: 1,
        min: 0.1,
        max: 5,
        precision: 0.1,
      },
    ],
    previousStatement: null,
    nextStatement: null,
    colour: "#42876d",
  },
  {
    type: "robot_repeat",
    message0: "%1 かい くりかえす",
    args0: [
      {
        type: "field_number",
        name: "COUNT",
        value: 2,
        min: 1,
        max: 20,
        precision: 1,
      },
    ],
    message1: "%1",
    args1: [{ type: "input_statement", name: "BODY" }],
    previousStatement: null,
    nextStatement: null,
    colour: "#c27431",
  },
  {
    type: "robot_if",
    message0: "もし %1 なら",
    args0: [
      {
        type: "field_dropdown",
        name: "CONDITION",
        options: [
          ["まえに すすめる", "CAN_MOVE_FORWARD"],
          ["はたの マスに いる", "GOAL"],
          ["ちょうさポイントに いる", "CHECKPOINT"],
          ["スタートに いる", "START"],
          ["みちの マスに いる", "PATH"],
        ],
      },
    ],
    message1: "%1",
    args1: [{ type: "input_statement", name: "BODY" }],
    message2: "そうでなければ",
    message3: "%1",
    args3: [{ type: "input_statement", name: "ELSE" }],
    previousStatement: null,
    nextStatement: null,
    colour: "#378b89",
  },
]);
export const theme = Blockly.Theme.defineTheme("atelier", {
  base: Blockly.Themes.Classic,
  name: "atelier",
  fontStyle: { family: "system-ui, sans-serif", size: 16, weight: "600" },
  componentStyles: {
    workspaceBackgroundColour: "#f5f8fa",
    toolboxBackgroundColour: "#fff",
    flyoutBackgroundColour: "#eef4f7",
    flyoutOpacity: 1,
    scrollbarColour: "#a9b9bc",
    insertionMarkerColour: "#173d36",
  },
});
export function toolbox(advanced: boolean) {
  return {
    kind: "flyoutToolbox",
    contents: [
      ...["robot_forward", "robot_backward", "robot_left", "robot_right"],
      ...(advanced ? ["robot_wait", "robot_repeat", "robot_if"] : []),
    ].map((type) => ({ kind: "block", type })),
  };
}
export function needsQuest(ast: Ast): boolean {
  return ast.commands.some(function visit(c): boolean {
    return c.type === "IF" || (c.type === "REPEAT" && c.body.some(visit));
  });
}
export function validateAst(ast: Ast): number {
  let nodes = 0;
  const integer = (x: number, min: number, max: number) =>
    Number.isInteger(x) && x >= min && x <= max;
  function walk(list: AstCommand[], depth: number): number {
    if (!list.length) throw new Error("EMPTY_PROGRAM_BODY");
    let expanded = 0;
    for (const c of list) {
      if (++nodes > 64) throw new Error("PROGRAM_LIMIT_64");
      if (c.type === "REPEAT" || c.type === "IF") {
        if (depth >= 3) throw new Error("REPEAT_LIMIT_EXCEEDED");
        const body = walk(c.body, depth + 1);
        if (c.type === "REPEAT") {
          if (!integer(c.count, 1, 20))
            throw new Error("REPEAT_LIMIT_EXCEEDED");
          expanded += body * c.count;
        } else {
          const other = c.elseBody.length ? walk(c.elseBody, depth + 1) : 0;
          expanded += Math.max(body, other);
        }
      } else {
        if (
          (c.type === "MOVE" && !integer(c.steps, 1, 10)) ||
          (c.type === "WAIT" && !integer(c.durationMs, 1, 5000))
        )
          throw new Error("INVALID_COMMAND_VALUE");
        expanded++;
      }
    }
    return expanded;
  }
  const count = walk(ast.commands, 0);
  if (!needsQuest(ast) && count > 64)
    throw new Error("EXPANDED_COMMAND_LIMIT_64");
  return count;
}
function conditionFromField(value: string): Condition {
  return value === "CAN_MOVE_FORWARD"
    ? { type: "CAN_MOVE_FORWARD" }
    : {
        type: "ON_CELL",
        cellType: value as "GOAL" | "CHECKPOINT" | "START" | "PATH",
      };
}
function conditionField(value: Condition): string {
  return value.type === "CAN_MOVE_FORWARD" ? value.type : value.cellType;
}
export function compile(workspace: Blockly.Workspace): {
  ast: Ast;
  blockIds: string[];
} {
  const tops = workspace.getTopBlocks(true);
  if (tops.length !== 1 || tops[0].type !== "robot_start")
    throw new Error("DISCONNECTED_BLOCKS");
  const paths = new Map<AstCommand, string>();
  function chain(first: Blockly.Block | null, depth = 0): AstCommand[] {
    const list: AstCommand[] = [];
    if (depth > 3) throw new Error("REPEAT_LIMIT_EXCEEDED");
    for (let b = first; b; b = b.getNextBlock()) {
      if (!b.isEnabled()) throw new Error("UNSUPPORTED_BLOCK");
      let c: AstCommand;
      switch (b.type) {
        case "robot_forward":
        case "robot_backward":
          c = {
            type: "MOVE",
            direction: b.type === "robot_forward" ? "FORWARD" : "BACKWARD",
            steps: Number(b.getFieldValue("STEPS")),
          };
          break;
        case "robot_left":
        case "robot_right":
          c = {
            type: "TURN",
            direction: b.type === "robot_left" ? "LEFT" : "RIGHT",
          };
          break;
        case "robot_wait":
          c = {
            type: "WAIT",
            durationMs: Math.round(Number(b.getFieldValue("SECONDS")) * 1000),
          };
          break;
        case "robot_if":
          c = {
            type: "IF",
            condition: conditionFromField(String(b.getFieldValue("CONDITION"))),
            body: chain(b.getInputTargetBlock("BODY"), depth + 1),
            elseBody: chain(b.getInputTargetBlock("ELSE"), depth + 1),
          };
          break;
        case "robot_repeat":
          c = {
            type: "REPEAT",
            count: Number(b.getFieldValue("COUNT")),
            body: chain(b.getInputTargetBlock("BODY"), depth + 1),
          };
          break;
        default:
          throw new Error("UNSUPPORTED_BLOCK");
      }
      paths.set(c, b.id);
      list.push(c);
    }
    return list;
  }
  const ast: Ast = { version: 1, commands: chain(tops[0].getNextBlock()) };
  validateAst(ast);
  const blockIds: string[] = [];
  const walk = (list: AstCommand[]) =>
    list.forEach((c) => {
      if (c.type === "REPEAT" || c.type === "IF") {
        walk(c.body);
        if (c.type === "IF") walk(c.elseBody);
      } else blockIds.push(paths.get(c)!);
    });
  walk(ast.commands);
  return { ast, blockIds };
}
export function workspaceFromAst(ast: Ast): WorkspaceState {
  function chain(list: AstCommand[]): Record<string, unknown> | undefined {
    const [c, ...rest] = list;
    if (!c) return;
    const block: Record<string, unknown> =
      c.type === "MOVE"
        ? {
            type:
              c.direction === "FORWARD" ? "robot_forward" : "robot_backward",
            fields: { STEPS: c.steps },
          }
        : c.type === "TURN"
          ? { type: c.direction === "LEFT" ? "robot_left" : "robot_right" }
          : c.type === "WAIT"
            ? { type: "robot_wait", fields: { SECONDS: c.durationMs / 1000 } }
            : c.type === "IF"
              ? {
                  type: "robot_if",
                  fields: { CONDITION: conditionField(c.condition) },
                  inputs: {
                    BODY: { block: chain(c.body) },
                    ELSE: { block: chain(c.elseBody) },
                  },
                }
              : {
                  type: "robot_repeat",
                  fields: { COUNT: c.count },
                  inputs: { BODY: { block: chain(c.body) } },
                };
    const next = chain(rest);
    if (next) block.next = { block: next };
    return block;
  }
  return {
    blocks: {
      languageVersion: 0,
      blocks: [
        {
          type: "robot_start",
          x: 40,
          y: 40,
          next: { block: chain(ast.commands) },
        },
      ],
    },
  };
}
export const starter = workspaceFromAst({
  version: 1,
  commands: [
    { type: "MOVE", direction: "FORWARD", steps: 1 },
    { type: "TURN", direction: "LEFT" },
  ],
});
