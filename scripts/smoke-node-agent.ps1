[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$apiBaseUrl = if ($env:VISIOX_SMOKE_API_URL) { $env:VISIOX_SMOKE_API_URL.TrimEnd('/') } else { "http://127.0.0.1:8000" }
$nodeName = "smoke-x86-node"
$containerName = "visiox-agent-smoke-x86-node"
$imageName = "visiox-node-agent:smoke"
$statePath = Join-Path $repoRoot ".local\visiox-agent\$nodeName"
$containerId = $null
$inventoryEnvFile = Join-Path ([System.IO.Path]::GetTempPath()) "visiox-agent-inventory-$PID.env"

try {
    $health = Invoke-RestMethod -Uri "$apiBaseUrl/health" -Method Get
    if ($health.status -ne "ok") {
        throw "API health check did not return status=ok"
    }

    $tokenBody = @{ name = $nodeName } | ConvertTo-Json -Compress
    $tokenResponse = Invoke-RestMethod -Uri "$apiBaseUrl/agent/v1/enrollment-tokens" -Method Post `
        -ContentType "application/json" -Body $tokenBody
    $enrollmentToken = [string]$tokenResponse.token
    if ([string]::IsNullOrWhiteSpace($enrollmentToken)) {
        throw "Enrollment token response did not contain a token"
    }

    & docker build --file (Join-Path $repoRoot "apps/node-agent/Dockerfile") --tag $imageName $repoRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Docker build failed for the smoke Agent image"
    }

    New-Item -ItemType Directory -Force $statePath | Out-Null
    $statePath = (Resolve-Path $statePath).Path
    $inventoryJson = @"
{"protocol_version":1,"type":"inventory","architecture":"amd64","platform_kind":"x86_nvidia","capabilities":{"nvidia_gpu":true},"resources":{"cpu_logical_cores":8,"memory_total_bytes":8589934592,"memory_available_bytes":4294967296,"gpus":[{"index":0,"uuid":"GPU-smoke","name":"Smoke GPU","compute_capability":"8.9","memory_total_bytes":8589934592,"memory_free_bytes":4294967296,"driver":"smoke"}]},"fingerprint":{"platform_kind":"x86_nvidia","architecture":"amd64","gpu_names":["Smoke GPU"],"compute_capabilities":["8.9"],"driver":"smoke"},"agent_version":"0.1.0-test"}
"@
    $inventoryJson = $inventoryJson.Trim()

    [System.IO.File]::WriteAllText(
        $inventoryEnvFile,
        "VISIOX_AGENT_TEST_INVENTORY_JSON=$inventoryJson",
        [System.Text.UTF8Encoding]::new($false)
    )

    $runArguments = @(
        "run", "--detach", "--name", $containerName,
        "--add-host", "host.docker.internal:host-gateway",
        "--mount", "type=bind,source=$statePath,target=/var/lib/visiox-agent",
        "--env-file", $inventoryEnvFile,
        "--env", "VISIOX_AGENT_PLATFORM_URL=http://host.docker.internal:8000",
        "--env", "VISIOX_AGENT_ALLOW_INSECURE_LOCAL=true",
        "--env", "VISIOX_AGENT_VERSION=0.1.0-test",
        "--env", "VISIOX_AGENT_NODE_NAME=$nodeName",
        "--env", "VISIOX_AGENT_ENROLLMENT_TOKEN=$enrollmentToken",
        $imageName
    )
    $containerId = (& docker @runArguments | Select-Object -Last 1).Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($containerId)) {
        throw "Could not start the smoke Agent container"
    }

    $deadline = [DateTime]::UtcNow.AddSeconds(60)
    while ([DateTime]::UtcNow -lt $deadline) {
        try {
            $nodes = Invoke-RestMethod -Uri "$apiBaseUrl/nodes" -Method Get
            $node = @($nodes.items) | Where-Object { $_.name -eq $nodeName } | Select-Object -First 1
            if ($null -ne $node -and $node.status -eq "online") {
                Write-Output "$nodeName online"
                return
            }
        }
        catch {
            Start-Sleep -Seconds 2
            continue
        }
        Start-Sleep -Seconds 2
    }
    throw "Timed out waiting for $nodeName to become online"
}
catch {
    Write-Output ("Smoke failed: " + $_.Exception.Message)
    Write-Output "Recent API logs:"
    & docker compose -f (Join-Path $repoRoot "infra/compose/docker-compose.yml") logs --no-color --tail 100 api-service
    if ($containerId) {
        Write-Output "Recent Agent logs:"
        & docker logs --tail 100 $containerName
    }
    exit 1
}
finally {
    if ($containerId) {
        & docker stop --time 5 $containerName *> $null
        & docker rm --force $containerName *> $null
    }
    if (Test-Path -LiteralPath $inventoryEnvFile) {
        Remove-Item -LiteralPath $inventoryEnvFile -Force
    }
}
