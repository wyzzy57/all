from __future__ import annotations

import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = {
    "x86_nvidia": ROOT / "scripts" / "accept-edge-x86.ps1",
    "jetson": ROOT / "scripts" / "accept-edge-jetson.ps1",
}
RUNBOOK = ROOT / "docs" / "runbooks" / "ssh-docker-edge-runtime.md"
README = ROOT / "README.md"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _param_block(text: str) -> str:
    match = re.search(r"(?ms)^param\((.*?)^\)", text)
    assert match is not None, "acceptance script must have a top-level param block"
    return match.group(1)


@pytest.mark.parametrize("script", SCRIPTS.values(), ids=SCRIPTS.keys())
def test_acceptance_scripts_require_confirmed_host_fingerprint(script: Path) -> None:
    text = _text(script)
    params = _param_block(text)

    assert re.search(
        r"\[Parameter\(Mandatory\s*=\s*\$true\)\]\s*\[ValidateNotNullOrEmpty\(\)\]"
        r"\s*\[string\]\$ExpectedHostFingerprint",
        params,
        re.IGNORECASE,
    )
    assert "/edge-nodes/scan-host-key" in text
    assert "ExpectedHostFingerprint" in text
    assert "fingerprint_mismatch" in text


@pytest.mark.parametrize("script", SCRIPTS.values(), ids=SCRIPTS.keys())
def test_acceptance_scripts_never_take_password_as_cli_argument(script: Path) -> None:
    text = _text(script)
    params = _param_block(text)

    assert not re.search(r"\$\w*password\w*", params, re.IGNORECASE)
    assert "Read-Host -Prompt" in text
    assert "-AsSecureString" in text
    assert "SecureStringToBSTR" in text
    assert "ZeroFreeBSTR" in text
    assert "password" not in re.sub(
        r'"password"\s*=\s*\$PlaintextPassword',
        "",
        text,
        flags=re.IGNORECASE,
    ).split("ConvertTo-Json")[-1].lower()


@pytest.mark.parametrize("script", SCRIPTS.values(), ids=SCRIPTS.keys())
def test_acceptance_scripts_emit_one_machine_readable_summary(script: Path) -> None:
    text = _text(script)

    assert "ConvertTo-Json -Depth" in text
    assert "Write-Output $summaryJson" in text
    assert 'schema_version = "visiox.edge-acceptance.v1"' in text
    assert "hardware_checks_executed" in text
    assert "checks = @($checks)" in text
    assert "started_at" in text
    assert "finished_at" in text


@pytest.mark.parametrize("script", SCRIPTS.values(), ids=SCRIPTS.keys())
def test_acceptance_scripts_default_physical_checks_to_pending(script: Path) -> None:
    text = _text(script)
    params = _param_block(text)

    assert "[switch]$RunHardwareChecks" in params
    assert '-Status "pending"' in text
    assert "if (-not $RunHardwareChecks)" in text
    assert "Physical hardware checks were not run" in text
    assert "hardware_checks_executed = [bool]$RunHardwareChecks" in text


@pytest.mark.parametrize("script", SCRIPTS.values(), ids=SCRIPTS.keys())
def test_acceptance_scripts_cover_the_production_hardware_loop(script: Path) -> None:
    text = _text(script)

    required_contracts = (
        "/edge-nodes/bootstrap",
        "/probe",
        '-Name "precision" -Value "fp16"',
        '-Name "format" -Value "engine"',
        "/health",
        "/predict/image",
        "/training-jobs/",
        "/artifacts",
        "/stop",
        "cleanup",
    )
    for contract in required_contracts:
        assert contract in text


def test_x86_script_checks_nvidia_x86_inventory() -> None:
    text = _text(SCRIPTS["x86_nvidia"])

    assert 'platform_kind -ne "x86_nvidia"' in text
    assert "nvidia-smi" in text
    assert "nvidia_runtime" in text
    assert "tensorrt_fp16_export" in text


def test_jetson_script_checks_jetpack_and_l4t_inventory() -> None:
    text = _text(SCRIPTS["jetson"])

    assert 'platform_kind -ne "jetson"' in text
    assert "/etc/nv_tegra_release" in text
    assert "jetpack_version" in text
    assert "l4t_version" in text
    assert "tensorrt_fp16_export" in text


def test_runbook_documents_safe_execution_and_evidence_rules() -> None:
    runbook = _text(RUNBOOK)
    readme = _text(README)

    assert "accept-edge-x86.ps1" in runbook
    assert "accept-edge-jetson.ps1" in runbook
    assert "ExpectedHostFingerprint" in runbook
    assert "RunHardwareChecks" in runbook
    assert "Read-Host -AsSecureString" in runbook
    assert "pending" in runbook
    assert "不得" in runbook
    assert "visiox.edge-acceptance.v1" in runbook
    assert "ssh-docker-edge-runtime.md" in readme
