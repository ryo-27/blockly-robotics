from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.workshop import WorkshopStore
from app.workshop_models import (
    ExecutionCreate,
    ExecutionView,
    Principal,
    ProgramCreate,
    ProgramUpdate,
    ProgramView,
    ReservationCreate,
    ReservationRenew,
    ReservationView,
    RobotEventView,
    SessionCreate,
    SessionIssued,
    SessionView,
)


def workshop_router(
    store: WorkshopStore, clock: Callable[[], float], require_client, require_station
) -> APIRouter:
    router = APIRouter()
    Client = Annotated[Principal, Depends(require_client)]
    Station = [Depends(require_station)]

    @router.post("/api/sessions", response_model=SessionIssued, dependencies=Station)
    def issue_session(body: SessionCreate):
        return store.issue_session(body.label, clock())

    @router.get("/api/sessions", response_model=list[SessionView], dependencies=Station)
    def sessions():
        return store.sessions(clock())

    @router.delete("/api/sessions/{session_id}", response_model=SessionView, dependencies=Station)
    def revoke_session(session_id: UUID):
        return store.revoke_session(str(session_id), clock())

    @router.post("/api/programs", response_model=ProgramView)
    def save_program(body: ProgramCreate, actor: Client):
        return store.create_program(actor, body, clock())

    @router.put("/api/programs/{program_id}", response_model=ProgramView)
    def update_program(program_id: UUID, body: ProgramUpdate, actor: Client):
        return store.update_program(str(program_id), actor, body, clock())

    @router.get("/api/programs/{program_id}", response_model=ProgramView)
    def get_program(program_id: UUID, actor: Client):
        return store.get_program(str(program_id), actor)

    @router.get("/api/programs", response_model=list[ProgramView])
    def programs(
        actor: Client,
        session_id: UUID | None = None,
        limit: int = Query(default=25, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ):
        return store.programs(actor, str(session_id) if session_id else None, limit, offset)

    @router.post(
        "/api/robots/{robot_id}/reservations", response_model=ReservationView, dependencies=Station
    )
    def reserve(robot_id: str, body: ReservationCreate):
        return store.reserve(robot_id, body, clock())

    @router.get("/api/reservations", response_model=list[ReservationView])
    def reservations(
        actor: Client,
        limit: int = Query(default=25, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ):
        return store.reservations(actor, clock(), limit, offset)

    @router.get("/api/reservations/{reservation_id}", response_model=ReservationView)
    def get_reservation(reservation_id: UUID, actor: Client):
        return store.get_reservation(str(reservation_id), actor, clock())

    @router.post(
        "/api/reservations/{reservation_id}/renew",
        response_model=ReservationView,
        dependencies=Station,
    )
    def renew_reservation(reservation_id: UUID, body: ReservationRenew):
        return store.renew_reservation(str(reservation_id), str(body.session_id), clock())

    @router.delete("/api/reservations/{reservation_id}", response_model=ReservationView)
    def release_reservation(reservation_id: UUID, actor: Client):
        return store.release_reservation(str(reservation_id), actor, clock())

    @router.post("/api/executions", response_model=ExecutionView, dependencies=Station)
    def create_execution(body: ExecutionCreate):
        return store.create_execution(body, clock())

    @router.get("/api/executions", response_model=list[ExecutionView])
    def executions(
        actor: Client,
        robot_id: str | None = None,
        limit: int = Query(default=25, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ):
        return store.executions(actor, robot_id, limit, offset)

    @router.get("/api/executions/{execution_id}", response_model=ExecutionView)
    def get_execution(execution_id: UUID, actor: Client):
        return store.get_execution(str(execution_id), actor)

    @router.post("/api/executions/{execution_id}/stop", response_model=ExecutionView)
    def stop_execution(execution_id: UUID, actor: Client):
        return store.stop_execution(str(execution_id), actor, clock())

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
