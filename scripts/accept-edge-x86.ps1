[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$ExpectedHostFingerprint,

    [string]$PlatformApiBaseUri = "http://127.0.0.1:8000",
    [string]$HostName = "",
    [ValidateRange(1, 65535)]
    [int]$SshPort = 22,
    [string]$Administrator = "",
    [string]$NodeName = "",
    [string]$ServiceRequestJsonPath = "",
    [string]$TrainingRequestJsonPath = "",
    [string]$TrainingPipelineId = "",
    [string]$InferenceImagePath = "",
    [ValidateRange(30, 7200)]
    [int]$TimeoutSeconds = 900,
    [switch]$RunHardwareChecks,
    [switch]$KeepResources
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
Add-Type -AssemblyName System.Net.Http
$startedAt = [DateTimeOffset]::UtcNow
$checks = [System.Collections.Generic.List[object]]::new()
$serviceId = $null
$nodeId = $null
$securePassword = $null
$PlaintextPassword = $null
$passwordPointer = [IntPtr]::Zero

function Add-AcceptanceCheck {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][ValidateSet("passed", "failed", "pending", "skipped")][string]$Status,
        [string]$Detail = "",
        [object]$Evidence = $null
    )
    $checks.Add([ordered]@{
        name = $Name
        status = $Status
        detail = $Detail
        evidence = $Evidence
    }) | Out-Null
}

function Invoke-ApiJson {
    param(
        [Parameter(Mandatory = $true)][ValidateSet("GET", "POST", "DELETE")][string]$Method,
        [Parameter(Mandatory = $true)][string]$Path,
        [object]$Body = $null
    )
    $uri = $PlatformApiBaseUri.TrimEnd("/") + $Path
    $parameters = @{
        Method = $Method
        Uri = $uri
        TimeoutSec = [Math]::Min($TimeoutSeconds, 300)
        ErrorAction = "Stop"
    }
    if ($null -ne $Body) {
        $parameters.ContentType = "application/json"
        $parameters.Body = $Body | ConvertTo-Json -Depth 20 -Compress
    }
    return Invoke-RestMethod @parameters
}

function Wait-ApiResource {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string[]]$SuccessStates,
        [Parameter(Mandatory = $true)][string[]]$FailureStates
    )
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        $resource = Invoke-ApiJson -Method GET -Path $Path
        $state = [string]$resource.status
        if ($SuccessStates -contains $state) { return $resource }
        if ($FailureStates -contains $state) {
            throw "Resource entered failure state '$state': $($resource.error_message)"
        }
        Start-Sleep -Seconds 2
    }
    throw "Timed out waiting for $Path"
}

function Set-JsonProperty {
    param([object]$Object, [string]$Name, [object]$Value)
    if ($Object.PSObject.Properties.Name -contains $Name) {
        $Object.$Name = $Value
    }
    else {
        $Object | Add-Member -NotePropertyName $Name -NotePropertyValue $Value
    }
}

function Invoke-ImageInference {
    param([string]$Uri, [string]$ImagePath)
    $client = [System.Net.Http.HttpClient]::new()
    $client.Timeout = [TimeSpan]::FromSeconds([Math]::Min($TimeoutSeconds, 300))
    $form = [System.Net.Http.MultipartFormDataContent]::new()
    $stream = $null
    $content = $null
    try {
        $stream = [System.IO.File]::OpenRead($ImagePath)
        $content = [System.Net.Http.StreamContent]::new($stream)
        $form.Add($content, "file", [System.IO.Path]::GetFileName($ImagePath))
        $response = $client.PostAsync($Uri, $form).GetAwaiter().GetResult()
        $responseBody = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        if (-not $response.IsSuccessStatusCode) {
            throw "Image inference returned HTTP $([int]$response.StatusCode)"
        }
        return $responseBody | ConvertFrom-Json
    }
    finally {
        if ($null -ne $content) { $content.Dispose() }
        if ($null -ne $stream) { $stream.Dispose() }
        $form.Dispose()
        $client.Dispose()
    }
}

function Assert-HardwareArguments {
    $required = [ordered]@{
        HostName = $HostName
        Administrator = $Administrator
        NodeName = $NodeName
        ServiceRequestJsonPath = $ServiceRequestJsonPath
        TrainingRequestJsonPath = $TrainingRequestJsonPath
        TrainingPipelineId = $TrainingPipelineId
        InferenceImagePath = $InferenceImagePath
    }
    foreach ($entry in $required.GetEnumerator()) {
        if ([string]::IsNullOrWhiteSpace([string]$entry.Value)) {
            throw "-$($entry.Key) is required with -RunHardwareChecks"
        }
    }
    foreach ($path in @($ServiceRequestJsonPath, $TrainingRequestJsonPath, $InferenceImagePath)) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Required acceptance input does not exist: $path"
        }
    }
}

if (-not $RunHardwareChecks) {
    Add-AcceptanceCheck -Name "physical_hardware" -Status "pending" -Detail "Physical hardware checks were not run; rerun with -RunHardwareChecks."
}
else {
    try {
        Assert-HardwareArguments
        $securePassword = Read-Host -Prompt "Edge administrator one-time password" -AsSecureString
        $passwordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
        $PlaintextPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPointer)

        $scan = Invoke-ApiJson -Method POST -Path "/edge-nodes/scan-host-key" -Body @{
            host = $HostName
            port = $SshPort
        }
        if ([string]$scan.fingerprint -cne $ExpectedHostFingerprint) {
            Add-AcceptanceCheck -Name "host_fingerprint" -Status "failed" -Detail "fingerprint_mismatch" -Evidence @{ observed = [string]$scan.fingerprint }
            throw "fingerprint_mismatch"
        }
        Add-AcceptanceCheck -Name "host_fingerprint" -Status "passed" -Evidence @{ fingerprint = $ExpectedHostFingerprint; key_type = $scan.host_key_type }

        $bootstrap = Invoke-ApiJson -Method POST -Path "/edge-nodes/bootstrap" -Body @{
            host = $HostName
            port = $SshPort
            administrator = $Administrator
            password = $PlaintextPassword
            confirmed_fingerprint = $ExpectedHostFingerprint
            node_name = $NodeName
        }
        $PlaintextPassword = $null
        if ($passwordPointer -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPointer)
            $passwordPointer = [IntPtr]::Zero
        }
        $securePassword.Dispose()
        $securePassword = $null
        $nodeId = [string]$bootstrap.node_id
        Add-AcceptanceCheck -Name "bootstrap" -Status "passed" -Evidence @{ node_id = $nodeId }

        $null = Invoke-ApiJson -Method POST -Path "/edge-nodes/$nodeId/test-connection"
        Add-AcceptanceCheck -Name "strict_ssh_reconnect" -Status "passed"

        $probe = Invoke-ApiJson -Method POST -Path "/edge-nodes/$nodeId/probe"
        if (-not [bool]$probe.supported -or [string]$probe.inventory.platform_kind -ne "x86_nvidia") {
            throw "Edge inventory is not a supported x86 NVIDIA host"
        }
        Add-AcceptanceCheck -Name "nvidia-smi" -Status "passed" -Evidence @{ gpu_count = @($probe.inventory.gpus).Count }
        if (-not [bool]$probe.inventory.docker.nvidia_runtime_available) {
            throw "Docker NVIDIA runtime is unavailable"
        }
        Add-AcceptanceCheck -Name "nvidia_runtime" -Status "passed" -Evidence @{ docker_version = $probe.inventory.docker.version }

        $serviceBody = Get-Content -LiteralPath $ServiceRequestJsonPath -Raw -Encoding UTF8 | ConvertFrom-Json
        Set-JsonProperty -Object $serviceBody -Name "node_id" -Value $nodeId
        Set-JsonProperty -Object $serviceBody -Name "precision" -Value "fp16"
        Set-JsonProperty -Object $serviceBody -Name "format" -Value "engine"
        $service = Invoke-ApiJson -Method POST -Path "/services" -Body $serviceBody
        $serviceId = [string]$service.id
        $service = Wait-ApiResource -Path "/services/$serviceId" -SuccessStates @("running") -FailureStates @("failed", "stopped")
        if ([string]$service.engine -ne "engine" -or [string]::IsNullOrWhiteSpace([string]$service.engine_digest)) {
            throw "TensorRT FP16 engine evidence is missing"
        }
        Add-AcceptanceCheck -Name "tensorrt_fp16_export" -Status "passed" -Evidence @{ engine_digest = $service.engine_digest }

        $health = Invoke-RestMethod -Method GET -Uri ($service.endpoint.TrimEnd("/") + "/health") -TimeoutSec 30
        if ([string]$health.status -ne "ok") { throw "Service health endpoint did not report ok" }
        Add-AcceptanceCheck -Name "service_health" -Status "passed" -Evidence @{ endpoint = $service.endpoint }

        $prediction = Invoke-ImageInference -Uri ($service.endpoint.TrimEnd("/") + "/predict/image") -ImagePath $InferenceImagePath
        Add-AcceptanceCheck -Name "image_inference" -Status "passed" -Evidence @{ response_received = ($null -ne $prediction) }

        $trainingBody = Get-Content -LiteralPath $TrainingRequestJsonPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $training = Invoke-ApiJson -Method POST -Path "/pipelines/$TrainingPipelineId/jobs" -Body $trainingBody
        $trainingId = [string]$training.id
        $training = Wait-ApiResource -Path "/training-jobs/$trainingId" -SuccessStates @("succeeded") -FailureStates @("failed", "cancelled")
        Add-AcceptanceCheck -Name "short_ultralytics_training" -Status "passed" -Evidence @{ training_job_id = $trainingId }

        $artifacts = Invoke-ApiJson -Method GET -Path "/training-jobs/$trainingId/artifacts"
        if (@($artifacts.items).Count -eq 0) { throw "Training completed without uploaded artifacts" }
        Add-AcceptanceCheck -Name "artifact_upload" -Status "passed" -Evidence @{ artifact_count = @($artifacts.items).Count }

        $null = Invoke-ApiJson -Method POST -Path "/services/$serviceId/stop"
        $null = Wait-ApiResource -Path "/services/$serviceId" -SuccessStates @("stopped") -FailureStates @("failed")
        Add-AcceptanceCheck -Name "service_stop" -Status "passed"

        if (-not $KeepResources) {
            $null = Invoke-ApiJson -Method DELETE -Path "/services/$serviceId"
            Add-AcceptanceCheck -Name "cleanup" -Status "passed" -Detail "Acceptance deployment service deleted."
            $serviceId = $null
        }
        else {
            Add-AcceptanceCheck -Name "cleanup" -Status "skipped" -Detail "Resources retained by -KeepResources."
        }
    }
    catch {
        Add-AcceptanceCheck -Name "acceptance_execution" -Status "failed" -Detail $_.Exception.Message
    }
    finally {
        if ($null -ne $serviceId -and -not $KeepResources) {
            $cleanupAlreadyPassed = @(
                $checks | Where-Object { $_.name -eq "cleanup" -and $_.status -eq "passed" }
            ).Count -gt 0
            if (-not $cleanupAlreadyPassed) {
                try {
                    $currentService = Invoke-ApiJson -Method GET -Path "/services/$serviceId"
                    if ([string]$currentService.status -ne "stopped") {
                        $null = Invoke-ApiJson -Method POST -Path "/services/$serviceId/stop"
                        $null = Wait-ApiResource -Path "/services/$serviceId" -SuccessStates @("stopped") -FailureStates @()
                    }
                    $null = Invoke-ApiJson -Method DELETE -Path "/services/$serviceId"
                    Add-AcceptanceCheck -Name "cleanup" -Status "passed" -Detail "Failure-path deployment service deleted."
                    $serviceId = $null
                }
                catch {
                    Add-AcceptanceCheck -Name "cleanup" -Status "failed" -Detail $_.Exception.Message
                }
            }
        }
        $PlaintextPassword = $null
        if ($null -ne $securePassword) {
            $securePassword.Dispose()
            $securePassword = $null
        }
        if ($passwordPointer -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPointer)
            $passwordPointer = [IntPtr]::Zero
        }
    }
}

$failed = @($checks | Where-Object { $_.status -eq "failed" }).Count -gt 0
$pending = @($checks | Where-Object { $_.status -eq "pending" }).Count -gt 0
$overallStatus = if ($failed) { "failed" } elseif ($pending) { "pending" } else { "passed" }
$summary = [ordered]@{
    schema_version = "visiox.edge-acceptance.v1"
    platform = "x86_nvidia"
    status = $overallStatus
    hardware_checks_executed = [bool]$RunHardwareChecks
    host = if ([string]::IsNullOrWhiteSpace($HostName)) { $null } else { $HostName }
    node_id = $nodeId
    service_id = $serviceId
    started_at = $startedAt.ToString("o")
    finished_at = [DateTimeOffset]::UtcNow.ToString("o")
    checks = @($checks)
}
$summaryJson = $summary | ConvertTo-Json -Depth 12 -Compress
Write-Output $summaryJson
if ($failed) { exit 1 }
