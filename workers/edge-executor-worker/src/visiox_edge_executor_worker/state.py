from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from visiox_common.tasks import TaskCommand, TaskType
from visiox_db.models import RemoteExecution

from .redaction import redact, redact_uri


ExecutionStatus = Literal["succeeded", "failed", "canceled"]
TERMINAL_EXECUTION_STATUSES = frozenset({"succeeded", "failed", "canceled"})


class RemoteExecutionNotFoundError(LookupError):
    pass


class RemoteExecutionStateError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExecutionResult:
    status: ExecutionStatus
    exit_code: int | None = None
    phase: str | None = None
    redacted_log_uri: str | None = None
    error_code: str | None = None
    error_message: str | None = None

    @classmethod
    def succeeded(
        cls,
        *,
        exit_code: int = 0,
        phase: str | None = None,
        redacted_log_uri: str | None = None,
    ) -> ExecutionResult:
        return cls(
            status="succeeded",
            exit_code=exit_code,
            phase=phase,
            redacted_log_uri=redacted_log_uri,
        )

    @classmethod
    def failed(
        cls,
        *,
        error_code: str,
        error_message: str,
        exit_code: int | None = None,
        phase: str | None = None,
        redacted_log_uri: str | None = None,
    ) -> ExecutionResult:
        return cls(
            status="failed",
            exit_code=exit_code,
            phase=phase,
            redacted_log_uri=redacted_log_uri,
            error_code=error_code,
            error_message=error_message,
        )


_RESOURCE_COLUMN_BY_TASK_TYPE = {
    TaskType.EDGE_PROBE: "node_id",
    TaskType.EDGE_DEPLOY: "deployment_service_id",
    TaskType.EDGE_STOP_DEPLOYMENT: "deployment_service_id",
    TaskType.EDGE_ROLLBACK: "deployment_service_id",
    TaskType.EDGE_TRAIN: "training_job_id",
    TaskType.EDGE_STOP_TRAINING: "training_job_id",
    TaskType.EDGE_RESUME_TRAINING: "training_job_id",
}


class RemoteExecutionRepository:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def load(self, execution_id: str) -> RemoteExecution | None:
        with self._session_factory() as session:
            return session.get(RemoteExecution, execution_id)

    def load_for_command(self, command: TaskCommand) -> RemoteExecution | None:
        task_type = TaskType(command.task_type)
        resource_column = _RESOURCE_COLUMN_BY_TASK_TYPE[task_type]
        resource_id = command.resource_refs[resource_column]
        statement = select(RemoteExecution).where(RemoteExecution.task_id == command.task_id)
        statement = statement.where(getattr(RemoteExecution, resource_column) == resource_id)
        with self._session_factory() as session:
            executions = list(session.scalars(statement.limit(2)))
        if len(executions) > 1:
            raise RemoteExecutionStateError("edge command resolves to multiple remote executions")
        return executions[0] if executions else None

    def claim(
        self,
        execution_id: str,
        *,
        reclaim_running: bool = False,
    ) -> RemoteExecution | None:
        claimable = ["queued"]
        if reclaim_running:
            claimable.append("running")
        now = datetime.now(UTC)
        statement = (
            update(RemoteExecution)
            .where(
                RemoteExecution.id == execution_id,
                RemoteExecution.status.in_(claimable),
            )
            .values(
                status="running",
                phase="dispatching",
                started_at=now,
                finished_at=None,
                error_code=None,
                error_message=None,
            )
        )
        with self._session_factory() as session:
            result = session.execute(statement)
            if result.rowcount != 1:
                session.rollback()
                return None
            session.commit()
            return session.get(RemoteExecution, execution_id)

    def finalize(self, execution_id: str, result: ExecutionResult) -> RemoteExecution:
        if result.status not in TERMINAL_EXECUTION_STATUSES:
            raise ValueError("remote execution result must be terminal")
        values = {
            "status": result.status,
            "phase": result.phase,
            "exit_code": result.exit_code,
            "redacted_log_uri": redact_uri(result.redacted_log_uri),
            "error_code": _safe_error_code(result.error_code),
            "error_message": redact(result.error_message) if result.error_message else None,
            "finished_at": datetime.now(UTC),
        }
        statement = (
            update(RemoteExecution)
            .where(RemoteExecution.id == execution_id, RemoteExecution.status == "running")
            .values(**values)
        )
        with self._session_factory() as session:
            update_result = session.execute(statement)
            if update_result.rowcount == 1:
                session.commit()
                execution = session.get(RemoteExecution, execution_id)
                assert execution is not None
                return execution
            session.rollback()
            execution = session.get(RemoteExecution, execution_id)
            if execution is None:
                raise RemoteExecutionNotFoundError("remote execution was not found")
            if execution.status in TERMINAL_EXECUTION_STATUSES:
                return execution
            raise RemoteExecutionStateError("remote execution is not running")

    def list_reconcilable(self) -> list[RemoteExecution]:
        statement = (
            select(RemoteExecution)
            .where(RemoteExecution.status == "running")
            .order_by(RemoteExecution.started_at, RemoteExecution.id)
        )
        with self._session_factory() as session:
            return list(session.scalars(statement))


def _safe_error_code(value: str | None) -> str | None:
    if value is None:
        return None
    sanitized = re_sub_error_code(value)
    return sanitized[:80] or "EDGE_OPERATION_FAILED"


def re_sub_error_code(value: str) -> str:
    return "".join(character if character.isalnum() or character == "_" else "_" for character in value.upper())
