from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Protocol

from visiox_paddlex.results import read_evaluation_result


_MODEL_CONFIG_PATHS = {
    "PP-YOLOE-S": "paddlex/configs/modules/object_detection/PP-YOLOE_plus-S.yaml",
    "PP-YOLOE_plus-S": "paddlex/configs/modules/object_detection/PP-YOLOE_plus-S.yaml",
    "RT-DETR-L": "paddlex/configs/modules/object_detection/RT-DETR-L.yaml",
}


@dataclass(frozen=True)
class PaddleXEvaluationRuntimeRequest:
    image_digest: str
    workspace: Path
    config_path: str
    overrides: tuple[str, ...]
    result_path: Path
    device: str

    @property
    def command(self) -> tuple[str, ...]:
        command = [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pids-limit=512",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=1g",
        ]
        if self.device.startswith("gpu:"):
            command.extend(("--gpus", f"device={self.device.removeprefix('gpu:')}"))
        command.extend(
            [
                "-v",
                f"{(self.workspace / 'paddlex_evaluate.py').resolve()}:/runner.py:ro",
                "-v",
                f"{(self.workspace / 'model').resolve()}:/workspace/model:ro",
                "-v",
                f"{(self.workspace / 'dataset').resolve()}:/workspace/dataset:ro",
                "-v",
                f"{(self.workspace / 'output').resolve()}:/workspace/output:rw",
                self.image_digest,
                "python",
                "/runner.py",
                self.config_path,
            ]
        )
        command.extend(self.overrides)
        return tuple(command)


class PaddleXEvaluationRuntime(Protocol):
    def run(self, request: PaddleXEvaluationRuntimeRequest) -> None: ...


class DockerPaddleXEvaluationRuntime:
    def run(self, request: PaddleXEvaluationRuntimeRequest) -> None:
        script = request.workspace / "paddlex_evaluate.py"
        script.write_text(_PADDLEX_EVALUATE_SCRIPT, encoding="utf-8")
        try:
            completed = subprocess.run(
                request.command,
                check=False,
                capture_output=True,
                text=True,
                timeout=3600,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError("PaddleX evaluation runtime could not be started") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout)[-2000:]
            raise RuntimeError(f"PaddleX evaluation failed: {detail}")
        if not request.result_path.is_file():
            raise RuntimeError("PaddleX evaluation did not produce evaluate_result.json")


def paddlex_model_config(runtime_model_id: str) -> str:
    try:
        return _MODEL_CONFIG_PATHS[runtime_model_id]
    except KeyError as exc:
        raise ValueError(f"Unsupported PaddleX runtime model {runtime_model_id!r}") from exc


def paddlex_evaluation_result(path: Path) -> tuple[float | None, dict[str, float]]:
    metrics = read_evaluation_result(path)
    score = metrics.get("detection.map_50")
    if score is None:
        score = metrics.get("detection.map_50_95")
    return score, metrics


_PADDLEX_EVALUATE_SCRIPT = r'''
import json
from pathlib import Path
import re
import subprocess
import sys

config, *overrides = sys.argv[1:]
command = ["python", "/opt/paddlex-runtime/paddlex_main.py", "-c", config]
for override in overrides:
    command.extend(("-o", override))
completed = subprocess.run(command, check=False, capture_output=True, text=True)
Path("/workspace/output/evaluate.log").write_text(
    completed.stdout + "\n" + completed.stderr, encoding="utf-8"
)
if completed.returncode:
    raise SystemExit(completed.returncode)
patterns = {
    "bbox_mAP": r"bbox_mAP(?:_50_95)?\s*[:=]\s*([0-9.]+)",
    "bbox_mAP_50": r"bbox_mAP_50\s*[:=]\s*([0-9.]+)",
    "bbox_mAP_75": r"bbox_mAP_75\s*[:=]\s*([0-9.]+)",
    "bbox_mAP_s": r"bbox_mAP_s\s*[:=]\s*([0-9.]+)",
    "bbox_mAP_m": r"bbox_mAP_m\s*[:=]\s*([0-9.]+)",
    "bbox_mAP_l": r"bbox_mAP_l\s*[:=]\s*([0-9.]+)",
    "bbox_AR_100": r"bbox_AR_100\s*[:=]\s*([0-9.]+)",
}
text = completed.stdout + "\n" + completed.stderr
metrics = {name: float(match.group(1)) for name, pattern in patterns.items() if (match := re.search(pattern, text))}
(Path("/workspace/output") / "evaluate_result.json").write_text(
    json.dumps(metrics), encoding="utf-8"
)
'''
