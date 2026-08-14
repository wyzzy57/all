from __future__ import annotations

import json
from pathlib import Path


def write_train_result(
    output_dir: Path,
    *,
    status: str,
    exit_code: int,
) -> Path:
    artifacts = []
    for path in sorted(output_dir.rglob("*")):
        relative = path.relative_to(output_dir)
        if relative.parts and relative.parts[0] == ".resume":
            continue
        if not path.is_file() or path.name in {
            "artifact-manifest.json",
            "train_result.json",
        }:
            continue
        role = _artifact_role(relative)
        if role is not None:
            artifacts.append(
                {
                    "role": role,
                    "path": relative.as_posix(),
                }
            )
    result_path = output_dir / "train_result.json"
    temporary = result_path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "framework": "paddlex",
                "status": status,
                "exit_code": exit_code,
                "artifacts": artifacts,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    temporary.replace(result_path)
    return result_path


def _artifact_role(relative: Path) -> str | None:
    value = relative.as_posix().lower()
    if relative.name == "train.log":
        return "train_log"
    if relative.name in {"config.yaml", "config.yml"}:
        return "config"
    if "best_model/" in value and relative.suffix == ".pdparams":
        return "best_dynamic_weights"
    if "best_model/inference" in value and (
        relative.suffix in {".json", ".pdmodel", ".pdiparams", ".yml", ".yaml"}
        or relative.name.endswith(".pdiparams.info")
    ):
        return "best_static_inference"
    if ("last" in value or "model_final" in value) and relative.suffix == ".pdparams":
        return "last_weights"
    if (
        "checkpoint" in value
        or any(part.isdigit() for part in relative.parts[:-1])
    ) and relative.suffix == ".pdparams":
        return "checkpoint_weights"
    if ("eval" in value or "metric" in value) and relative.suffix in {
        ".json",
        ".csv",
    }:
        return "evaluation_report"
    if relative.suffix.lower() in {".png", ".jpg", ".jpeg"}:
        return "visualization"
    if "tfevents" in relative.name.lower():
        return "tensorboard_event"
    return None
