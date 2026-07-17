[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$apiBaseUrl = if ($env:VISIOX_SMOKE_API_URL) { $env:VISIOX_SMOKE_API_URL.TrimEnd('/') } else { "http://127.0.0.1:8000" }
$nodeName = "smoke-x86-node"
$containerName = "visiox-agent-smoke-x86-node"
$smokeLabel = "com.visiox.node-agent.smoke=true"
$imageName = "visiox-node-agent:smoke"
$statePath = Join-Path $repoRoot ".local\visiox-agent\$nodeName"
$containerId = $null
$inventoryEnvFile = Join-Path ([System.IO.Path]::GetTempPath()) "visiox-agent-inventory-$PID.env"
$primaryFailure = $null
$cleanupFailures = [System.Collections.Generic.List[string]]::new()

function Get-DockerContainerIds {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Description,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $ids = @(& docker @Arguments)
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "$Description failed with Docker exit code $exitCode"
    }
    return @($ids | ForEach-Object { $_.Trim() } | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
}

function Remove-SmokeContainer {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ContainerId,
        [Parameter(Mandatory = $true)]
        [string]$Description
    )

    $errors = [System.Collections.Generic.List[string]]::new()
    & docker stop --timeout 5 $ContainerId
    $stopExitCode = $LASTEXITCODE
    if ($stopExitCode -ne 0) {
        [void]$errors.Add("docker stop failed with exit code $stopExitCode")
    }

    & docker rm --force $ContainerId
    $removeExitCode = $LASTEXITCODE
    if ($removeExitCode -ne 0) {
        [void]$errors.Add("docker rm --force failed with exit code $removeExitCode")
    }

    if ($errors.Count -ne 0) {
        throw "$Description cleanup failed: $($errors -join '; ')"
    }
}

function Remove-StaleLabeledSmokeContainer {
    $nameFilter = 'name=^/' + $containerName + '$'
    $namedContainerIds = @(Get-DockerContainerIds -Description "Inspect smoke container name" -Arguments @(
        "ps", "--all", "--quiet", "--filter", $nameFilter
    ))
    if ($namedContainerIds.Count -eq 0) {
        return
    }
    if ($namedContainerIds.Count -ne 1) {
        throw "Refusing to reclaim $($namedContainerIds.Count) containers named $containerName"
    }

    $labeledContainerIds = @(Get-DockerContainerIds -Description "Inspect smoke container label" -Arguments @(
        "ps", "--all", "--quiet", "--filter", $nameFilter, "--filter", "label=$smokeLabel"
    ))
    if ($labeledContainerIds.Count -ne 1 -or $labeledContainerIds[0] -ne $namedContainerIds[0]) {
        throw "Refusing to remove existing container $($namedContainerIds[0]): it is not the labeled smoke container"
    }

    Remove-SmokeContainer -ContainerId $namedContainerIds[0] -Description "Stale labeled smoke container"
}

function Initialize-SmokeStateDirectory {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $arguments = @(
        "run", "--rm", "--user", "0:0", "--network", "none",
        "--entrypoint", "/bin/sh",
        "--mount", "type=bind,source=$Path,target=/var/lib/visiox-agent",
        $imageName, "-ec", "chown -R 10001:10001 /var/lib/visiox-agent && chmod 0700 /var/lib/visiox-agent"
    )
    & docker @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Could not prepare the bind-mounted smoke state directory for the non-root agent"
    }
}

function Assert-SmokeImageRunsAsAgentUser {
    $imageUser = @(& docker image inspect --format '{{.Config.User}}' $imageName)
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "Could not inspect the smoke image user"
    }
    if (($imageUser | Select-Object -Last 1).Trim() -ne "visiox-agent") {
        throw "Smoke image must run as visiox-agent"
    }
}

function Assert-SmokeContainerRunsAsAgentUser {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ContainerId
    )

    $containerUser = @(& docker container inspect --format '{{.Config.User}}' $ContainerId)
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "Could not inspect the smoke container user"
    }
    if (($containerUser | Select-Object -Last 1).Trim() -ne "visiox-agent") {
        throw "Smoke container must run as visiox-agent"
    }
}

function Write-SmokeDiagnostics {
    Write-Output "Recent API logs:"
    & docker compose -f (Join-Path $repoRoot "infra/compose/docker-compose.yml") logs --no-color --tail 100 api-service
    if ($LASTEXITCODE -ne 0) {
        Write-Output "Recent API logs were unavailable"
    }
    if ($containerId) {
        Write-Output "Recent Agent logs:"
        & docker logs --tail 100 $containerId
        if ($LASTEXITCODE -ne 0) {
            Write-Output "Recent Agent logs were unavailable"
        }
    }
}

try {
    Remove-StaleLabeledSmokeContainer

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

    & docker build --file (Join-Path $repoRoot "apps/node-agent/Dockerfile") `
        --build-arg "TEST_INVENTORY_FIXTURE_BUILD=true" --tag $imageName $repoRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Docker build failed for the smoke Agent image"
    }
    Assert-SmokeImageRunsAsAgentUser

    New-Item -ItemType Directory -Force $statePath | Out-Null
    $statePath = (Resolve-Path $statePath).Path
    Initialize-SmokeStateDirectory -Path $statePath

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
        "run", "--detach", "--name", $containerName, "--label", $smokeLabel,
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
    $runOutput = @(& docker @runArguments)
    $runExitCode = $LASTEXITCODE
    if ($runExitCode -ne 0) {
        throw "Could not start the smoke Agent container"
    }
    $containerId = ($runOutput | Select-Object -Last 1).Trim()
    if ([string]::IsNullOrWhiteSpace($containerId)) {
        throw "Could not determine the smoke Agent container ID"
    }

    $deadline = [DateTime]::UtcNow.AddSeconds(60)
    $nodeOnline = $false
    while ([DateTime]::UtcNow -lt $deadline) {
        try {
            $nodes = Invoke-RestMethod -Uri "$apiBaseUrl/nodes" -Method Get
            $node = @($nodes.items) | Where-Object { $_.name -eq $nodeName } | Select-Object -First 1
            if ($null -ne $node -and $node.status -eq "online") {
                $nodeOnline = $true
                break
            }
        }
        catch {
            Start-Sleep -Seconds 2
            continue
        }
        Start-Sleep -Seconds 2
    }
    if (-not $nodeOnline) {
        throw "Timed out waiting for $nodeName to become online"
    }

    Assert-SmokeContainerRunsAsAgentUser -ContainerId $containerId
    if (-not (Test-Path -LiteralPath (Join-Path $statePath "agent.db"))) {
        throw "Smoke Agent did not persist agent.db in the bind-mounted state directory"
    }
}
catch {
    $primaryFailure = $_.Exception
    Write-Output ("Smoke failed: " + $primaryFailure.Message)
    try {
        Write-SmokeDiagnostics
    }
    catch {
        Write-Output ("Could not collect smoke diagnostics: " + $_.Exception.Message)
    }
}
finally {
    if ($containerId) {
        try {
            Remove-SmokeContainer -ContainerId $containerId -Description "Smoke Agent container"
        }
        catch {
            [void]$cleanupFailures.Add($_.Exception.Message)
            Write-Output ("Smoke cleanup failed: " + $_.Exception.Message)
        }
    }
    try {
        if (Test-Path -LiteralPath $inventoryEnvFile) {
            Remove-Item -LiteralPath $inventoryEnvFile -Force
        }
    }
    catch {
        [void]$cleanupFailures.Add("Could not remove the inventory environment file: $($_.Exception.Message)")
        Write-Output ("Smoke cleanup failed: " + $_.Exception.Message)
    }
}

if ($primaryFailure) {
    if ($cleanupFailures.Count -ne 0) {
        Write-Output ("Smoke cleanup also failed: " + ($cleanupFailures -join '; '))
    }
    exit 1
}
if ($cleanupFailures.Count -ne 0) {
    Write-Error ("Smoke cleanup failed: " + ($cleanupFailures -join '; '))
    exit 1
}

Write-Output "$nodeName online"
