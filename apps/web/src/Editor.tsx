import { useEffect, useRef } from "react";
import { Plus, Minus, LocateFixed } from "lucide-react";
import * as Blockly from "blockly/core";
import { compile, starter, theme, toolbox } from "./blocks";
import type { Ast, WorkspaceState } from "./types";
export type Draft = {
  workspace: WorkspaceState;
  ast: Ast | null;
  blockIds: string[];
  error: string;
};
export function Editor({
  initial,
  advanced,
  onChange,
  highlight,
}: {
  initial: WorkspaceState | null;
  advanced: boolean;
  onChange: (d: Draft) => void;
  highlight: string | null;
}) {
  const element = useRef<HTMLDivElement>(null),
    ws = useRef<Blockly.WorkspaceSvg | null>(null),
    callback = useRef(onChange);
  callback.current = onChange;
  useEffect(() => {
    if (!element.current) return;
    // タッチ端末の既定25pxを抑え、指での作業領域ドラッグは維持する。
    Blockly.Scrollbar.scrollbarThickness = 12;
    const workspace = Blockly.inject(element.current, {
      toolbox: toolbox(advanced),
      theme,
      renderer: "zelos",
      trashcan: true,
      move: { scrollbars: true, drag: true, wheel: true },
      zoom: {
        controls: false,
        wheel: true,
        startScale: 0.95 / 1.2,
        scaleSpeed: 1.2,
        minScale: 0.35,
        maxScale: 1.6,
      },
      grid: { spacing: 24, length: 2, colour: "#d8e1e5", snap: true },
      sounds: false,
      media: "/blockly/media/",
    });
    ws.current = workspace;
    try {
      Blockly.serialization.workspaces.load(initial || starter, workspace);
    } catch {
      workspace.clear();
      Blockly.serialization.workspaces.load(starter, workspace);
    }
    const start = workspace
      .getTopBlocks(false)
      .find((b) => b.type === "robot_start");
    start?.setDeletable(false);
    const report = () => {
      const snapshot = Blockly.serialization.workspaces.save(
        workspace,
      ) as WorkspaceState;
      try {
        callback.current({
          workspace: snapshot,
          ...compile(workspace),
          error: "",
        });
      } catch (e) {
        callback.current({
          workspace: snapshot,
          ast: null,
          blockIds: [],
          error: e instanceof Error ? e.message : "INVALID_WORKSPACE",
        });
      }
    };
    workspace.addChangeListener((e) => {
      if (!e.isUiEvent) report();
    });
    report();
    const observer = new ResizeObserver(() => Blockly.svgResize(workspace));
    observer.observe(element.current);
    return () => {
      observer.disconnect();
      workspace.dispose();
      ws.current = null;
    };
    // initial は編集対象が変わった時に親のkeyで読み直す。
  }, []);
  useEffect(() => {
    ws.current?.updateToolbox(toolbox(advanced));
  }, [advanced]);
  useEffect(() => {
    ws.current?.highlightBlock(highlight);
  }, [highlight]);
  return (
    <>
      <div
        ref={element}
        className="blockly-host"
        aria-label="ブロックをつなげる場所"
      />
      <div className="workspace-zoom" role="group" aria-label="作業範囲の表示">
        <button
          aria-label="ブロックを中央に表示"
          title="中央に表示"
          onClick={() => ws.current?.scrollCenter()}
        >
          <LocateFixed size={20} />
        </button>
        <button
          aria-label="拡大"
          title="拡大"
          onClick={() => ws.current?.zoomCenter(1)}
        >
          <Plus size={20} />
        </button>
        <button
          aria-label="縮小"
          title="縮小"
          onClick={() => ws.current?.zoomCenter(-1)}
        >
          <Minus size={20} />
        </button>
      </div>
    </>
  );
}
