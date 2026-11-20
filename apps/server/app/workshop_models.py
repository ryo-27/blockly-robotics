import json
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, JsonValue, field_validator, model_validator

from app.execution_models import (
    MAX_COMMANDS,
    Command,
    MoveCommand,
    TestExecutionView,
    TurnCommand,
    WaitCommand,
)
from app.schemas import Message

MAX_REPEAT_DEPTH = 3
MAX_AST_BYTES = 32_000


class RepeatCommand(Message):
    type: Literal["REPEAT"]
    count: int = Field(strict=True, ge=1, le=20)
    body: list["AstCommand"] = Field(min_length=1, max_length=MAX_COMMANDS)


class CanMoveForward(Message):
    type: Literal["CAN_MOVE_FORWARD"]


class OnCell(Message):
    type: Literal["ON_CELL"]
    cell_type: Literal["GOAL", "CHECKPOINT", "START", "PATH"]


class IfCommand(Message):
    type: Literal["IF"]
    condition: Annotated[CanMoveForward | OnCell, Field(discriminator="type")]
    body: list["AstCommand"] = Field(min_length=1, max_length=MAX_COMMANDS)
    else_body: list["AstCommand"] = Field(default_factory=list, max_length=MAX_COMMANDS)


AstCommand = Annotated[
    MoveCommand | TurnCommand | WaitCommand | RepeatCommand | IfCommand, Field(discriminator="type")
]
RepeatCommand.model_rebuild()
IfCommand.model_rebuild()


class ProgramAst(Message):
    version: Literal[1]
    commands: list[AstCommand] = Field(min_length=1, max_length=MAX_COMMANDS)

    @field_validator("version", mode="before")
    @classmethod
    def strict_version(cls, value):
        if type(value) is not int:
            raise ValueError("AST version must be an integer")
        return value


def expand_program(ast: ProgramAst) -> list[Command]:
    expanded: list[Command] = []

    def walk(commands: list[AstCommand], depth: int):
        for command in commands:
            if isinstance(command, RepeatCommand):
                if depth >= MAX_REPEAT_DEPTH:
                    raise ValueError("REPEAT_TOO_DEEP")
                for _ in range(command.count):
                    walk(command.body, depth + 1)
            elif isinstance(command, IfCommand):
                raise ValueError("QUEST_REQUIRED")  # noqa: TRY004
            else:
                expanded.append(command)
                if len(expanded) > MAX_COMMANDS:
                    raise ValueError("EXPANDED_PROGRAM_TOO_LARGE")

    walk(ast.commands, 0)
    return expanded


def bounded_json(value: object, maximum: int) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    if len(encoded.encode("utf-8")) > maximum:
        raise ValueError("PROGRAM_DATA_TOO_LARGE")
    return encoded


def validate_structure(ast: ProgramAst):
    count = 0

    def walk(commands, depth):
        nonlocal count
        for command in commands:
            count += 1
            if count > MAX_COMMANDS:
                raise ValueError("PROGRAM_TOO_LARGE")
            if isinstance(command, (RepeatCommand, IfCommand)):
                if depth >= MAX_REPEAT_DEPTH:
                    raise ValueError("PROGRAM_TOO_DEEP")
                walk(command.body, depth + 1)
                if isinstance(command, IfCommand):
                    walk(command.else_body, depth + 1)

    walk(ast.commands, 0)


class ExecutionView(TestExecutionView):
    quest_id: str | None = None
    quest_version: int | None = None
    quest_cleared: bool | None = None
    command_block_indices: list[int] | None = None
    kind: Literal["TEST", "WORKSHOP", "STATION"]
    participant_session_id: UUID | None = Field(default=None, alias="sessionId")
    program_id: UUID | None
    program_version: int | None
    reservation_id: UUID | None
    started_at: float | None


class RobotEventView(Message):
    event_id: int
    robot_id: str
    execution_id: UUID | None
    event_type: str
    payload: dict[str, JsonValue]
    created_at: float


class StationExecutionCreate(Message):
    quest_id: str | None = Field(default=None, pattern=r"^[A-Z0-9-]{1,32}$")
    quest_version: int | None = Field(default=None, strict=True, ge=1)
    request_id: UUID
    ast: ProgramAst

    @model_validator(mode="after")
    def valid_instructions(self) -> "StationExecutionCreate":
        bounded_json(self.ast.model_dump(by_alias=True), MAX_AST_BYTES)
        validate_structure(self.ast)
        if (self.quest_id is None) != (self.quest_version is None):
            raise ValueError("QUEST_VERSION_REQUIRED")
        if self.quest_id is None:
            expand_program(self.ast)
        return self
