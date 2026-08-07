import json
import shutil
import subprocess
from pathlib import Path

import pytest


RUNTIME_DIGESTS = {
    "VISIOX_DEPLOYMENT_IMAGE_DIGEST": "registry.example/visiox/yolo26-inference@sha256:" + "a" * 64,
    "VISIOX_ULTRALYTICS_TRAINING_IMAGE_DIGEST": "registry.example/visiox/ultralytics-training@sha256:" + "b" * 64,
    "VISIOX_PADDLEX_TRAINING_IMAGE_DIGEST": "registry.example/visiox/paddlex-training@sha256:" + "c" * 64,
    "VISIOX_PADDLEX_INFERENCE_IMAGE_DIGEST": "registry.example/visiox/paddlex-inference@sha256:" + "d" * 64,
    "VISIOX_LLM_TRAINING_IMAGE_DIGEST": "registry.example/visiox/llm-training@sha256:" + "e" * 64,
}


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker CLI is required")
def test_compose_preserves_runtime_digests_from_api_env_file(tmp_path):
    env_file = tmp_path / "runtime.env"
    env_file.write_text(
        "\n".join(f"{key}={value}" for key, value in RUNTIME_DIGESTS.items()),
        encoding="utf-8",
    )
    override = tmp_path / "runtime-compose.yml"
    override.write_text(
        "services:\n  api-service:\n    env_file:\n      - "
        + env_file.as_posix()
        + "\n",
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            "infra/compose/docker-compose.yml",
            "-f",
            str(override),
            "config",
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    environment = json.loads(completed.stdout)["services"]["api-service"]["environment"]

    assert {key: environment[key] for key in RUNTIME_DIGESTS} == RUNTIME_DIGESTS


def test_paddlex_runbook_uses_authenticated_capabilities_endpoint():
    runbook = Path("docs/operations/paddlex-object-detection-runbook.md").read_text(
        encoding="utf-8"
    )

    assert "/frameworks/capabilities" in runbook
    assert "Authorization = \"Bearer $($login.access_token)\"" in runbook


def test_control_plane_image_uses_a_reliable_configurable_package_mirror():
    dockerfile = Path("apps/api-service/Dockerfile").read_text(encoding="utf-8")

    assert "ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple" in dockerfile
    assert "PIP_INDEX_URL=${PIP_INDEX_URL}" in dockerfile
