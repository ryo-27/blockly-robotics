import json
from dataclasses import dataclass
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
MAX_WORKSPACE_BYTES = 200_000


@dataclass(frozen=True)
class Principal:
    role: Literal["STATION", "PARTICIPANT"]
    session_id: str | None = None


class RepeatCommand(Message):
    type: Literal["REPEAT"]
    count: int = Field(strict=True, ge=1, le=20)
    body: list["AstCommand"] = Field(min_length=1, max_length=MAX_COMMANDS)


AstCommand = Annotated[
    MoveCommand | TurnCommand | WaitCommand | RepeatCommand, Field(discriminator="type")
]
RepeatCommand.model_rebuild()


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


class ProgramContent(Message):
    name: str = Field(default="プログラム", min_length=1, max_length=80)
    blockly_workspace: dict[str, JsonValue] | None = None
    ast: ProgramAst

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Program name must not be blank")
        return value.strip()

    @model_validator(mode="after")
    def valid_program(self) -> "ProgramContent":
        bounded_json(self.blockly_workspace, MAX_WORKSPACE_BYTES)
        bounded_json(self.ast.model_dump(by_alias=True), MAX_AST_BYTES)
        expand_program(self.ast)
        return self


class ProgramCreate(ProgramContent):
    request_id: UUID


class ProgramUpdate(ProgramContent):
    expected_version: int = Field(strict=True, ge=1)


class ProgramView(ProgramContent):
    program_id: UUID
    session_id: UUID
    version: int
    expanded_command_count: int
    created_at: float
    updated_at: float


class SessionCreate(Message):
    label: str = Field(default="参加者", min_length=1, max_length=80)


class SessionView(Message):
    session_id: UUID
    label: str
    created_at: float
    expires_at: float
    revoked: bool


class SessionIssued(SessionView):
    session_token: str


class ReservationCreate(Message):
    request_id: UUID
    session_id: UUID


class ReservationRenew(Message):
    session_id: UUID


class ReservationView(Message):
    reservation_id: UUID
    request_id: UUID
    robot_id: str
    session_id: UUID
    status: Literal["RESERVED", "IN_USE", "RELEASED", "EXPIRED", "FINISHED"]
    created_at: float
    expires_at: float
    execution_id: UUID | None


class ExecutionCreate(Message):
    request_id: UUID
    program_id: UUID
    program_version: int = Field(strict=True, ge=1)
    reservation_id: UUID


class ExecutionView(TestExecutionView):
    kind: Literal["TEST", "WORKSHOP"]
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
