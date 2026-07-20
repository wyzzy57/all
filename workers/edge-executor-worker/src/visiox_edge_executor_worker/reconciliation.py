from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
from pathlib import PurePosixPath
import re
import shlex
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from visiox_db.models import EdgeSshCredential, RemoteExecution

from .crypto import EncryptedSecret
from .scripts import load_packaged_script
from .startup import EdgeExecutorSecurityContext
from .state import ExecutionResult, RemoteExecutionRepository


INSPECTION_TIMEOUT_SECONDS = 30.0
_SCRIPT_NAME = "inspect_runtime.sh"
_REQUEST_NAME = "request.json"
_SAFE_LABEL_VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}\Z")
_CONTAINER_ID = re.compile(r"[0-9a-f]{12,64}\Z")
_CONTAINER_STATUSES = frozenset({"created", "restarting", "running", "removing", "paused", "exited", "dead"})
_HEALTH_STATUSES = frozenset({"starting", "healthy", "unhealthy"})


class DockerLabels:
    MANAGED = "com.visiox.managed"
    REMOTE_EXECUTION_ID = "com.visiox.remote-execution-id"
    NODE_ID = "com.visiox.node-id"
    RESOURCE_TYPE = "com.visiox.resource-type"
    RESOURCE_ID = "com.visiox.resource-id"


class _CredentialUnavailableError(LookupError):
    pass


@dataclass(frozen=True)
class _Recovery:
    phase: str
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class _SshTarget:
    host: str
    port: int
    username: str
    expected_fingerprint: str
    private_key: bytes


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
    for value in labels.values():
        if not _SAFE_LABEL_VALUE.fullmatch(value):
            raise ValueError("remote execution has an invalid Docker label value")
    return labels


class RemoteRuntimeReconciler:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        repository: RemoteExecutionRepository,
        security: EdgeExecutorSecurityContext,
    ) -> None:
        self._session_factory = session_factory
        self._repository = repository
        self._security = security
        self._script = load_packaged_script(_SCRIPT_NAME)

    def reconcile_startup(self) -> int:
        reconciled = 0
        for execution in self._repository.list_reconcilable():
            decision = self._reconcile(execution)
            if isinstance(decision, ExecutionResult):
                self._repository.finalize(execution.id, decision)
            else:
                self._repository.recover(
                    execution.id,
                    phase=decision.phase,
                    error_code=decision.error_code,
                    error_message=decision.error_message,
                )
            reconciled += 1
        return reconciled

    def _reconcile(self, execution: RemoteExecution) -> ExecutionResult | _Recovery:
        try:
            target = self._load_target(execution.node_id)
        except _CredentialUnavailableError:
            return ExecutionResult.failed(
                error_code="EDGE_CREDENTIAL_UNAVAILABLE",
                error_message="Edge SSH credential is unavailable",
                phase="reconciliation_failed",
            )
        except Exception:
            return _Recovery(
                phase="reconciliation_retry",
                error_code="REMOTE_INSPECTION_RETRY",
                error_message="Remote runtime inspection must be retried",
            )
        try:
            response = self._inspect(target, docker_labels_for_execution(execution))
            containers = _validate_response(response)
        except Exception:
            return _Recovery(
                phase="reconciliation_retry",
                error_code="REMOTE_INSPECTION_RETRY",
                error_message="Remote runtime inspection must be retried",
            )
        if len(containers) > 1:
            return ExecutionResult.failed(
                error_code="REMOTE_STATE_AMBIGUOUS",
                error_message="Multiple remote runtimes matched one execution",
                phase="reconciliation_failed",
            )
        if not containers:
            return _Recovery(phase="reconciliation_pending")
        container = containers[0]
        if container["status"] == "dead" or (
            container["status"] == "exited"
            and (container["exit_code"] != 0 or container["oom_killed"])
        ):
            return ExecutionResult.failed(
                error_code="REMOTE_RUNTIME_FAILED",
                error_message="Remote runtime exited unsuccessfully",
                exit_code=container["exit_code"],
                phase="reconciliation_failed",
            )
        return _Recovery(phase="reconciled")

    def _load_target(self, node_id: str) -> _SshTarget:
        with self._session_factory() as session:
            credential = session.scalar(
                select(EdgeSshCredential).where(EdgeSshCredential.node_id == node_id)
            )
            if credential is None:
                raise _CredentialUnavailableError("edge SSH credential is unavailable")
            private_key = self._security.credential_cipher.decrypt(
                EncryptedSecret(
                    ciphertext=credential.encrypted_private_key,
                    nonce=credential.encryption_nonce,
                    key_version=credential.key_version,
                )
            )
            return _SshTarget(
                host=credential.ssh_host,
                port=credential.ssh_port,
                username=credential.ssh_user,
                expected_fingerprint=credential.host_key_fingerprint,
                private_key=private_key,
            )

    def _inspect(self, target: _SshTarget, labels: dict[str, str]) -> dict[str, Any]:
        ssh_session = self._security.ssh_client.connect(
            host=target.host,
            port=target.port,
            username=target.username,
            private_key=target.private_key,
            expected_fingerprint=target.expected_fingerprint,
            timeout_seconds=INSPECTION_TIMEOUT_SECONDS,
        )
        try:
            workspace = ssh_session.create_private_directory(
                timeout_seconds=INSPECTION_TIMEOUT_SECONDS
            )
            script_path = str(PurePosixPath(workspace.path) / _SCRIPT_NAME)
            request_path = str(PurePosixPath(workspace.path) / _REQUEST_NAME)
            remote_paths = (script_path, request_path)
            request = json.dumps(
                {"labels": labels},
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("ascii")
            try:
                for data, path in ((self._script, script_path), (request, request_path)):
                    ssh_session.upload_bytes_exclusive(
                        data,
                        path,
                        expected_owner_uid=workspace.owner_uid,
                        timeout_seconds=INSPECTION_TIMEOUT_SECONDS,
                    )
                    ssh_session.validate_remote_file(
                        path,
                        expected_owner_uid=workspace.owner_uid,
                        timeout_seconds=INSPECTION_TIMEOUT_SECONDS,
                    )
                result = ssh_session.run(
                    f"/bin/bash {shlex.quote(script_path)} {shlex.quote(request_path)}",
                    timeout_seconds=INSPECTION_TIMEOUT_SECONDS,
                )
                if result.exit_status != 0:
                    raise RuntimeError("remote runtime inspection failed")
                return json.loads(result.stdout)
            finally:
                ssh_session.cleanup_private_directory(
                    workspace,
                    remote_paths,
                    timeout_seconds=INSPECTION_TIMEOUT_SECONDS,
                )
        finally:
            ssh_session.close()


def _validate_response(response: object) -> list[dict[str, Any]]:
    if not isinstance(response, dict) or set(response) != {"containers"}:
        raise ValueError("remote runtime inspection response is invalid")
    containers = response["containers"]
    if not isinstance(containers, list) or len(containers) > 2:
        raise ValueError("remote runtime inspection response is invalid")
    validated: list[dict[str, Any]] = []
    for container in containers:
        if not isinstance(container, dict) or set(container) != {
            "id",
            "status",
            "exit_code",
            "oom_killed",
            "health",
        }:
            raise ValueError("remote runtime inspection response is invalid")
        if not isinstance(container["id"], str) or not _CONTAINER_ID.fullmatch(container["id"]):
            raise ValueError("remote runtime inspection response is invalid")
        if container["status"] not in _CONTAINER_STATUSES:
            raise ValueError("remote runtime inspection response is invalid")
        if not isinstance(container["exit_code"], int) or isinstance(container["exit_code"], bool):
            raise ValueError("remote runtime inspection response is invalid")
        if not isinstance(container["oom_killed"], bool):
            raise ValueError("remote runtime inspection response is invalid")
        if container["health"] is not None and container["health"] not in _HEALTH_STATUSES:
            raise ValueError("remote runtime inspection response is invalid")
        validated.append(container)
    return validated
