from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class Message(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class RegisterRequest(Message):
    protocol_version: Literal[1]
    robot_id: str = Field(min_length=1, max_length=48)
    boot_id: UUID
    firmware_version: str = Field(min_length=1, max_length=64)


class HeartbeatRequest(Message):
    protocol_version: Literal[1]
    session_id: UUID
    boot_id: UUID
    sequence: int = Field(strict=True, ge=1, le=2147483647)
    status: Literal["IDLE"]


class AgentResponse(Message):
    protocol_version: Literal[1] = 1
    session_id: UUID
    status: Literal["IDLE"] = "IDLE"
    poll_interval_ms: int = 500
    motors_enabled: Literal[False] = False


class RobotView(Message):
    id: str
    display_name: str
    status: Literal["OFFLINE", "IDLE"]
    last_seen_at: str | None
    firmware_version: str | None
