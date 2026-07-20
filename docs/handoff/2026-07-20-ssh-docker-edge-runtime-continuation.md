# Visiox SSH/Docker Edge Runtime Continuation

## Immediate Instruction

Resume in `C:\Users\Administrator\Documents\visiox` on branch
`codex/ssh-docker-edge-runtime`.

Do **not** reset, checkout, clean, stash, or discard the current working-tree
changes. They are an in-progress Task 3 security remediation with intentional
RED tests.

Read these files first:

1. `docs/superpowers/specs/2026-07-20-ssh-docker-edge-runtime-design.md`
2. `docs/superpowers/plans/2026-07-20-ssh-docker-edge-runtime.md`
3. `.superpowers/sdd/progress.md`
4. This handoff document.

Use Python 3.12 through `py -3.12` or
`.\.tools\Python312\python.exe`. The system `python` points to Python 3.10 and
cannot run this project.

## Product Decisions

- Control edge devices through SSH and local Docker, not custom WSS/mTLS Agent commands.
- Keep the old Agent/WSS implementation frozen for compatibility; do not delete it in this branch.
- Support Ubuntu Jetson and x86 NVIDIA hosts on one directly reachable high-speed LAN.
- Platform has no GPU/compute; training and inference execute on edge nodes.
- Use one dedicated `edge-executor-worker` and identifier-only Redis jobs.
- Generate one Ed25519 key pair per node; strict SHA-256 host-key verification is mandatory.
- The one-time administrator password may exist only in the HTTP request, private Unix-socket frame, and worker memory.
- Encrypt private keys with AES-256-GCM using a 32-byte Docker-secret master key available only to the worker.
- Use MinIO for datasets/models/results and the local Registry for immutable images.
- First production loop is YOLO26 object detection plus image/HTTP inference.
- Deployment defaults to PT -> ONNX -> TensorRT FP16, with manual overrides and optional INT8 calibration.
- Distributed training uses Ultralytics/PyTorch `torchrun` with NCCL; one run uses one compatible homogeneous pool only.
- Kubernetes, custom certificate renewal, cross-Internet/NAT control, and video inference are out of scope for this version.

## Git State

Branch: `codex/ssh-docker-edge-runtime`

Current committed HEAD before the partial remediation:

```text
03a0657 feat: bootstrap ssh edge nodes
6d74848 fix: enforce ssh operation deadlines
3788dc1 fix: harden bounded ssh operations
1e9a9cf feat: add encrypted strict ssh client
1dbcbbe fix: close edge task dispatch bypasses
62d452d fix: harden ssh edge task contracts
8d13b0d feat: add ssh edge runtime persistence
0dbaef5 docs: plan ssh docker edge runtime
120fcfd docs: design ssh docker edge runtime
```

The branch has no upstream yet and has not been pushed during this execution.

## Completed and Approved

### Task 1: Persistence and Edge Task Contracts

Approved after three review rounds.

- Added `EdgeSshCredential`, `RemoteExecution`, `DeploymentInstance`, and `DistributedTrainingRun`.
- Added migration `20260720_0001` based on `20260717_0001`.
- Added seven Edge task types routed to `stream:edge_executor.commands`.
- Generic `POST /tasks` rejects every Edge type; dedicated APIs must create Edge commands from real DB resources.
- `TaskCommand.to_stream_fields()` revalidates current dictionaries at the Redis boundary.
- PostgreSQL explicit identifiers stay within 63 characters.
- Evidence: 33 migration/API tests plus 28 final security tests passed; Ruff and diff check passed.

### Task 2: Encryption and Strict SSH

Approved after real Paramiko lifecycle review.

- AES-256-GCM, 12-byte nonce, AAD `visiox-edge-ssh`, exact 32-byte secret.
- Canonical OpenSSH SHA-256 fingerprint and host-key verification before password/private-key authentication.
- Bounded command and SFTP operations use one monotonic deadline.
- Paramiko request waits run behind a bounded daemon helper; timeout closes the complete transport/socket.
- stdout/stderr are continuously drained with a hard 4 MiB maximum.
- Password/private-key/command/path/data never appear in fixed error messages.
- `initialize_security(settings)` reads the secret before constructing the SSH client.
- Evidence: 50 crypto/SSH/startup/settings tests passed; Ruff and diff check passed.

Deferred Task 2 check: Task 5's real runner must call
`initialize_security(settings)` before creating a Redis consumer or accepting
work.

Non-blocking residual risk: a daemon request thread can briefly outlive the
caller until Paramiko reacts to transport close; network resources are closed
before return.

## Task 3: Committed Implementation and Review

Commit `03a0657` added:

- Private AF_UNIX framing and bootstrap server.
- `/edge-nodes/scan-host-key`, `/bootstrap`, `/{id}/test-connection`, and `/{id}/rotate-key`.
- Ed25519 generation, password bootstrap, strict key reconnect, AES-GCM persistence.
- Initial `bootstrap_user.sh` and API/worker tests.

The initial implementation passed 23 focused and 142 regression tests, but the
independent review rejected it for the concrete issues below.

### Blocking Review Findings

1. Repeated bootstrap can replace the current remote key before reconnect/DB commit and lock out the saved credential.
2. Concurrent operations on the same host/node are not serialized and can produce DB/remote key divergence.
3. Fixed predictable `/tmp` script/config paths are vulnerable to pre-creation or replacement.
4. Malformed bootstrap bodies can make FastAPI's automatic 422 body echo the password value.
5. Socket and SSH steps use per-operation timeouts instead of one total request deadline.
6. The private server had no real CLI caller, and the remote script lived outside installed package data.
7. Stale socket cleanup could unlink a regular file, symlink, foreign-owned socket, or a replacement socket.
8. The script assumes `sudo` exists and is passwordless, and does not handle root separately.
9. A same-name old Agent/WSS node can be silently converted into an SSH node.
10. Some no-secret persistence assertions used an unrelated in-memory DB and were not meaningful.

## Current Uncommitted Task 3 Remediation

The security-fix agent was interrupted safely for the account switch. Changes
are **not staged and not committed**.

Modified tracked files:

```text
infra/migrations/versions/20260720_0001_ssh_edge_runtime.py
packages/visiox-db/src/visiox_db/models/edge_compute.py
tests/integration/test_edge_ssh_api.py
tests/integration/test_migrations.py
tests/unit/test_edge_ssh_adapter.py
workers/edge-executor-worker/src/visiox_edge_executor_worker/ssh.py
```

Untracked intentional RED test:

```text
tests/unit/test_edge_bootstrap_hardening.py
```

Already implemented in the uncommitted changes:

- Unique ORM/migration constraint on `(ssh_host, ssh_port)`.
- Bounded SFTP private workspace API with random 128-bit directory names.
- Mode 0700 directories, exclusive `x` file creation, mode 0600 files, regular-file/owner permission validation, and targeted cleanup.
- Caller-provided total timeout for SSH connect and a tighter host-key scan deadline.
- RED tests for duplicate node/host, Agent identity preservation, concurrent serialization, managed-key versions, and fixed password-safe API errors.

Last partial test evidence:

```text
SSH adapter: 29 passed, 2 skipped (POSIX permission semantics skipped on Windows)
unique constraint and migration focused tests: 2 passed
hardening + API RED: 11 failed, 4 passed (expected RED)
```

The 11 failures are expected because the worker/API/script changes below are
not implemented yet.

## Exact Next Implementation Steps

Continue Task 3 with TDD in this order:

1. Run `git status --short` and inspect `tests/unit/test_edge_bootstrap_hardening.py`. Do not revert any current diff.
2. In `bootstrap_server.py`, reject repeated bootstrap for any node that already has a credential; require rotate instead.
3. Reject same-name non-`ssh_edge` nodes and reject any host/port already owned by another node before remote mutation.
4. Add keyed in-process locks for canonical host/port and node ID; clean unused lock entries. DB uniqueness is the final race guard. Task 5 must run one worker replica for the MVP.
5. Replace fixed `/tmp` staging with the new random private SFTP workspace API. Package the script inside `visiox_edge_executor_worker/remote/` and load it through `importlib.resources`.
6. Change `bootstrap_user.sh` to preserve all non-Visiox authorized keys and manage only lines marked `visiox-edge:<node-id>:<version>`.
7. On reconnect or DB commit failure, best-effort remove only the newly added key. Rotation remains add -> verify -> persist -> remove old; cleanup failure reports `cleanup_required` and leaves both keys usable.
8. Handle root directly. For non-root administrators, run a fixed preflight for `command -v sudo` and `sudo -n true`; never send the login password to sudo.
9. Make the bootstrap HTTP endpoint read bounded raw JSON and manually validate it so every 422 response is fixed and never includes Pydantic `input` data or the password.
10. Apply one monotonic 60-second deadline across API framing, worker framing, SSH/SFTP/command steps, compensation, and commit decision.
11. Secure Unix-socket lifecycle: current-UID mode-0700 parent; `lstat` socket/owner checks; refuse active server; record inode/device; unlink on stop only if unchanged.
12. Add a runnable bootstrap-server CLI that calls `initialize_security(settings)` before bind/listen/accept.
13. Replace the unrelated in-memory DB assertion with tests against the worker's actual session factory.
14. Run focused Task 3 tests, Task 2 SSH regressions, migrations, Agent/WSS regressions, Ruff, and `git diff --check`.
15. Append remediation evidence to `.superpowers/sdd/task-3-report.md` and commit with `fix: harden ssh node bootstrap`.
16. Generate a fresh review package from `6d74848` through the new Head and dispatch a fresh independent Task 3 reviewer. Do not begin Task 4 until Critical/Important findings are closed.

## Recommended Commands

```powershell
cd C:\Users\Administrator\Documents\visiox
git branch --show-current
git status --short
git diff --stat
py -3.12 -m pytest tests/unit/test_edge_bootstrap_hardening.py tests/unit/test_edge_bootstrap_protocol.py tests/integration/test_edge_ssh_api.py -q
```

Do not use bare `python`; it resolves to Python 3.10.

## Remaining Plan

- Task 3: finish hardening and obtain review approval.
- Task 4: inventory probe and compatible resource pools.
- Task 5: Edge Executor queue, audit, reconciliation, Compose, master-key secret, and the deferred startup happens-before check.
- Task 6: production YOLO26 TensorRT image deployment.
- Task 7: real deployment UI and online experience.
- Task 8: homogeneous-pool Ultralytics/PyTorch distributed training.
- Task 9: disposable SSH targets and two-node CPU/Gloo smoke.
- Task 10: x86/Jetson hardware acceptance scripts and runbook.

## Subagent-Driven Workflow

The previous subagents will not survive an account switch. Spawn a fresh capable
implementer for the remaining Task 3 remediation, then a separate read-only
reviewer. Use the task brief at `.superpowers/sdd/task-3-brief.md`; do not make a
new agent read the entire implementation plan.

After each approved task, append its commit range and review result to
`.superpowers/sdd/progress.md`. The ledger is git-ignored but remains on this
machine.
