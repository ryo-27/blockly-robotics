import hmac
import os
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import REPOSITORY_ROOT, WorkshopConfig, load_config
from app.schemas import AgentResponse, HeartbeatRequest, RegisterRequest, RobotView
from app.store import SessionConflict, Store, token_digest

bearer = HTTPBearer(auto_error=False)
Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]


def create_app(
    config: WorkshopConfig | None = None,
    db_path: Path | None = None,
    clock: Callable[[], float] = time.time,
) -> FastAPI:
    store = Store(
        db_path
        or Path(os.environ.get("WORKSHOP_DB", str(REPOSITORY_ROOT / "data/workshop.sqlite3")))
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        settings = config or load_config()
        store.initialize(settings)
        application.state.settings = settings
        yield

    api = FastAPI(title="Robot Workshop API", version="0.1.0", lifespan=lifespan)

    def require_token(credentials: Credentials) -> str:
        if credentials is None:
            raise HTTPException(
                401, "AUTHENTICATION_REQUIRED", headers={"WWW-Authenticate": "Bearer"}
            )
        return credentials.credentials

    def require_robot(token: Annotated[str, Depends(require_token)]) -> str:
        robot_id = store.authenticate(token)
        if robot_id is None:
            raise HTTPException(401, "INVALID_ROBOT_TOKEN")
        return robot_id

    def require_station(token: Annotated[str, Depends(require_token)]):
        expected = api.state.settings.station_token.get_secret_value()
        if not hmac.compare_digest(token_digest(token), token_digest(expected)):
            raise HTTPException(403, "STATION_ACCESS_REQUIRED")

    @api.get("/api/health")
    def health():
        return {"status": "ok", "protocolVersion": 1, "phase": 1}

    @api.get("/api/robots", response_model=list[RobotView], dependencies=[Depends(require_station)])
    def robots():
        return store.list_robots(clock())

    @api.post("/api/robot-agent/register", response_model=AgentResponse)
    def register(body: RegisterRequest, robot_id: Annotated[str, Depends(require_robot)]):
        if body.robot_id != robot_id:
            raise HTTPException(403, "ROBOT_ID_MISMATCH")
        session = store.register(robot_id, str(body.boot_id), body.firmware_version, clock())
        return AgentResponse(session_id=session)

    @api.post("/api/robot-agent/heartbeat", response_model=AgentResponse)
    def heartbeat(body: HeartbeatRequest, robot_id: Annotated[str, Depends(require_robot)]):
        try:
            store.heartbeat(
                robot_id, str(body.boot_id), str(body.session_id), body.sequence, clock()
            )
        except SessionConflict as error:
            raise HTTPException(409, str(error)) from error
        return AgentResponse(session_id=body.session_id)

    return api


app = create_app()
