from collections.abc import Callable
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.inspection import database_overview, inspect_table
from app.quests import quest_catalog
from app.workshop import WorkshopStore
from app.workshop_models import ExecutionView, RobotEventView, StationExecutionCreate


def workshop_router(store: WorkshopStore, clock: Callable[[], float], require_station) -> APIRouter:
    router = APIRouter()
    Station = [Depends(require_station)]

    @router.get("/api/quests", dependencies=Station)
    def quests():
        return quest_catalog()

    @router.get("/api/station/status", dependencies=Station)
    def station_status():
        return {"role": "STATION"}

    @router.get("/api/station/database", dependencies=Station)
    def database():
        return database_overview(store.path)

    @router.get("/api/station/database/{table}", dependencies=Station)
    def table_contents(
        table: str,
        limit: int = Query(default=25, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ):
        return inspect_table(store.path, table, limit, offset)

    @router.post(
        "/api/robots/{robot_id}/executions", response_model=ExecutionView, dependencies=Station
    )
    def dispatch_instructions(robot_id: str, body: StationExecutionCreate):
        return store.create_station_execution(robot_id, body, clock())

    @router.get("/api/executions", response_model=list[ExecutionView], dependencies=Station)
    def executions(
        robot_id: str | None = None,
        limit: int = Query(default=25, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ):
        return store.executions(robot_id, limit, offset)

    @router.get(
        "/api/executions/{execution_id}", response_model=ExecutionView, dependencies=Station
    )
    def get_execution(execution_id: UUID):
        return store.get_execution(str(execution_id))

    @router.post(
        "/api/executions/{execution_id}/stop", response_model=ExecutionView, dependencies=Station
    )
    def stop_execution(execution_id: UUID):
        return store.stop_execution(str(execution_id), clock())

    @router.get("/api/robot-events", response_model=list[RobotEventView], dependencies=Station)
    def robot_events(
        robot_id: str | None = None,
        execution_id: UUID | None = None,
        after_id: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
    ):
        return store.robot_events(
            robot_id, str(execution_id) if execution_id else None, after_id, limit
        )

    return router
