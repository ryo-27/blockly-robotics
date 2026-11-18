from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas import Message

MAX_COMMANDS = 64
MAX_COMMAND_MS = 5_000
MAX_EXECUTION_MS = 30_000
LEASE_MS = 1_500
ACTIVE_STATUSES = ("QUEUED", "DISPATCHED", "RUNNING", "STOP_REQUESTED")
TERMINAL_STATUSES = ("COMPLETED", "STOPPED", "ERROR")


class Calibration(Message):
    invert_left: bool = Field(default=False, strict=True)
    invert_right: bool = Field(default=True, strict=True)
    forward_one_cell_ms: int = Field(default=500, strict=True, ge=50, le=MAX_COMMAND_MS)
    backward_one_cell_ms: int = Field(default=500, strict=True, ge=50, le=MAX_COMMAND_MS)
    turn_left90_ms: int = Field(default=330, strict=True, ge=50, le=MAX_COMMAND_MS)
    turn_right90_ms: int = Field(default=330, strict=True, ge=50, le=MAX_COMMAND_MS)


class MoveCommand(Message):
    type: Literal["MOVE"]
    direction: Literal["FORWARD", "BACKWARD"]
    steps: int = Field(strict=True, ge=1, le=10)


class TurnCommand(Message):
    type: Literal["TURN"]
    direction: Literal["LEFT", "RIGHT"]


class WaitCommand(Message):
    type: Literal["WAIT"]
    duration_ms: int = Field(strict=True, ge=1, le=MAX_COMMAND_MS)


Command = Annotated[MoveCommand | TurnCommand | WaitCommand, Field(discriminator="type")]


class TestExecutionRequest(Message):
    request_id: UUID
    commands: list[Command] = Field(min_length=1, max_length=MAX_COMMANDS)


def execution_duration(commands: list[Command], calibration: Calibration) -> int:
    total = 0
    for command in commands:
        if isinstance(command, MoveCommand):
            duration = command.steps * (
                calibration.forward_one_cell_ms
                if command.direction == "FORWARD"
                else calibration.backward_one_cell_ms
            )
        elif isinstance(command, TurnCommand):
            duration = (
                calibration.turn_left90_ms
                if command.direction == "LEFT"
                else calibration.turn_right90_ms
            )
        else:
            duration = command.duration_ms
        if duration > MAX_COMMAND_MS:
            raise ValueError("COMMAND_TOO_LONG")
        total += duration
    if total > MAX_EXECUTION_MS:
        raise ValueError("EXECUTION_TOO_LONG")
    return total


class ControlRequest(Message):
    protocol_version: Literal[2]
    session_id: UUID
    boot_id: UUID
    sequence: int = Field(strict=True, ge=1, le=2147483647)
    status: Literal["IDLE", "RUNNING", "COMPLETED", "STOPPED", "ERROR"]
    execution_id: UUID | None = None
    current_command_index: int = Field(default=0, strict=True, ge=0, le=MAX_COMMANDS)
    error_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{0,47}$")

    @model_validator(mode="after")
    def consistent_state(self) -> "ControlRequest":
        if self.status == "IDLE":
            if self.execution_id is not None or self.current_command_index or self.error_code:
                raise ValueError("IDLE must have no execution")
        elif self.execution_id is None:
            raise ValueError("Execution ID is required")
        if self.status in ("IDLE", "RUNNING", "COMPLETED") and self.error_code is not None:
            raise ValueError("Error code is only valid for STOPPED or ERROR")
        if self.status == "ERROR" and self.error_code is None:
            raise ValueError("ERROR requires an error code")
        return self


class ControlResponse(Message):
    protocol_version: Literal[2] = 2
    session_id: UUID
    sequence: int
    action: Literal["IDLE", "START", "CONTINUE", "STOP", "ACK"]
    lease_ms: int = 0
    execution_id: UUID | None = None
    execution_sequence: int | None = None
    commands: list[Command] | None = None
    calibration: Calibration | None = None


class TestExecutionView(Message):
    model_config = Message.model_config | {"extra": "ignore"}
    execution_id: UUID
    request_id: UUID
    robot_id: str
    execution_sequence: int
    status: Literal[
        "QUEUED", "DISPATCHED", "RUNNING", "STOP_REQUESTED", "COMPLETED", "STOPPED", "ERROR"
    ]
    current_command_index: int
    estimated_duration_ms: int
    error_code: str | None
    created_at: float
    completed_at: float | None
