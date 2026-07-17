# Task 8 Implementation Report

Status: DONE_WITH_CONCERNS
Date: 2026-07-17 Asia/Shanghai
Workspace: C:\Users\Administrator\Documents\visiox

## Scope

Implemented Task 8 node-agent onboarding, including the Docker image, systemd
unit, root installer, cross-architecture build, exact smoke workflow, agent CA
startup, Compose agent-pki wiring, ignored local identity state, and the
test-only inventory fixture injection required by the brief.

The fixture is accepted only when `AgentVersion` ends in `-test`. Production
versions reject `VISIOX_AGENT_TEST_INVENTORY_JSON` and otherwise always call the
hardware inventory probe. The smoke script never prints the enrollment token.

## TDD RED

CA startup tests were written first:

```powershell
py -3.12 -m pytest tests/integration/test_agent_ca_startup.py -q
```

Output: `2 failed`, because application lifespan did not yet call the CA
startup helper and production startup did not yet fail closed.

The local Docker hostname test was written before the allowlist change:

```powershell
docker run --rm -v "${PWD}:/src" -w /src/apps/node-agent golang:1.26.5-alpine go test ./internal/config -run TestLoadAllowsExplicitInsecureLocalPlatformURL -count=1
```

Output: failed because `host.docker.internal` was rejected by the local
development URL policy.

The CA certificate-signing assertion was also written before the certificate
fix:

```text
ExtensionNotFound: No <class 'cryptography.x509.extensions.KeyUsage'> extension was found
```

The Go inventory tests were written before the test-only fixture helper and
initially failed to compile until the helper and test seam were added.

## TDD GREEN

CA startup and the required API integration coverage:

```powershell
py -3.12 -m pytest tests/integration/test_agent_ca_startup.py tests/integration/test_agent_gateway.py tests/integration/test_node_enrollment_api.py -q
```

Output: `49 passed, 1 warning in 28.61s`. The warning is the existing
Starlette/httpx TestClient deprecation warning.

Focused Python lint:

```powershell
py -3.12 -m ruff check apps/api-service/src/visiox_api/main.py apps/api-service/src/visiox_api/services/agent_identity.py packages/visiox-common/src/visiox_common/settings.py tests/integration/test_agent_ca_startup.py tests/unit/test_agent_identity.py
```

Output: `All checks passed!`

Pinned Go gate:

```powershell
docker run --rm -v "${PWD}:/src" -w /src/apps/node-agent golang:1.26.5-alpine sh -c "apk add --no-cache gcc musl-dev >/dev/null && /usr/local/go/bin/gofmt -w . && go test -race ./... && go vet ./..."
```

Output: all Go packages passed race tests and `go vet`; command exit code 0.

## Build And Smoke

```powershell
.\scripts\build-node-agent.ps1
```

Output:

```text
dist\visiox-node-agent-linux-amd64 7090338
dist\visiox-node-agent-linux-arm64 6619298
```

The required exact smoke command was run twice:

```powershell
.\scripts\smoke-node-agent.ps1
```

Output from both runs:

```text
smoke-x86-node online
```

The second exact run reused the persisted ignored state at
`.local\visiox-agent\smoke-x86-node`.

PowerShell parser and Compose validation:

```text
PowerShell syntax OK
Compose config OK
```

## Compose Staged Scope

The live Compose file was not added with `git add`. A clean HEAD-based ignored
scratch file was created at `.tmp\task8-compose-clean.yml`, Task 8 changes were
applied to that file, and its blob was written and installed with:

```powershell
$blob = (git hash-object -w .tmp/task8-compose-clean.yml).Trim()
git update-index --cacheinfo "100644,$blob,infra/compose/docker-compose.yml"
```

Cached Compose diff proof:

```text
+      VISIOX_AGENT_GATEWAY_ENABLED: "true"
+      VISIOX_AGENT_AUTO_GENERATE_CA: "true"
+      VISIOX_AGENT_PUBLIC_WS_URL: ${VISIOX_AGENT_PUBLIC_WS_URL:-ws://host.docker.internal:8000/agent/v1/connect}
+      - agent-pki:/var/lib/visiox/pki
+  agent-pki:
No forbidden Label Studio hunks in cached Compose
```

The cached diff contains no `0.0.0.0`, `80:8080`, or replacement of
`http://10.10.40.2:8080`. The worktree diff still contains the user hunks:

```text
-      - http://10.10.40.2:8080
+      - 0.0.0.0
+      - "80:8080"
       - "8080:8080"
```

## Cleanup And Tracking

The smoke script removes its container and temporary env file in `finally`.
Failed enrollment records created during debugging were deleted from the
local database. The stale CA volume material was removed before the final API
restart so the corrected CA was regenerated with `KeyUsage.key_cert_sign`.

The amd64 and arm64 binaries are ignored by `/dist/`. The persisted agent DB
is ignored by `/.local/visiox-agent/`, and CA key patterns are ignored by
`*.agent-ca.key`. No generated
secret, binary, agent DB, or CA key is tracked. `.codex-logs/` and `.planning/`
were left untouched and uncommitted.

## Commit

The staged commit command is:

```powershell
git commit -m "feat: package node agent onboarding"
```

The commit contains only Task 8 implementation, tests, packaging/scripts,
report, and the clean Task 8 Compose blob. The live Label Studio hunks remain
unstaged in the worktree.

## Self Review

- CA startup is local-development auto-generating and production fail-closed.
- CA certificate signing usage is explicit and verified by tests.
- Enrollment token values are not written to smoke output or the report.
- Fixture injection is rejected for production and non-suffix test versions.
- Repeated smoke runs use the same ignored local identity state.
- Compose staging was performed through a clean scratch blob and verified.
- No `.codex-logs/`, `.planning/`, generated secret, binary, or user Compose
  hunk is staged.

## Concerns

The required test suite retains one existing Starlette deprecation warning.
The Docker local-output build currently transfers the builder filesystem for
each architecture, so the build is correct but slower than the final binary
size suggests. Neither concern blocks Task 8 acceptance.
