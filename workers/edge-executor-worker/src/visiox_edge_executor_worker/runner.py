from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
import logging
import os
import socket
from typing import Any, Protocol

from redis.asyncio import Redis
from redis.asyncio import from_url as redis_from_url
from redis.exceptions import ResponseError

from visiox_common.settings import Settings, get_settings
from visiox_common.tasks import TaskCommand
from visiox_db.models import RemoteExecution
from visiox_db.session import create_session_factory

from .bootstrap_server import BootstrapServer
from .startup import EdgeExecutorSecurityContext, initialize_security
from .state import (
    ExecutionResult,
    RemoteExecutionNotFoundError,
    RemoteExecutionRepository,
    TERMINAL_EXECUTION_STATUSES,
)


LOGGER = logging.getLogger(__name__)
CONSUMER_GROUP = "edge-executor-workers"
DEFAULT_RECLAIM_IDLE_MS = 60_000


class DockerLabels:
    MANAGED = "com.visiox.managed"
    REMOTE_EXECUTION_ID = "com.visiox.remote-execution-id"
    NODE_ID = "com.visiox.node-id"
    RESOURCE_TYPE = "com.visiox.resource-type"
    RESOURCE_ID = "com.visiox.resource-id"


class EdgeOperationHandler(Protocol):
    def execute(self, execution: RemoteExecution) -> ExecutionResult: ...

    def reconcile(
        self,
        execution: RemoteExecution,
        docker_labels: dict[str, str],
    ) -> ExecutionResult | None: ...


class ExecutionRepository(Protocol):
    def load(self, execution_id: str) -> RemoteExecution | None: ...

    def load_for_command(self, command: TaskCommand) -> RemoteExecution | None: ...

    def claim(
        self,
        execution_id: str,
        *,
        reclaim_running: bool = False,
    ) -> RemoteExecution | None: ...

    def finalize(self, execution_id: str, result: ExecutionResult) -> RemoteExecution: ...

    def list_reconcilable(self) -> list[RemoteExecution]: ...


@dataclass(frozen=True)
class DispatchOutcome:
    execution_id: str
    status: str
    durable_terminal: bool


def docker_labels_for_execution(execution: RemoteExecution) -> dict[str, str]:
    labels = {
        DockerLabels.MANAGED: "true",
        DockerLabels.REMOTE_EXECUTION_ID: execution.id,
        DockerLabels.NODE_ID: execution.node_id,
    }
    if execution.resource_type is not None:
        labels[DockerLabels.RESOURCE_TYPE] = execution.resource_type
    if execution.resource_id is not None:
        labels[DockerLabels.RESOURCE_ID] = execution.resource_id
    return labels


class EdgeExecutionDispatcher:
    def __init__(
        self,
        repository: ExecutionRepository,
        handlers: Mapping[str, EdgeOperationHandler],
    ) -> None:
        self._repository = repository
        self._handlers = dict(handlers)

    def dispatch(
        self,
        command: TaskCommand,
        *,
        reclaim_running: bool = False,
    ) -> DispatchOutcome:
        execution = self._load_execution(command)
        if execution is None:
            raise RemoteExecutionNotFoundError("remote execution was not found")
        if execution.status in TERMINAL_EXECUTION_STATUSES:
            return DispatchOutcome(execution.id, execution.status, True)

        claimed = self._repository.claim(
            execution.id,
            reclaim_running=reclaim_running,
        )
        if claimed is None:
            return DispatchOutcome(execution.id, execution.status, False)

        handler = self._handlers.get(claimed.operation)
        if handler is None:
            result = ExecutionResult.failed(
                error_code="UNSUPPORTED_OPERATION",
                error_message="Edge operation is not available",
                phase="dispatch",
            )
        else:
            try:
                result = handler.execute(claimed)
            except Exception:
                LOGGER.error("edge operation handler failed; details were suppressed")
                result = ExecutionResult.failed(
                    error_code="EDGE_OPERATION_FAILED",
                    error_message="Edge operation failed",
                    phase="dispatch",
                )
        finalized = self._repository.finalize(claimed.id, result)
        return DispatchOutcome(
            finalized.id,
            finalized.status,
            finalized.status in TERMINAL_EXECUTION_STATUSES,
        )

    def reconcile_startup(self) -> int:
        finalized_count = 0
        for execution in self._repository.list_reconcilable():
            handler = self._handlers.get(execution.operation)
            if handler is None:
                continue
            try:
                result = handler.reconcile(
                    execution,
                    docker_labels_for_execution(execution),
                )
            except Exception:
                LOGGER.error("edge startup reconciliation failed; details were suppressed")
                continue
            if result is None:
                continue
            finalized = self._repository.finalize(execution.id, result)
            if finalized.status in TERMINAL_EXECUTION_STATUSES:
                finalized_count += 1
        return finalized_count

    def _load_execution(self, command: TaskCommand) -> RemoteExecution | None:
        execution_id = command.resource_refs.get("remote_execution_id")
        if execution_id is not None:
            return self._repository.load(execution_id)
        return self._repository.load_for_command(command)


def decode_task_command(fields: Mapping[Any, Any]) -> TaskCommand:
    decoded = {_decode_text(key): _decode_text(value) for key, value in fields.items()}
    return TaskCommand.model_validate(
        {
            "task_id": decoded["task_id"],
            "task_type": decoded["task_type"],
            "resource_refs": json.loads(decoded["resource_refs"]),
            "payload": json.loads(decoded["payload"]),
            "payload_version": int(decoded["payload_version"]),
        }
    )


class EdgeExecutorQueue:
    def __init__(
        self,
        redis_client: Any,
        dispatcher: EdgeExecutionDispatcher,
        *,
        stream_name: str,
        consumer_name: str,
        reclaim_idle_ms: int = DEFAULT_RECLAIM_IDLE_MS,
    ) -> None:
        self._redis = redis_client
        self._dispatcher = dispatcher
        self._stream_name = stream_name
        self._consumer_name = consumer_name
        self._reclaim_idle_ms = reclaim_idle_ms
        self._reclaim_cursor = "0-0"
        self._stopping = asyncio.Event()

    async def ensure_consumer_group(self) -> None:
        try:
            await self._redis.xgroup_create(
                self._stream_name,
                CONSUMER_GROUP,
                id="0-0",
                mkstream=True,
            )
        except ResponseError as error:
            if "BUSYGROUP" not in str(error):
                raise

    async def process_message(
        self,
        message_id: str | bytes,
        fields: Mapping[Any, Any],
        *,
        reclaimed: bool = False,
    ) -> bool:
        normalized_message_id = _decode_text(message_id)
        try:
            command = decode_task_command(fields)
            outcome = self._dispatcher.dispatch(
                command,
                reclaim_running=reclaimed,
            )
        except Exception:
            LOGGER.error("edge command remains pending after dispatch failure; details were suppressed")
            return False
        if not outcome.durable_terminal:
            return False
        await self._redis.xack(
            self._stream_name,
            CONSUMER_GROUP,
            normalized_message_id,
        )
        return True

    async def run_once(self, *, count: int = 10, block_ms: int = 1000) -> int:
        processed = 0
        reclaimed = await self._redis.xautoclaim(
            self._stream_name,
            CONSUMER_GROUP,
            self._consumer_name,
            self._reclaim_idle_ms,
            self._reclaim_cursor,
            count=count,
        )
        self._reclaim_cursor = _decode_text(reclaimed[0])
        reclaimed_messages = reclaimed[1]
        for message_id, fields in reclaimed_messages:
            processed += int(
                await self.process_message(message_id, fields, reclaimed=True)
            )
        if processed >= count:
            return processed

        batches = await self._redis.xreadgroup(
            CONSUMER_GROUP,
            self._consumer_name,
            {self._stream_name: ">"},
            count=count - processed,
            block=block_ms,
        )
        for _, messages in batches:
            for message_id, fields in messages:
                processed += int(await self.process_message(message_id, fields))
        return processed

    async def run_forever(self) -> None:
        await self.ensure_consumer_group()
        while not self._stopping.is_set():
            await self.run_once()

    def stop(self) -> None:
        self._stopping.set()


@dataclass
class EdgeExecutorApplication:
    dispatcher: EdgeExecutionDispatcher
    queue: EdgeExecutorQueue
    bootstrap_server: BootstrapServer
    redis_client: Any

    async def run(self) -> None:
        self.dispatcher.reconcile_startup()
        await self.queue.ensure_consumer_group()
        server_task = asyncio.create_task(asyncio.to_thread(self.bootstrap_server.serve_forever))
        queue_task = asyncio.create_task(self.queue.run_forever())
        tasks = {server_task, queue_task}
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            self.queue.stop()
            self.bootstrap_server.stop()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            close = getattr(self.redis_client, "aclose", None)
            if close is not None:
                await close()


def build_application(
    settings: Settings,
    *,
    handlers: Mapping[str, EdgeOperationHandler] | None = None,
    redis_factory: Callable[[str], Any] = redis_from_url,
    server_factory: Callable[..., BootstrapServer] = BootstrapServer,
) -> EdgeExecutorApplication:
    security: EdgeExecutorSecurityContext = initialize_security(settings)
    session_factory = create_session_factory()
    repository = RemoteExecutionRepository(session_factory)
    dispatcher = EdgeExecutionDispatcher(repository, handlers or {})
    redis_client: Redis = redis_factory(settings.redis_url)
    queue = EdgeExecutorQueue(
        redis_client,
        dispatcher,
        stream_name=settings.edge_executor_stream,
        consumer_name=_consumer_name(),
    )
    bootstrap_server = server_factory(
        settings,
        session_factory,
        security_context=security,
    )
    return EdgeExecutorApplication(
        dispatcher=dispatcher,
        queue=queue,
        bootstrap_server=bootstrap_server,
        redis_client=redis_client,
    )


def _consumer_name() -> str:
    return f"{socket.gethostname()}-{os.getpid()}"


def _decode_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    application = build_application(get_settings())
    asyncio.run(application.run())


if __name__ == "__main__":
    main()
