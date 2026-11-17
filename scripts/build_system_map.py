"""公開可能なソースと説明データから、オフライン閲覧用のマップを生成する。"""

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAP_DIRECTORY = ROOT / "docs/system-map"
# 秘密設定や実行時データを取り込まないよう、公開ソースを列挙する。
SOURCES = (
    "apps/server/app/config.py",
    "apps/server/app/main.py",
    "apps/server/app/schemas.py",
    "apps/server/app/store.py",
    "apps/server/pyproject.toml",
    "apps/server/uv.lock",
    "apps/server/tests/test_connectivity.py",
    "firmware/src/main.cpp",
    "firmware/src/motor_control.cpp",
    "firmware/include/motor_control.h",
    "firmware/include/robot_config.example.h",
    "firmware/platformio.ini",
    "config/local.example.json",
    ".gitignore",
    "README.md",
    "docs/architecture.md",
    "docs/protocol.md",
    "docs/phase-1-verification.md",
    "docs/system-design.md",
)


def build() -> str:
    data = json.loads((MAP_DIRECTORY / "map.json").read_text(encoding="utf-8"))
    sources = {}
    digest = hashlib.sha256()
    for relative in SOURCES:
        text = (ROOT / relative).read_text(encoding="utf-8")
        sources[relative] = text.splitlines()
        digest.update(relative.encode())
        digest.update(text.encode())
    nodes = {node["id"] for node in data["nodes"]}
    if len(nodes) != len(data["nodes"]):
        raise ValueError("Duplicate node ID")
    for node in data["nodes"]:
        for source in node["sources"]:
            lines = sources[source["path"]]
            matches = [i + 1 for i, line in enumerate(lines) if source["find"] in line]
            if not matches:
                raise ValueError(f"Missing source: {source}")
            source["line"] = matches[0]
    for view in data["views"]:
        visible = {position["id"] for position in view["positions"]}
        if not visible <= nodes:
            raise ValueError(f"Unknown node in {view['id']}")
        if len(visible) != len(view["positions"]):
            raise ValueError(f"Duplicate position in {view['id']}")
        for edge in view["edges"]:
            if edge["source"] not in visible or edge["target"] not in visible:
                raise ValueError(f"Edge outside view: {edge}")
        if any(step["node"] not in visible for step in view["steps"]):
            raise ValueError(f"Walkthrough outside view: {view['id']}")
    data["sources"] = sources
    data["sourceDigest"] = digest.hexdigest()[:12]
    digest.update((MAP_DIRECTORY / "map.json").read_bytes())
    data["buildDigest"] = digest.hexdigest()[:12]
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    payload = (
        payload.replace("<", "\\u003c")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    return (
        "// scripts/build_system_map.pyで生成。直接編集しない。\nwindow.SYSTEM_MAP = "
        + payload
        + ";\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="説明とソースの同期を確認する"
    )
    args = parser.parse_args()
    output = MAP_DIRECTORY / "data.js"
    expected = build()
    if args.check:
        if not output.exists() or output.read_text(encoding="utf-8") != expected:
            raise SystemExit(
                "System map is stale. Run: python3 scripts/build_system_map.py"
            )
        print(
            "System map is up to date; source references and graph connections are valid."
        )
    else:
        output.write_text(expected, encoding="utf-8")
        print("Generated docs/system-map/data.js")


if __name__ == "__main__":
    main()
