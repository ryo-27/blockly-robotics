import { it, expect, vi, afterEach } from "vitest";
import {
  captureInstructions,
  prepareSend,
  sendInstructions,
} from "../src/station";
import { newId } from "../src/api";
import type { Ast } from "../src/types";
afterEach(() => vi.unstubAllGlobals());
it("送信する機体と指示を固定し、編集で未確認の要求を書き換えない", () => {
  const ast: Ast = {
    version: 1,
    commands: [{ type: "WAIT", durationMs: 1000 }],
  };
  const intent = prepareSend("robot-01", ast, ["b1"]);
  ast.commands.push({ type: "TURN", direction: "LEFT" });
  expect(intent.ast.commands).toHaveLength(1);
  expect(intent.robotId).toBe("robot-01");
});
it("応答が失われた後も同じ要求を送り、参加者・保存・予約APIを呼ばない", async () => {
  const fetch = vi
    .fn()
    .mockRejectedValueOnce(new TypeError("network"))
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ executionId: "e1", status: "QUEUED" })),
    );
  vi.stubGlobal("fetch", fetch);
  const intent = prepareSend(
    "robot-02",
    { version: 1, commands: [{ type: "WAIT", durationMs: 500 }] },
    [],
  );
  await expect(sendInstructions("station", intent)).rejects.toMatchObject({
    code: "NETWORK_ERROR",
  });
  await expect(sendInstructions("station", intent)).resolves.toMatchObject({
    executionId: "e1",
  });
  expect(fetch.mock.calls[0][0]).toBe("/api/robots/robot-02/executions");
  expect(fetch.mock.calls[0][1].body).toBe(fetch.mock.calls[1][1].body);
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({
    requestId: intent.requestId,
    ast: intent.ast,
  });
});
it("LAN内HTTPで利用できるUUIDを毎回新しく生成する", () => {
  expect(newId()).toMatch(
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
  );
  expect(newId()).not.toBe(newId());
});

it("機体を決める前に指示を固定し、後から送信先だけを指定できる", () => {
  const ast: Ast = {
    version: 1,
    commands: [{ type: "WAIT", durationMs: 500 }],
  };
  const snapshot = captureInstructions(ast, ["block-1"]);
  expect(snapshot).not.toHaveProperty("robotId");
  ast.commands.push({ type: "TURN", direction: "LEFT" });
  const intent = prepareSend("robot-03", snapshot.ast, snapshot.blockIds);
  expect(intent.robotId).toBe("robot-03");
  expect(intent.ast.commands).toHaveLength(1);
});

it("クエストのIDと版を送信要求へ固定し、再送でも変えない", async () => {
  const fetch = vi
    .fn()
    .mockResolvedValue(new Response(JSON.stringify({ executionId: "e1" })));
  vi.stubGlobal("fetch", fetch);
  const quest = { id: "Q01", version: 1, title: "探検基地" };
  const intent = prepareSend(
    "robot-01",
    { version: 1, commands: [{ type: "WAIT", durationMs: 100 }] },
    ["b1"],
    quest,
  );
  quest.version = 2;
  await sendInstructions("station", intent);
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toMatchObject({
    questId: "Q01",
    questVersion: 1,
  });
});
