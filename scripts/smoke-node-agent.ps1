[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$apiBaseUrl = if ($env:VISIOX_SMOKE_API_URL) { $env:VISIOX_SMOKE_API_URL.TrimEnd('/') } else { "http://127.0.0.1:8000" }
$smokeRunId = [guid]::NewGuid().ToString("N").Substring(0, 12)
$smokeLabel = "com.visiox.node-agent.smoke=true"
$smokeRunLabel = "com.visiox.node-agent.smoke.run=$smokeRunId"
$imageName = "visiox-node-agent:smoke"
$stateBasePath = Join-Path $repoRoot ".local\visiox-agent"
$stateRoot = Join-Path $stateBasePath "smoke-$smokeRunId"
$inventoryEnvFile = Join-Path ([System.IO.Path]::GetTempPath()) "visiox-agent-inventory-$PID-$smokeRunId.env"
$agents = [System.Collections.Generic.List[object]]::new()
$stateRootCreated = $false
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

    $matches = @(Get-DockerContainerIds -Description "Inspect smoke container" -Arguments @(
        "ps", "--all", "--quiet", "--filter", "id=$ContainerId"
    ))
    if ($matches.Count -eq 0) {
        return
    }
    if (
        $matches.Count -ne 1 -or
        -not $ContainerId.StartsWith($matches[0], [System.StringComparison]::OrdinalIgnoreCase)
    ) {
        throw "$Description cleanup refused an unexpected container match"
    }
    $null = & docker stop --timeout 5 $ContainerId
    $null = & docker rm --force $ContainerId
    if ($LASTEXITCODE -ne 0) {
        throw "$Description cleanup failed: docker rm --force failed with exit code $LASTEXITCODE"
    }
}

function Assert-SafeSmokeStatePath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $trimCharacters = [char[]]@('\', '/')
    $basePath = [System.IO.Path]::GetFullPath($stateBasePath).TrimEnd($trimCharacters)
    $fullPath = [System.IO.Path]::GetFullPath($Path).TrimEnd($trimCharacters)
    $prefix = $basePath + [System.IO.Path]::DirectorySeparatorChar
    $relativePath = if ($fullPath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        $fullPath.Substring($prefix.Length)
    }
    else {
        ""
    }
    $runDirectory = ($relativePath -split '[\\/]')[0]
    if (-not $fullPath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase) -or
        $runDirectory -notlike "smoke-*") {
        throw "Refusing to manage unsafe smoke state path: $fullPath"
    }
}

function Initialize-SmokeStateDirectory {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    Assert-SafeSmokeStatePath -Path $Path
    if (Test-Path -LiteralPath $Path) {
        throw "Refusing to reuse an existing smoke state directory: $Path"
    }
    New-Item -ItemType Directory -Path $Path | Out-Null
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

function Remove-SmokeStateRoot {
    Assert-SafeSmokeStatePath -Path $stateRoot
    if (Test-Path -LiteralPath $stateRoot) {
        Remove-Item -LiteralPath $stateRoot -Recurse -Force
    }
}

function Assert-SmokeImageRunsAsAgentUser {
    $imageUser = @(& docker image inspect --format '{{.Config.User}}' $imageName)
    if ($LASTEXITCODE -ne 0) {
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
    if ($LASTEXITCODE -ne 0) {
        throw "Could not inspect the smoke container user"
    }
    if (($containerUser | Select-Object -Last 1).Trim() -ne "visiox-agent") {
        throw "Smoke container must run as visiox-agent"
    }
}

function Wait-SmokeNodeOnline {
    param(
        [Parameter(Mandatory = $true)]
        [string]$NodeName
    )

    $deadline = [DateTime]::UtcNow.AddSeconds(60)
    while ([DateTime]::UtcNow -lt $deadline) {
        try {
            $nodes = Invoke-RestMethod -Uri "$apiBaseUrl/nodes" -Method Get
            $node = @($nodes.items) | Where-Object { $_.name -eq $NodeName } | Select-Object -First 1
            if ($null -ne $node -and $node.status -eq "online" -and -not [string]::IsNullOrWhiteSpace([string]$node.id)) {
                return $node
            }
        }
        catch {
            Start-Sleep -Seconds 2
            continue
        }
        Start-Sleep -Seconds 2
    }
    throw "Timed out waiting for $NodeName to become online"
}

function Write-SmokeDiagnostics {
    Write-Output "Recent API logs:"
    & docker compose -f (Join-Path $repoRoot "infra/compose/docker-compose.yml") logs --no-color --tail 100 api-service
    if ($LASTEXITCODE -ne 0) {
        Write-Output "Recent API logs were unavailable"
    }
    foreach ($agent in $agents) {
        if ($agent.ContainerId) {
            Write-Output "Recent Agent logs for $($agent.NodeName):"
            & docker logs --tail 100 $agent.ContainerId
            if ($LASTEXITCODE -ne 0) {
                Write-Output "Recent Agent logs were unavailable"
            }
        }
    }
}

try {
    New-Item -ItemType Directory -Force -Path $stateBasePath | Out-Null
    Assert-SafeSmokeStatePath -Path $stateRoot
    if (Test-Path -LiteralPath $stateRoot) {
        throw "Refusing to reuse an existing smoke run directory: $stateRoot"
    }
    New-Item -ItemType Directory -Path $stateRoot | Out-Null
    $stateRootCreated = $true

    $health = Invoke-RestMethod -Uri "$apiBaseUrl/health" -Method Get
    if ($health.status -ne "ok") {
        throw "API health check did not return status=ok"
    }

    & docker build --file (Join-Path $repoRoot "apps/node-agent/Dockerfile") `
        --build-arg "TEST_INVENTORY_FIXTURE_BUILD=true" --tag $imageName $repoRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Docker build failed for the smoke Agent image"
    }
    Assert-SmokeImageRunsAsAgentUser

    $inventoryJson = @"
{"protocol_version":1,"type":"inventory","architecture":"amd64","platform_kind":"x86_nvidia","capabilities":{"nvidia_gpu":true},"resources":{"cpu_logical_cores":8,"memory_total_bytes":8589934592,"memory_available_bytes":4294967296,"gpus":[{"index":0,"uuid":"GPU-smoke","name":"Smoke GPU","compute_capability":"8.9","memory_total_bytes":8589934592,"memory_free_bytes":4294967296,"driver":"smoke"}]},"fingerprint":{"platform_kind":"x86_nvidia","architecture":"amd64","gpu_names":["Smoke GPU"],"compute_capabilities":["8.9"],"driver":"smoke"},"agent_version":"0.1.0-test"}
"@.Trim()
    [System.IO.File]::WriteAllText(
        $inventoryEnvFile,
        "VISIOX_AGENT_TEST_INVENTORY_JSON=$inventoryJson",
        [System.Text.UTF8Encoding]::new($false)
    )

    foreach ($index in 1..2) {
        $nodeName = "smoke-x86-$smokeRunId-$index"
        $containerName = "visiox-agent-smoke-$smokeRunId-$index"
        $agentStatePath = Join-Path $stateRoot "agent-$index"
        $tokenBody = @{ name = $nodeName } | ConvertTo-Json -Compress
        $tokenResponse = Invoke-RestMethod -Uri "$apiBaseUrl/agent/v1/enrollment-tokens" -Method Post `
            -ContentType "application/json" -Body $tokenBody
        $enrollmentToken = [string]$tokenResponse.token
        if ([string]::IsNullOrWhiteSpace($enrollmentToken)) {
            throw "Enrollment token response did not contain a token for $nodeName"
        }
        if ($agents | Where-Object { $_.EnrollmentToken -eq $enrollmentToken }) {
            throw "API returned a duplicate enrollment token during the same smoke run"
        }

        Initialize-SmokeStateDirectory -Path $agentStatePath
        $agent = [pscustomobject]@{
            NodeName = $nodeName
            ContainerName = $containerName
            StatePath = (Resolve-Path $agentStatePath).Path
            EnrollmentToken = $enrollmentToken
            ContainerId = $null
            NodeId = $null
        }
        [void]$agents.Add($agent)

        $runArguments = @(
            "run", "--detach", "--name", $agent.ContainerName,
            "--label", $smokeLabel, "--label", $smokeRunLabel,
            "--add-host", "host.docker.internal:host-gateway",
            "--mount", "type=bind,source=$($agent.StatePath),target=/var/lib/visiox-agent",
            "--env-file", $inventoryEnvFile,
            "--env", "VISIOX_AGENT_PLATFORM_URL=http://host.docker.internal:8000",
            "--env", "VISIOX_AGENT_ALLOW_INSECURE_LOCAL=true",
            "--env", "VISIOX_AGENT_VERSION=0.1.0-test",
            "--env", "VISIOX_AGENT_NODE_NAME=$($agent.NodeName)",
            "--env", "VISIOX_AGENT_ENROLLMENT_TOKEN=$($agent.EnrollmentToken)",
            $imageName
        )
        $runOutput = @(& docker @runArguments)
        if ($LASTEXITCODE -ne 0) {
            throw "Could not start the smoke Agent container for $($agent.NodeName)"
        }
        $agent.ContainerId = ($runOutput | Select-Object -Last 1).Trim()
        if ([string]::IsNullOrWhiteSpace($agent.ContainerId)) {
            throw "Could not determine the smoke Agent container ID for $($agent.NodeName)"
        }

        $onlineNode = Wait-SmokeNodeOnline -NodeName $agent.NodeName
        $agent.NodeId = [string]$onlineNode.id
        Assert-SmokeContainerRunsAsAgentUser -ContainerId $agent.ContainerId
        if (-not (Test-Path -LiteralPath (Join-Path $agent.StatePath "agent.db"))) {
            throw "Smoke Agent $($agent.NodeName) did not persist agent.db in its bind-mounted state directory"
        }
    }

    $nodeNames = @($agents | ForEach-Object { $_.NodeName })
    $nodeIds = @($agents | ForEach-Object { $_.NodeId })
    if ($nodeNames.Count -ne 2 -or (@($nodeNames | Select-Object -Unique)).Count -ne 2) {
        throw "Smoke run did not produce two distinct node names"
    }
    if ($nodeIds.Count -ne 2 -or (@($nodeIds | Select-Object -Unique)).Count -ne 2) {
        throw "Smoke run did not produce two distinct enrolled node IDs"
    }
    $nodes = Invoke-RestMethod -Uri "$apiBaseUrl/nodes" -Method Get
    foreach ($agent in $agents) {
        $node = @($nodes.items) | Where-Object { $_.id -eq $agent.NodeId -and $_.name -eq $agent.NodeName } | Select-Object -First 1
        if ($null -eq $node -or $node.status -ne "online") {
            throw "Fresh smoke node $($agent.NodeName) was not online in the shared database"
        }
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
    foreach ($agent in $agents) {
        if ($agent.ContainerId) {
            try {
                Remove-SmokeContainer -ContainerId $agent.ContainerId -Description "Smoke Agent $($agent.NodeName)"
            }
            catch {
                [void]$cleanupFailures.Add($_.Exception.Message)
                Write-Output ("Smoke cleanup failed: " + $_.Exception.Message)
            }
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
    if ($stateRootCreated) {
        try {
            Remove-SmokeStateRoot
        }
        catch {
            [void]$cleanupFailures.Add("Could not remove the smoke state directory: $($_.Exception.Message)")
            Write-Output ("Smoke cleanup failed: " + $_.Exception.Message)
        }
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

$summary = $agents | ForEach-Object { "$($_.NodeName) ($($_.NodeId))" }
Write-Output ("Smoke run $smokeRunId enrolled two fresh nodes: " + ($summary -join ', '))
