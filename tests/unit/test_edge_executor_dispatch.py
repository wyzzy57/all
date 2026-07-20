from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
import signal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
import yaml

from visiox_common.settings import Settings
from visiox_common.tasks import TaskCommand, TaskType
from visiox_db.base import Base
from visiox_db.models import RemoteExecution
from visiox_db.session import create_session_factory
from visiox_edge_executor_worker.runner import (
    CONSUMER_GROUP,
    EdgeExecutionDispatcher,
    EdgeExecutorQueue,
    build_application,
    decode_task_command,
    install_signal_handlers,
)
from visiox_edge_executor_worker.state import (
    ExecutionResult,
    RemoteExecutionRepository,
)


REPOSITORY_ROOT = Path(__file__).parents[2]


class RecordingRepository:
    def __init__(self, execution=None) -> None:
        self.execution = execution or SimpleNamespace(
            id="exec-1",
            operation="deploy",
            status="queued",
            node_id="node-1",
            resource_type="deployment_service",
            resource_id="service-1",
        )
        self.loaded_ids: list[str] = []
        self.reclaim_flags: list[bool] = []
        self.finalized: list[tuple[str, ExecutionResult]] = []

    def load(self, execution_id: str):
        self.loaded_ids.append(execution_id)
        return self.execution

    def load_for_command(self, command):
        raise AssertionError("dispatcher must use the remote execution identifier")

    def claim(self, execution_id: str, *, reclaim_running: bool = False):
        self.reclaim_flags.append(reclaim_running)
        self.execution.status = "running"
        return self.execution

    def finalize(self, execution_id: str, result: ExecutionResult):
        self.finalized.append((execution_id, result))
        self.execution.status = result.status
        return self.execution

    def list_reconcilable(self):
        return [self.execution]


class SuccessfulHandler:
    def __init__(self) -> None:
        self.executed_ids: list[str] = []

    def execute(self, execution) -> ExecutionResult:
        self.executed_ids.append(execution.id)
        return ExecutionResult.succeeded(exit_code=0, phase="complete")



class FakeRedis:
    def __init__(self) -> None:
        self.acked: list[tuple[str, str, str]] = []

    async def xack(self, stream: str, group: str, message_id: str) -> int:
        self.acked.append((stream, group, message_id))
        return 1


def _stream_fields() -> dict[str, str]:
    return TaskCommand(
        task_id="task-1",
        task_type=TaskType.EDGE_PROBE,
        resource_refs={"remote_execution_id": "exec-1"},
    ).to_stream_fields()


def test_dispatch_loads_operation_by_id_not_payload() -> None:
    repository = RecordingRepository()
    handler = SuccessfulHandler()
    dispatcher = EdgeExecutionDispatcher(repository, {"deploy": handler})
    command = decode_task_command(
        TaskCommand(
            task_id="task-1",
            task_type=TaskType.EDGE_DEPLOY,
            resource_refs={"remote_execution_id": "exec-1"},
        ).to_stream_fields()
    )

    outcome = dispatcher.dispatch(command)

    assert repository.loaded_ids == ["exec-1"]
    assert handler.executed_ids == ["exec-1"]
    assert outcome.durable_terminal is True


def test_dispatch_failure_does_not_log_or_persist_exception_secrets(caplog) -> None:
    repository = RecordingRepository()

    class FailingHandler:
        def execute(self, execution):
            raise RuntimeError("Authorization: Bearer secret password=secret")


    dispatcher = EdgeExecutionDispatcher(repository, {"deploy": FailingHandler()})
    command = decode_task_command(
        TaskCommand(
            task_id="task-1",
            task_type=TaskType.EDGE_DEPLOY,
            resource_refs={"remote_execution_id": "exec-1"},
        ).to_stream_fields()
    )

    with caplog.at_level(logging.ERROR):
        outcome = dispatcher.dispatch(command)

    assert outcome.durable_terminal is True
    assert "secret" not in caplog.text.casefold()
    assert repository.finalized[0][1].error_message == "Edge operation failed"


def test_repository_claim_and_finalize_are_atomic_and_idempotent() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        session.add(
            RemoteExecution(
                id="exec-1",
                node_id="node-1",
                operation="deploy",
                idempotency_key="deploy-service-1-attempt-1",
            )
        )
        session.commit()
    repository = RemoteExecutionRepository(session_factory)

    claimed = repository.claim("exec-1")
    duplicate_claim = repository.claim("exec-1")
    completed = repository.finalize(
        "exec-1",
        ExecutionResult.succeeded(
            exit_code=0,
            phase="complete",
            redacted_log_uri="https://logs.test/run.log?X-Amz-Signature=secret",
        ),
    )
    repeated = repository.finalize(
        "exec-1",
        ExecutionResult.failed(error_code="LATE_FAILURE", error_message="password=secret"),
    )

    assert claimed is not None
    assert claimed.status == "running"
    assert duplicate_claim is None
    assert completed.status == "succeeded"
    assert completed.exit_code == 0
    assert completed.redacted_log_uri == "https://logs.test/run.log"
    assert repeated.status == "succeeded"
    assert repeated.error_code is None


def test_queue_acknowledges_only_after_durable_terminal_state() -> None:
    redis_client = FakeRedis()
    outcomes = iter([False, True])

    class Dispatcher:
        def dispatch(self, command, *, reclaim_running: bool = False):
            return SimpleNamespace(durable_terminal=next(outcomes))

    queue = EdgeExecutorQueue(redis_client, Dispatcher(), stream_name="edge-stream", consumer_name="worker-1")

    assert asyncio.run(queue.process_message("1-0", _stream_fields())) is False
    assert redis_client.acked == []
    assert asyncio.run(queue.process_message("1-0", _stream_fields())) is True
    assert redis_client.acked == [("edge-stream", CONSUMER_GROUP, "1-0")]


def test_reclaimed_pending_message_can_reclaim_running_execution() -> None:
    redis_client = FakeRedis()
    reclaim_flags: list[bool] = []

    class Dispatcher:
        def dispatch(self, command, *, reclaim_running: bool = False):
            reclaim_flags.append(reclaim_running)
            return SimpleNamespace(durable_terminal=True)

    queue = EdgeExecutorQueue(redis_client, Dispatcher(), stream_name="edge-stream", consumer_name="worker-1")

    asyncio.run(queue.process_message("2-0", _stream_fields(), reclaimed=True))

    assert reclaim_flags == [True]
    assert redis_client.acked == [("edge-stream", CONSUMER_GROUP, "2-0")]


def test_pending_reclaim_scan_continues_from_redis_cursor() -> None:
    class CursorRedis(FakeRedis):
        def __init__(self) -> None:
            super().__init__()
            self.start_ids: list[str] = []

        async def xautoclaim(
            self,
            stream,
            group,
            consumer,
            idle_ms,
            start_id,
            *,
            count,
        ):
            self.start_ids.append(start_id)
            next_cursor = b"5-0" if len(self.start_ids) == 1 else b"0-0"
            return next_cursor, [], []

        async def xreadgroup(self, group, consumer, streams, *, count, block):
            return []

    class Dispatcher:
        def dispatch(self, command, *, reclaim_running: bool = False):
            return SimpleNamespace(durable_terminal=True)

    redis_client = CursorRedis()
    queue = EdgeExecutorQueue(redis_client, Dispatcher(), stream_name="edge-stream", consumer_name="worker-1")

    asyncio.run(queue.run_once(block_ms=1))
    asyncio.run(queue.run_once(block_ms=1))

    assert redis_client.start_ids == ["0-0", "5-0"]


def test_consumer_group_creation_retries_with_bounded_exponential_backoff() -> None:
    class StartingRedis(FakeRedis):
        def __init__(self) -> None:
            super().__init__()
            self.attempts = 0

        async def xgroup_create(self, *args, **kwargs):
            self.attempts += 1
            if self.attempts < 3:
                raise ConnectionError("redis password=secret")

    sleeps: list[float] = []

    async def record_sleep(delay: float) -> None:
        sleeps.append(delay)

    redis_client = StartingRedis()
    queue = EdgeExecutorQueue(
        redis_client,
        SimpleNamespace(),
        stream_name="edge-stream",
        consumer_name="worker-1",
        sleep=record_sleep,
    )

    asyncio.run(queue.ensure_consumer_group())

    assert redis_client.attempts == 3
    assert sleeps == [0.25, 0.5]


def test_task_loop_recovers_after_transient_redis_failure() -> None:
    sleeps: list[float] = []

    async def record_sleep(delay: float) -> None:
        sleeps.append(delay)

    queue = EdgeExecutorQueue(
        FakeRedis(),
        SimpleNamespace(),
        stream_name="edge-stream",
        consumer_name="worker-1",
        sleep=record_sleep,
    )
    attempts = 0

    async def run_once(*, count: int = 10, block_ms: int = 1000) -> int:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionError("redis token=secret")
        queue.stop()
        return 0

    queue.run_once = run_once

    asyncio.run(queue.run_forever(group_ready=True))

    assert attempts == 2
    assert sleeps == [0.25]


def test_signal_callbacks_request_shutdown() -> None:
    callbacks = {}

    class Loop:
        def add_signal_handler(self, signal_number, callback):
            callbacks[signal_number] = callback

    class Application:
        def __init__(self) -> None:
            self.shutdown_requests = 0

        def request_shutdown(self) -> None:
            self.shutdown_requests += 1

    application = Application()

    install_signal_handlers(Loop(), application)
    callbacks[signal.SIGTERM]()
    callbacks[signal.SIGINT]()

    assert application.shutdown_requests == 2


def test_application_cleanup_stops_queue_socket_and_redis() -> None:
    events: list[str] = []

    class Reconciler:
        def reconcile_startup(self):
            events.append("reconcile")

    class Queue:
        async def ensure_consumer_group(self):
            events.append("group")

        async def run_forever(self, *, group_ready=False):
            events.append(f"queue:{group_ready}")

        def stop(self):
            events.append("queue-stop")

    class Server:
        def serve_forever(self):
            events.append("server")

        def stop(self):
            events.append("server-stop")

    class Redis:
        async def aclose(self):
            events.append("redis-close")

    from visiox_edge_executor_worker.runner import EdgeExecutorApplication

    application = EdgeExecutorApplication(
        dispatcher=SimpleNamespace(),
        reconciler=Reconciler(),
        queue=Queue(),
        bootstrap_server=Server(),
        redis_client=Redis(),
    )

    asyncio.run(application.run())

    assert "reconcile" in events
    assert "group" in events
    assert "queue:True" in events
    assert "queue-stop" in events
    assert "server-stop" in events
    assert "redis-close" in events


def test_application_retries_database_reconciliation_before_starting_listeners() -> None:
    events: list[str] = []

    class Reconciler:
        def __init__(self) -> None:
            self.attempts = 0

        def reconcile_startup(self):
            self.attempts += 1
            events.append(f"db:{self.attempts}")
            if self.attempts < 3:
                raise ConnectionError("postgres password=secret")

    class Queue:
        async def ensure_consumer_group(self):
            events.append("group")

        async def run_forever(self, *, group_ready=False):
            events.append("queue")

        def stop(self):
            pass

    class Server:
        def serve_forever(self):
            events.append("server")

        def stop(self):
            pass

    class Redis:
        async def aclose(self):
            pass

    async def record_sleep(delay: float) -> None:
        events.append(f"sleep:{delay}")

    from visiox_edge_executor_worker.runner import EdgeExecutorApplication

    application = EdgeExecutorApplication(
        dispatcher=SimpleNamespace(),
        reconciler=Reconciler(),
        queue=Queue(),
        bootstrap_server=Server(),
        redis_client=Redis(),
        sleep=record_sleep,
    )

    asyncio.run(application.run())

    assert events[:5] == ["db:1", "sleep:0.25", "db:2", "sleep:0.5", "db:3"]
    assert events.index("db:3") < events.index("group")


def test_security_initializes_before_redis_or_socket_runtime_is_created(tmp_path) -> None:
    events: list[str] = []
    settings = Settings(
        _env_file=None,
        edge_credential_master_key_file=tmp_path / "missing-master-key",
    )

    def redis_factory(url: str):
        events.append("redis")
        raise AssertionError("Redis must not be created before security succeeds")

    def server_factory(*args, **kwargs):
        events.append("server")
        raise AssertionError("Socket server must not be created before security succeeds")

    with pytest.raises(ValueError, match="unavailable"):
        build_application(
            settings,
            redis_factory=redis_factory,
            server_factory=server_factory,
        )

    assert events == []


def test_stream_fields_decode_through_task_command() -> None:
    fields = _stream_fields()
    command = decode_task_command(fields)
    assert json.loads(fields["resource_refs"]) == {"remote_execution_id": "exec-1"}
    assert command.resource_refs == {"remote_execution_id": "exec-1"}


def test_compose_and_startup_files_keep_edge_master_key_worker_only() -> None:
    compose_path = REPOSITORY_ROOT / "infra" / "compose" / "docker-compose.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    services = compose["services"]
    worker = services["edge-executor-worker"]
    api = services["api-service"]

    assert worker["deploy"]["replicas"] == 1
    assert worker["restart"] == "unless-stopped"
    assert worker["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert worker["depends_on"]["redis"]["condition"] == "service_healthy"
    assert compose["services"]["postgres"]["healthcheck"]["test"][0] == "CMD-SHELL"
    assert compose["services"]["redis"]["healthcheck"]["test"] == [
        "CMD",
        "redis-cli",
        "ping",
    ]
    assert worker["command"] == ["python", "-m", "visiox_edge_executor_worker.runner"]
    assert worker["environment"]["VISIOX_EDGE_BOOTSTRAP_SOCKET"] == (
        "/run/visiox-edge/edge-bootstrap.sock"
    )
    assert worker["environment"]["VISIOX_EDGE_CREDENTIAL_MASTER_KEY_FILE"] == (
        "/run/secrets/edge_credential_master_key"
    )
    assert worker["secrets"] == [
        {
            "source": "edge_credential_master_key",
            "target": "edge_credential_master_key",
            "mode": 0o400,
        }
    ]
    assert "edge-runtime:/run/visiox-edge" in worker["volumes"]
    assert (
        "../../workers/edge-executor-worker/remote:/app/workers/edge-executor-worker/remote:ro"
        in worker["volumes"]
    )

    assert api["environment"]["VISIOX_EDGE_BOOTSTRAP_SOCKET"] == (
        "/run/visiox-edge/edge-bootstrap.sock"
    )
    assert api.get("secrets", []) == []
    assert "edge-runtime:/run/visiox-edge" in api["volumes"]
    assert all("edge_credential_master_key" not in volume for volume in api["volumes"])
    assert compose["secrets"]["edge_credential_master_key"]["file"] == (
        "../../.local-secrets/edge_credential_master_key"
    )

    gitignore = (REPOSITORY_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".local-secrets/" in gitignore.splitlines()
    script = (REPOSITORY_ROOT / "scripts" / "init-edge-runtime.ps1").read_text(
        encoding="utf-8"
    )
    assert "RandomNumberGenerator" in script
    assert "CreateNew" in script
    assert "WriteAllText" not in script
    assert "Convert]::ToBase64String" not in script
    assert "exit 0" not in script
    assert "SetAccessRuleProtection($true, $false)" in script
    assert "DirectorySecurity" in script
    assert "FileSecurity" in script
    assert "Set-Acl" in script
    assert '"700"' in script
    assert '"600"' in script
    assert "/bin/chmod" in script
