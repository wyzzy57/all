from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
from typing import Any


IMAGE_SIZE_ENV = "VISIOX_PADDLEX_IMAGE_SIZE"
WORKERS_ENV = "VISIOX_PADDLEX_WORKERS"
VISUALDL_DIR_ENV = "VISIOX_PADDLEX_VDL_DIR"


def apply_runtime_config(
    config: Any,
    *,
    image_size: int,
    workers: int,
    visualdl_dir: str,
) -> None:
    config.update_num_workers(workers)
    config["eval_size"] = [image_size, image_size]
    for dataset_name in ("TrainDataset", "EvalDataset"):
        if dataset_name in config and isinstance(config[dataset_name], dict):
            config[dataset_name]["image_dir"] = ""
    _set_reader_resize(config, "TrainReader", image_size, batch_random=True)
    _set_reader_resize(config, "EvalReader", image_size, batch_random=False)
    _set_reader_resize(config, "TestReader", image_size, batch_random=False)
    test_reader = config["TestReader"] if "TestReader" in config else None
    if isinstance(test_reader, dict):
        inputs_def = test_reader.get("inputs_def")
        if isinstance(inputs_def, dict) and "image_shape" in inputs_def:
            inputs_def["image_shape"] = [3, image_size, image_size]
    config["use_vdl"] = True
    config["vdl_log_dir"] = visualdl_dir
    config["output_eval"] = str(PurePosixPath(visualdl_dir).parent)


def _set_reader_resize(
    config: Any,
    reader_name: str,
    image_size: int,
    *,
    batch_random: bool,
) -> None:
    if reader_name not in config:
        return
    operation_name = "BatchRandomResize" if batch_random else "Resize"
    target_size = [image_size] if batch_random else [image_size, image_size]
    _replace_target_size(config[reader_name], operation_name, target_size)


def _replace_target_size(node: Any, operation_name: str, target_size: list[int]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == operation_name and isinstance(value, dict):
                value["target_size"] = list(target_size)
            else:
                _replace_target_size(value, operation_name, target_size)
    elif isinstance(node, list):
        for value in node:
            _replace_target_size(value, operation_name, target_size)


def _required_integer(name: str) -> int:
    value = int(os.environ[name])
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


def _patch_detection_trainer() -> None:
    from paddlex.modules.object_detection.trainer import DetTrainer

    original = DetTrainer.update_config

    def update_config(self: Any) -> None:
        original(self)
        visualdl_dir = os.environ[VISUALDL_DIR_ENV]
        Path(visualdl_dir).mkdir(parents=True, exist_ok=True)
        apply_runtime_config(
            self.pdx_config,
            image_size=_required_integer(IMAGE_SIZE_ENV),
            workers=_required_integer(WORKERS_ENV),
            visualdl_dir=visualdl_dir,
        )

    DetTrainer.update_config = update_config


def main() -> None:
    _patch_detection_trainer()
    from paddlex.engine import Engine

    Engine().run()


if __name__ == "__main__":
    main()
