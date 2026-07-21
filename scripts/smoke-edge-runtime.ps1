[CmdletBinding()]
param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$runId = [guid]::NewGuid().ToString("N")
$outputRoot = Join-Path $repoRoot ".tmp\edge-runtime-smoke-$runId"
$pytestPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $pytestPython)) {
    $pytestPython = $Python
}

$result = [ordered]@{
    schema = "visiox.edge-runtime-smoke.v1"
    status = "PENDING"
    run_id = $runId
    ssh_lifecycle = [ordered]@{ status = "PENDING"; detail = "not started" }
    distributed_gloo = [ordered]@{ status = "PENDING"; detail = "not started" }
}

function Invoke-Captured {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    $lines = @(& $FilePath @Arguments 2>&1 | ForEach-Object { [string]$_ })
    return [pscustomobject]@{ ExitCode = $LASTEXITCODE; Lines = $lines; Text = ($lines -join "`n") }
}

function Test-DockerReady {
    try {
        $null = & docker version --format '{{.Server.Version}}' 2>&1
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

try {
    New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null

    if (Test-DockerReady) {
        $previousIntegrationFlag = $env:VISIOX_RUN_EDGE_SSH_INTEGRATION
        try {
            $env:VISIOX_RUN_EDGE_SSH_INTEGRATION = "1"
            $ssh = Invoke-Captured -FilePath $pytestPython -Arguments @(
                "-m", "pytest",
                "tests/integration/edge_ssh/test_edge_executor_ssh.py",
                "-q", "-rA",
                "--basetemp=$outputRoot/pytest",
                "-o", "cache_dir=$outputRoot/pytest-cache"
            )
        }
        finally {
            $env:VISIOX_RUN_EDGE_SSH_INTEGRATION = $previousIntegrationFlag
        }
        if ($ssh.ExitCode -ne 0) {
            $result.ssh_lifecycle = [ordered]@{ status = "FAIL"; detail = $ssh.Text }
        }
        elseif ($ssh.Text -match "skipped" -or $ssh.Text -match "SKIPPED") {
            $result.ssh_lifecycle = [ordered]@{
                status = "PENDING"
                detail = "Disposable SSH target was skipped; inspect pytest output"
                output = $ssh.Text
            }
        }
        else {
            $result.ssh_lifecycle = [ordered]@{ status = "PASS"; detail = $ssh.Text }
        }
    }
    else {
        $result.ssh_lifecycle = [ordered]@{
            status = "PENDING"
            detail = "Docker Desktop is unavailable"
        }
    }

    $glooOutput = Join-Path $outputRoot "gloo"
    $gloo = Invoke-Captured -FilePath $Python -Arguments @(
        "tests/smoke/distributed_gloo_smoke.py",
        "--output", $glooOutput
    )
    $glooPayload = $null
    foreach ($line in ($gloo.Lines | Select-Object -Reverse)) {
        try {
            $candidate = $line | ConvertFrom-Json -ErrorAction Stop
            if ($candidate.schema -eq "visiox.distributed-gloo-smoke.v1") {
                $glooPayload = $candidate
                break
            }
        }
        catch {
            continue
        }
    }
    if ($null -eq $glooPayload) {
        $result.distributed_gloo = [ordered]@{
            status = "FAIL"
            detail = "Gloo smoke did not return structured output"
            output = $gloo.Text
        }
    }
    else {
        $result.distributed_gloo = $glooPayload
        if ($gloo.ExitCode -ne 0 -and $glooPayload.status -eq "PASS") {
            $result.distributed_gloo.status = "FAIL"
            $result.distributed_gloo | Add-Member -NotePropertyName detail `
                -NotePropertyValue "Gloo process exited with code $($gloo.ExitCode)" -Force
        }
    }

    $statuses = @($result.ssh_lifecycle.status, $result.distributed_gloo.status)
    if ($statuses -contains "FAIL") {
        $result.status = "FAIL"
    }
    elseif (@($statuses | Where-Object { $_ -eq "PASS" }).Count -eq 2) {
        $result.status = "PASS"
    }
    else {
        $result.status = "PENDING"
    }
}
catch {
    $result.status = "FAIL"
    $result.error = $_.Exception.Message
}

$result | ConvertTo-Json -Depth 8 -Compress
if ($result.status -eq "PASS") {
    exit 0
}
if ($result.status -eq "PENDING") {
    exit 2
}
exit 1
