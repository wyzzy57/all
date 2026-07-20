$ErrorActionPreference = "Stop"

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$secretDirectory = Join-Path $repositoryRoot ".local-secrets"
$secretPath = Join-Path $secretDirectory "edge_credential_master_key"

if (Test-Path -LiteralPath $secretPath -PathType Leaf) {
    if ((Get-Item -LiteralPath $secretPath).Length -ne 32) {
        throw "Existing edge credential master key must contain exactly 32 raw bytes."
    }
    Write-Host "Edge runtime secret is already initialized."
    return
}

[System.IO.Directory]::CreateDirectory($secretDirectory) | Out-Null
$secretBytes = [byte[]]::new(32)
$random = [System.Security.Cryptography.RandomNumberGenerator]::Create()
$stream = $null

try {
    $random.GetBytes($secretBytes)
    $stream = [System.IO.File]::Open(
        $secretPath,
        [System.IO.FileMode]::CreateNew,
        [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::None
    )
    $stream.Write($secretBytes, 0, $secretBytes.Length)
    $stream.Flush($true)
}
finally {
    if ($null -ne $stream) {
        $stream.Dispose()
    }
    $random.Dispose()
    [System.Array]::Clear($secretBytes, 0, $secretBytes.Length)
}

Write-Host "Edge runtime secret initialized."
