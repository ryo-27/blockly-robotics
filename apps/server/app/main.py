import hmac
import logging
import os
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import REPOSITORY_ROOT, WorkshopConfig, load_config
from app.execution_models import (
    ControlRequest,
    ControlResponse,
    TestExecutionRequest,
    TestExecutionView,
)
from app.executions import ExecutionError, ExecutionStore
from app.schemas import AgentResponse, HeartbeatRequest, RegisterRequest, RobotView
from app.store import SessionConflict, token_digest

bearer = HTTPBearer(auto_error=False)
Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]


def create_app(
    config: WorkshopConfig | None = None,
    db_path: Path | None = None,
    clock: Callable[[], float] = time.time,
) -> FastAPI:
    store = ExecutionStore(
        db_path
        or Path(os.environ.get("WORKSHOP_DB", str(REPOSITORY_ROOT / "data/workshop.sqlite3")))
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        settings = config or load_config()
        store.initialize(settings)
        application.state.settings = settings
        yield

    api = FastAPI(title="Robot Workshop API", version="0.2.0", lifespan=lifespan)

    @api.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        if request.url.path == "/api/robot-agent/control":
            fields = ", ".join(
                f"{'.'.join(str(part) for part in item['loc'])} ({item['type']})"
                for item in error.errors()
            )
            # 入力値・資格情報を出さず、拒否した項目と検証の種類だけを記録する。
            logging.getLogger(__name__).warning("Robot control report rejected: %s", fields)
        return await request_validation_exception_handler(request, error)

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
        return {"status": "ok", "protocolVersion": 1, "phase": 2}

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

    @api.post("/api/robot-agent/control", response_model=ControlResponse)
    def control(body: ControlRequest, robot_id: Annotated[str, Depends(require_robot)]):
        try:
            return store.control(robot_id, body, clock())
        except SessionConflict as error:
            raise HTTPException(409, str(error)) from error

    @api.post(
        "/api/robots/{robot_id}/test-executions",
        response_model=TestExecutionView,
        dependencies=[Depends(require_station)],
    )
    def create_test_execution(robot_id: str, body: TestExecutionRequest):
        try:
            return store.create_test(robot_id, body, clock())
        except ExecutionError as error:
            raise HTTPException(error.status_code, error.code) from error

    @api.get(
        "/api/test-executions/{execution_id}",
        response_model=TestExecutionView,
        dependencies=[Depends(require_station)],
    )
    def get_test_execution(execution_id: str):
        try:
            return store.get_test(execution_id)
        except ExecutionError as error:
            raise HTTPException(error.status_code, error.code) from error

    @api.post(
        "/api/test-executions/{execution_id}/stop",
        response_model=TestExecutionView,
        dependencies=[Depends(require_station)],
    )
    def stop_test_execution(execution_id: str):
        try:
            return store.stop_test(execution_id, clock())
        except ExecutionError as error:
            raise HTTPException(error.status_code, error.code) from error

    return api


app = create_app()
