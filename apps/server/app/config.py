import json
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


class RobotConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,47}$")
    display_name: str = Field(alias="displayName", min_length=1, max_length=80)
    token: SecretStr

    @field_validator("token")
    @classmethod
    def validate_token(cls, value: SecretStr) -> SecretStr:
        check_token(value)
        return value


def check_token(value: SecretStr) -> None:
    token = value.get_secret_value()
    if len(token) < 32 or not token.isascii() or any(c.isspace() for c in token):
        raise ValueError("Use a random ASCII token of at least 32 characters without whitespace")


class WorkshopConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    station_token: SecretStr = Field(alias="stationToken")
    robots: list[RobotConfig] = Field(min_length=1)

    @field_validator("station_token")
    @classmethod
    def validate_station_token(cls, value: SecretStr) -> SecretStr:
        check_token(value)
        return value

    @model_validator(mode="after")
    def distinct_credentials(self) -> "WorkshopConfig":
        if len({r.id for r in self.robots}) != len(self.robots):
            raise ValueError("Robot IDs must be unique")
        tokens = [self.station_token.get_secret_value()]
        tokens.extend(r.token.get_secret_value() for r in self.robots)
        if len(set(tokens)) != len(tokens):
            raise ValueError("Each station and robot must have a different token")
        return self


def load_config() -> WorkshopConfig:
    path = Path(os.environ.get("WORKSHOP_CONFIG", str(REPOSITORY_ROOT / "config/local.json")))
    return WorkshopConfig.model_validate(json.loads(path.read_text(encoding="utf-8")))
