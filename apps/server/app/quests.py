"""マップ上の理想移動を計算し、機体用の命令列へ条件分岐を解決する。"""

import json
import re
from typing import Literal

from pydantic import Field, model_validator

from app.config import REPOSITORY_ROOT
from app.execution_models import MAX_COMMANDS, MoveCommand, TurnCommand
from app.executions import ExecutionError
from app.schemas import Message
from app.workshop_models import CanMoveForward, IfCommand, ProgramAst, RepeatCommand


def coordinates(cell: str):
    if not re.fullmatch(r"[A-Z][1-9][0-9]?", cell):
        raise ValueError("INVALID_MAP_CELL")
    return ord(cell[0]) - ord("A"), int(cell[1:]) - 1


class Start(Message):
    cell: str
    heading: Literal["N", "E", "S", "W"]


class QuestMap(Message):
    width: int = Field(strict=True, ge=1, le=26)
    height: int = Field(strict=True, ge=1, le=99)
    start: Start
    goal: str
    blocked: list[str]
    checkpoints: list[str]

    @model_validator(mode="after")
    def valid_cells(self):
        for cell in [self.start.cell, self.goal, *self.blocked, *self.checkpoints]:
            x, y = coordinates(cell)
            if not 0 <= x < self.width or not 0 <= y < self.height:
                raise ValueError("MAP_CELL_OUT_OF_BOUNDS")
        if set(self.blocked) & {self.start.cell, self.goal, *self.checkpoints}:
            raise ValueError("BLOCKED_MAP_MARKER")
        return self


class Quest(Message):
    id: str
    version: int = Field(strict=True, ge=1)
    title: str
    map: QuestMap


def load_quests():
    quests = [
        Quest.model_validate(q)
        for q in json.loads((REPOSITORY_ROOT / "config/quests.json").read_text())
    ]
    if len({q.id for q in quests}) != len(quests):
        raise ValueError("DUPLICATE_QUEST_ID")
    return {q.id: q for q in quests}


def quest_catalog():
    # マップや予測経路を送信画面へ表示しない。
    return [q.model_dump(include={"id", "version", "title"}) for q in load_quests().values()]


def get_quest(identifier, version):
    if identifier is None:
        return None
    quest = load_quests().get(identifier)
    if quest is None:
        raise ExecutionError("QUEST_NOT_FOUND", 404)
    if quest.version != version:
        raise ExecutionError("QUEST_VERSION_CONFLICT", 409)
    return quest


def resolve_program(ast: ProgramAst, quest: Quest | None):
    commands, indices = [], []
    source_indices = {}

    def index_sources(items):
        for item in items:
            if isinstance(item, (RepeatCommand, IfCommand)):
                index_sources(item.body)
                if isinstance(item, IfCommand):
                    index_sources(item.else_body)
            else:
                source_indices[id(item)] = len(source_indices)

    index_sources(ast.commands)
    board = quest.map if quest else None
    x, y = coordinates(board.start.cell) if board else (0, 0)
    heading = "NESW".index(board.start.heading) if board else 0
    blocked = {coordinates(cell) for cell in board.blocked} if board else set()
    vectors = ((0, 1), (1, 0), (0, -1), (-1, 0))

    def can_enter(nx, ny):
        return (
            board is not None
            and 0 <= nx < board.width
            and 0 <= ny < board.height
            and (nx, ny) not in blocked
        )

    def condition_matches(condition):
        if board is None:
            raise ExecutionError("QUEST_REQUIRED", 422)
        if isinstance(condition, CanMoveForward):
            dx, dy = vectors[heading]
            return can_enter(x + dx, y + dy)
        cell = chr(x + ord("A")) + str(y + 1)
        checks = {
            "GOAL": cell == board.goal,
            "START": cell == board.start.cell,
            "CHECKPOINT": cell in board.checkpoints,
        }
        checks["PATH"] = not any(checks.values())
        return checks[condition.cell_type]

    def walk(items):
        nonlocal x, y, heading
        for item in items:
            if isinstance(item, RepeatCommand):
                for _ in range(item.count):
                    walk(item.body)
            elif isinstance(item, IfCommand):
                walk(item.body if condition_matches(item.condition) else item.else_body)
            else:
                if len(commands) >= MAX_COMMANDS:
                    raise ExecutionError("EXPANDED_PROGRAM_TOO_LARGE", 422)
                if board and isinstance(item, MoveCommand):
                    dx, dy = vectors[heading]
                    sign = 1 if item.direction == "FORWARD" else -1
                    # 複数マスの途中で岩や境界を通過する場合も拒否する。
                    for _ in range(item.steps):
                        nx, ny = x + sign * dx, y + sign * dy
                        if not can_enter(nx, ny):
                            raise ExecutionError("QUEST_PATH_BLOCKED", 422)
                        x, y = nx, ny
                elif isinstance(item, TurnCommand):
                    heading = (heading + (1 if item.direction == "RIGHT" else -1)) % 4
                commands.append(item)
                indices.append(source_indices[id(item)])

    walk(ast.commands)
    if not commands:
        raise ExecutionError("NO_COMMANDS", 422)
    cleared = (x, y) == coordinates(board.goal) if board else None
    return commands, indices, cleared
