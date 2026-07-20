$ErrorActionPreference = "Stop"

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$secretDirectory = Join-Path $repositoryRoot ".local-secrets"
$secretPath = Join-Path $secretDirectory "edge_credential_master_key"

function Test-IsWindows {
    return [System.Runtime.InteropServices.RuntimeInformation]::IsOSPlatform(
        [System.Runtime.InteropServices.OSPlatform]::Windows
    )
}

function Set-RestrictedPermissions {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [bool]$IsDirectory
    )

    if (Test-IsWindows) {
        $currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
        $localSystem = [System.Security.Principal.SecurityIdentifier]::new("S-1-5-18")
        if ($IsDirectory) {
            $acl = [System.Security.AccessControl.DirectorySecurity]::new()
        }
        else {
            $acl = [System.Security.AccessControl.FileSecurity]::new()
        }
        $acl.SetOwner($currentUser)
        $acl.SetAccessRuleProtection($true, $false)
        foreach ($identity in @($currentUser, $localSystem)) {
            if ($IsDirectory) {
                $inheritance = (
                    [System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
                    [System.Security.AccessControl.InheritanceFlags]::ObjectInherit
                )
                $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
                    $identity,
                    [System.Security.AccessControl.FileSystemRights]::FullControl,
                    $inheritance,
                    [System.Security.AccessControl.PropagationFlags]::None,
                    [System.Security.AccessControl.AccessControlType]::Allow
                )
            }
            else {
                $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
                    $identity,
                    [System.Security.AccessControl.FileSystemRights]::FullControl,
                    [System.Security.AccessControl.AccessControlType]::Allow
                )
            }
            $acl.AddAccessRule($rule) | Out-Null
        }
        Set-Acl -LiteralPath $Path -AclObject $acl
        return
    }

    $mode = if ($IsDirectory) { "700" } else { "600" }
    & /bin/chmod $mode -- $Path
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to restrict edge runtime secret permissions."
    }
}

[System.IO.Directory]::CreateDirectory($secretDirectory) | Out-Null
Set-RestrictedPermissions -Path $secretDirectory -IsDirectory $true

if (Test-Path -LiteralPath $secretPath -PathType Leaf) {
    Set-RestrictedPermissions -Path $secretPath -IsDirectory $false
    if ((Get-Item -LiteralPath $secretPath).Length -ne 32) {
        throw "Existing edge credential master key must contain exactly 32 raw bytes."
    }
    Write-Host "Edge runtime secret is already initialized."
    return
}

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

Set-RestrictedPermissions -Path $secretPath -IsDirectory $false
Write-Host "Edge runtime secret initialized."
