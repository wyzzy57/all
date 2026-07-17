[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$dockerfile = Join-Path $repoRoot "apps/node-agent/Dockerfile"
$dist = Join-Path $repoRoot "dist"
$temporaryRoot = Join-Path ([System.IO.Path]::GetTempPath()) "visiox-node-agent-build-$PID"

New-Item -ItemType Directory -Force $dist | Out-Null
New-Item -ItemType Directory -Force $temporaryRoot | Out-Null

try {
    foreach ($architecture in @("amd64", "arm64")) {
        $output = Join-Path $temporaryRoot $architecture
        New-Item -ItemType Directory -Force $output | Out-Null
        $arguments = @(
            "build",
            "--file", $dockerfile,
            "--target", "builder",
            "--build-arg", "TARGETOS=linux",
            "--build-arg", "TARGETARCH=$architecture",
            "--output", "type=local,dest=$output",
            $repoRoot
        )
        & docker @arguments
        if ($LASTEXITCODE -ne 0) {
            throw "Docker build failed for linux/$architecture"
        }

        $source = Join-Path $output "out\visiox-node-agent"
        if (-not (Test-Path -LiteralPath $source)) {
            throw "Docker build did not produce $source"
        }
        Copy-Item -LiteralPath $source -Destination (Join-Path $dist "visiox-node-agent-linux-$architecture") -Force
    }
}
finally {
    if (Test-Path -LiteralPath $temporaryRoot) {
        Remove-Item -LiteralPath $temporaryRoot -Recurse -Force
    }
}

Get-ChildItem -LiteralPath $dist -Filter "visiox-node-agent-linux-*" | Select-Object FullName, Length
