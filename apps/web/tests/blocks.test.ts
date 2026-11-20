import { describe, it, expect } from "vitest";
import * as Blockly from "blockly/core";
import { compile, workspaceFromAst, validateAst } from "../src/blocks";
import type { Ast, AstCommand } from "../src/types";
const move: AstCommand = { type: "MOVE", direction: "FORWARD", steps: 2 };
describe("Blocklyと実行ASTの契約", () => {
  it("ネストしたREPEATを往復し、命令元のブロックIDを保持する", () => {
    const ast: Ast = {
      version: 1,
      commands: [
        {
          type: "REPEAT",
          count: 2,
          body: [move, { type: "TURN", direction: "LEFT" }],
        },
        { type: "WAIT", durationMs: 5000 },
      ],
    };
    const ws = new Blockly.Workspace();
    Blockly.serialization.workspaces.load(workspaceFromAst(ast), ws);
    const result = compile(ws);
    expect(result.ast).toEqual(ast);
    expect(result.blockIds).toHaveLength(3);
    expect(new Set(result.blockIds).size).toBe(3);
    const restored = new Blockly.Workspace();
    Blockly.serialization.workspaces.load(
      Blockly.serialization.workspaces.save(ws),
      restored,
    );
    expect(compile(restored).ast).toEqual(ast);
    restored.dispose();
    ws.dispose();
  });
  it("切り離されたブロックを送信しない", () => {
    const ws = new Blockly.Workspace();
    Blockly.serialization.workspaces.load(
      workspaceFromAst({ version: 1, commands: [move] }),
      ws,
    );
    ws.newBlock("robot_left");
    expect(() => compile(ws)).toThrow("DISCONNECTED_BLOCKS");
    ws.dispose();
  });
  it("空の繰り返しと深すぎる繰り返しを拒否する", () => {
    expect(() =>
      validateAst({
        version: 1,
        commands: [{ type: "REPEAT", count: 2, body: [] }],
      }),
    ).toThrow();
    let c: AstCommand = move;
    for (let i = 0; i < 4; i++) c = { type: "REPEAT", count: 1, body: [c] };
    expect(() => validateAst({ version: 1, commands: [c] })).toThrow(
      "REPEAT_LIMIT_EXCEEDED",
    );
  });
  it("展開後64命令の境界を検証する", () => {
    expect(
      validateAst({
        version: 1,
        commands: Array.from({ length: 64 }, () => move),
      }),
    ).toBe(64);
    expect(() =>
      validateAst({
        version: 1,
        commands: [
          { type: "REPEAT", count: 20, body: [move, move, move, move] },
        ],
      }),
    ).toThrow("64");
  });
});

it("ifの両分岐を保存復元し、命令元IDを実行順序と独立して保持する", () => {
  const ast: Ast = {
    version: 1,
    commands: [
      {
        type: "REPEAT",
        count: 2,
        body: [
          {
            type: "IF",
            condition: { type: "ON_CELL", cellType: "CHECKPOINT" },
            body: [move],
            elseBody: [{ type: "TURN", direction: "RIGHT" }],
          },
        ],
      },
    ],
  };
  const ws = new Blockly.Workspace();
  Blockly.serialization.workspaces.load(workspaceFromAst(ast), ws);
  const result = compile(ws);
  expect(result.ast).toEqual(ast);
  expect(result.blockIds).toHaveLength(2);
  expect(new Set(result.blockIds).size).toBe(2);
  ws.dispose();
});
