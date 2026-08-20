from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .curriculum import CURRICULUM_VERSION, get_drill, public_curriculum, public_drill
from .datasets import dataset_schema
from .progress import progress_overview, submit_guided_attempt
from .query_supervisor import run_sql
from .sessions import create_session, get_session, save_answer, start_session, submit_session
from .sql_policy import QueryRefused


router = APIRouter(prefix="/api/prep/technical", tags=["technical-learning"])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunRequest(StrictModel):
    drillId: str = Field(min_length=1, max_length=120)
    sql: str = Field(min_length=1, max_length=50_000)


class AttemptRequest(StrictModel):
    drillId: str = Field(min_length=1, max_length=120)
    answer: Any
    hintsUnlocked: int = Field(default=0, ge=0, le=2)
    solutionRevealed: bool = False
    curriculumVersion: str = CURRICULUM_VERSION


class SessionRequest(StrictModel):
    durationMinutes: Literal[30, 45, 60]
    role: str | None = Field(default=None, max_length=80)


class AnswerRequest(StrictModel):
    answer: Any


def _not_found(exc: KeyError) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc).strip("'"))


@router.get("")
def technical_overview() -> dict[str, Any]:
    return progress_overview()


@router.get("/curriculum")
def technical_curriculum() -> dict[str, Any]:
    return public_curriculum()


@router.get("/drills/{drill_id}")
def technical_drill(drill_id: str) -> dict[str, Any]:
    try:
        drill = get_drill(drill_id)
        public = public_drill(drill_id)
    except KeyError as exc:
        raise _not_found(exc) from exc
    public["schema"] = (
        dataset_schema(drill.dataset_id, drill.dataset_version)
        if drill.dataset_id and drill.dataset_version
        else []
    )
    return public


@router.post("/run")
def technical_run(request: RunRequest) -> dict[str, Any]:
    try:
        drill = get_drill(request.drillId)
    except KeyError as exc:
        raise _not_found(exc) from exc
    if drill.kind != "sql" or not drill.dataset_id or not drill.dataset_version:
        raise HTTPException(status_code=422, detail="This drill does not execute SQL.")
    try:
        result = run_sql(drill.dataset_id, drill.dataset_version, request.sql)
    except QueryRefused as exc:
        # Preserve the useful policy category without echoing candidate SQL.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "ok": result.ok,
        "columns": result.columns,
        "rows": result.rows,
        "rowCount": result.row_count,
        "truncated": result.truncated,
        "errorCode": result.error_code,
        "message": result.message,
    }


@router.post("/attempts")
def technical_attempt(request: AttemptRequest) -> dict[str, Any]:
    try:
        return submit_guided_attempt(
            request.drillId,
            request.answer,
            hints_unlocked=request.hintsUnlocked,
            solution_revealed=request.solutionRevealed,
            curriculum_version=request.curriculumVersion,
        )
    except KeyError as exc:
        raise _not_found(exc) from exc


@router.post("/sessions")
def technical_create_session(request: SessionRequest) -> dict[str, Any]:
    try:
        return create_session(request.durationMinutes, role=request.role)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/sessions/{session_id}/start")
def technical_start_session(session_id: str) -> dict[str, Any]:
    try:
        return start_session(session_id)
    except KeyError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.patch("/sessions/{session_id}/answers/{question_id}")
def technical_save_answer(session_id: str, question_id: str, request: AnswerRequest) -> dict[str, Any]:
    try:
        return save_answer(session_id, question_id, request.answer)
    except KeyError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/sessions/{session_id}/submit")
def technical_submit_session(session_id: str) -> dict[str, Any]:
    try:
        return submit_session(session_id)
    except KeyError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/sessions/{session_id}")
def technical_get_session(session_id: str) -> dict[str, Any]:
    try:
        return get_session(session_id)
    except KeyError as exc:
        raise _not_found(exc) from exc
