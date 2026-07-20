from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
import logging
import os
import signal
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
from .reconciliation import RemoteRuntimeReconciler
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
RETRY_ATTEMPTS = 5
RETRY_INITIAL_SECONDS = 0.25
RETRY_MAX_SECONDS = 4.0


class EdgeOperationHandler(Protocol):
    def execute(self, execution: RemoteExecution) -> ExecutionResult: ...


class ExecutionRepository(Protocol):
    def load(self, execution_id: str) -> RemoteExecution | None: ...

    def claim(
        self,
        execution_id: str,
        *,
        reclaim_running: bool = False,
    ) -> RemoteExecution | None: ...

    def finalize(self, execution_id: str, result: ExecutionResult) -> RemoteExecution: ...


@dataclass(frozen=True)
class DispatchOutcome:
    execution_id: str
    status: str
    durable_terminal: bool


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

    def _load_execution(self, command: TaskCommand) -> RemoteExecution | None:
        return self._repository.load(command.resource_refs["remote_execution_id"])


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
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self._redis = redis_client
        self._dispatcher = dispatcher
        self._stream_name = stream_name
        self._consumer_name = consumer_name
        self._reclaim_idle_ms = reclaim_idle_ms
        self._reclaim_cursor = "0-0"
        self._stopping = asyncio.Event()
        self._sleep = sleep

    async def ensure_consumer_group(self) -> None:
        delay = RETRY_INITIAL_SECONDS
        for attempt in range(RETRY_ATTEMPTS):
            try:
                await self._redis.xgroup_create(
                    self._stream_name,
                    CONSUMER_GROUP,
                    id="0-0",
                    mkstream=True,
                )
                return
            except ResponseError as error:
                if "BUSYGROUP" in str(error):
                    return
                if attempt == RETRY_ATTEMPTS - 1:
                    raise
            except Exception:
                if attempt == RETRY_ATTEMPTS - 1:
                    raise
            LOGGER.error("Redis consumer group is not ready; details were suppressed")
            await self._sleep(delay)
            delay = min(delay * 2, RETRY_MAX_SECONDS)

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

    async def run_forever(self, *, group_ready: bool = False) -> None:
        if not group_ready:
            await self.ensure_consumer_group()
        delay = RETRY_INITIAL_SECONDS
        while not self._stopping.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.error("edge queue poll failed; details were suppressed")
                await self._sleep(delay)
                delay = min(delay * 2, RETRY_MAX_SECONDS)
            else:
                delay = RETRY_INITIAL_SECONDS

    def stop(self) -> None:
        self._stopping.set()


@dataclass
class EdgeExecutorApplication:
    dispatcher: EdgeExecutionDispatcher
    reconciler: RemoteRuntimeReconciler
    queue: EdgeExecutorQueue
    bootstrap_server: BootstrapServer
    redis_client: Any
    sleep: Callable[[float], Any] = asyncio.sleep

    async def run(self) -> None:
        await self._reconcile_with_retry()
        await self.queue.ensure_consumer_group()
        server_task = asyncio.create_task(asyncio.to_thread(self.bootstrap_server.serve_forever))
        queue_task = asyncio.create_task(self.queue.run_forever(group_ready=True))
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

    def request_shutdown(self) -> None:
        self.queue.stop()
        self.bootstrap_server.stop()

    async def _reconcile_with_retry(self) -> None:
        delay = RETRY_INITIAL_SECONDS
        for attempt in range(RETRY_ATTEMPTS):
            try:
                await asyncio.to_thread(self.reconciler.reconcile_startup)
                return
            except Exception:
                if attempt == RETRY_ATTEMPTS - 1:
                    raise
                LOGGER.error("database reconciliation is not ready; details were suppressed")
                await self.sleep(delay)
                delay = min(delay * 2, RETRY_MAX_SECONDS)


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
    reconciler = RemoteRuntimeReconciler(session_factory, repository, security)
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
        reconciler=reconciler,
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


def install_signal_handlers(
    loop: asyncio.AbstractEventLoop,
    application: EdgeExecutorApplication,
) -> None:
    for signal_number in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(signal_number, application.request_shutdown)
        except NotImplementedError:
            signal.signal(
                signal_number,
                lambda *_args, app=application: loop.call_soon_threadsafe(
                    app.request_shutdown
                ),
            )


async def _run_main() -> None:
    application = build_application(get_settings())
    install_signal_handlers(asyncio.get_running_loop(), application)
    await application.run()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_run_main())


if __name__ == "__main__":
    main()
