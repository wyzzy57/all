from __future__ import annotations

import importlib
import json

import pytest


def _config_module():
    return importlib.import_module("visiox_paddlex_training_worker.config")


def _launch_spec(
    *,
    model_id: str = "RT-DETR-L",
    parameters: dict[str, object] | None = None,
) -> dict[str, object]:
    runtime_inputs = {
        "parameters": parameters
        or {
            "epochs": 12,
            "batch_size": 4,
            "learning_rate": 0.0005,
            "image_size": 640,
            "workers": 6,
            "amp": True,
            "devices": [0, 2],
            "resume": True,
        },
        "model": {
            "source": "paddlex",
            "id": model_id,
            "runtime_id": model_id,
            "revision": f"paddlex-model-zoo/3.0.3/{model_id}",
        },
        "dataset": {
            "id": "dataset-1",
            "version_id": "version-1",
            "format": "coco",
            "manifest_checksum": "b" * 64,
        },
        "artifacts": [
            {"role": "dataset", "path": "/workspace/dataset"},
            {"role": "checkpoint", "path": "/workspace/checkpoint/last.pt"},
        ],
    }
    return {
        "schema_version": "1.0",
        "adapter_key": "paddlex.object_detection.v1",
        "adapter_version": "1.0.0",
        "argv": ["/usr/local/bin/visiox-train"],
        "env": {
            "VISIOX_RUNTIME_INPUTS_JSON": json.dumps(runtime_inputs),
            "VISIOX_TRAINING_JOB_ID": "job-1",
            "VISIOX_OUTPUT_DIR": "/workspace/output",
        },
        "working_directory": "workspace",
    }


@pytest.mark.parametrize(
    ("model_id", "expected_path"),
    [
        (
            "PP-YOLOE-S",
            "paddlex/configs/modules/object_detection/PP-YOLOE_plus-S.yaml",
        ),
        (
            "RT-DETR-L",
            "paddlex/configs/modules/object_detection/RT-DETR-L.yaml",
        ),
    ],
)
def test_model_maps_to_prescribed_paddlex_config(
    model_id: str,
    expected_path: str,
) -> None:
    config = _config_module().build_training_config(_launch_spec(model_id=model_id))

    assert config.config_path == expected_path


def test_typed_parameters_build_an_argv_command_with_managed_paths() -> None:
    module = _config_module()
    config = module.build_training_config(_launch_spec())

    command = module.build_training_command(config)

    assert isinstance(command, tuple)
    assert command == (
        "python",
        "/opt/paddlex-runtime/paddlex_main.py",
        "-c",
        "paddlex/configs/modules/object_detection/RT-DETR-L.yaml",
        "-o",
        "Global.mode=train",
        "-o",
        "Global.dataset_dir=/workspace/dataset",
        "-o",
        "Global.output=/workspace/output",
        "-o",
        "Global.device=gpu:0,2",
        "-o",
        "Train.epochs_iters=12",
        "-o",
        "Train.batch_size=4",
        "-o",
        "Train.learning_rate=0.0005",
        "-o",
        "Train.amp=O1",
        "-o",
        "Train.resume_path=/workspace/output/.resume/last.pdparams",
        "-o",
        "Train.eval_interval=1",
    )
    assert all("\n" not in argument for argument in command)


def test_export_command_uses_trained_best_weights_and_static_bundle_dir() -> None:
    module = _config_module()
    config = module.build_training_config(_launch_spec())

    assert module.build_export_command(config) == (
        "python",
        "/opt/paddlex-runtime/paddlex_main.py",
        "-c",
        "paddlex/configs/modules/object_detection/RT-DETR-L.yaml",
        "-o",
        "Global.mode=export",
        "-o",
        "Global.output=/workspace/output/best_model/inference",
        "-o",
        "Global.device=gpu:0,2",
        "-o",
        "Export.weight_path=/workspace/output/best_model/best_model.pdparams",
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("epochs", 0),
        ("batch_size", "4"),
        ("learning_rate", True),
        ("image_size", 0),
        ("workers", -1),
        ("amp", "yes"),
        ("devices", "0,1"),
        ("devices", [0, 0]),
        ("resume", "checkpoint"),
    ],
)
def test_typed_parameters_reject_invalid_values(field: str, value: object) -> None:
    parameters = {
        "epochs": 2,
        "batch_size": 4,
        "learning_rate": 0.001,
        "image_size": 640,
        "workers": 2,
        "amp": False,
        "devices": [0],
        "resume": False,
        field: value,
    }

    with pytest.raises(ValueError, match=field):
        _config_module().build_training_config(_launch_spec(parameters=parameters))


@pytest.mark.parametrize(
    "managed_field",
    [
        "Global.mode",
        "Global.dataset_dir",
        "Global.output",
        "Global.device",
        "Train.resume_path",
        "Train.eval_interval",
        "Train.amp",
    ],
)
def test_managed_field_overrides_are_rejected(managed_field: str) -> None:
    parameters = {
        "epochs": 2,
        "batch_size": 4,
        "learning_rate": 0.001,
        "image_size": 640,
        "workers": 2,
        "amp": False,
        "devices": [0],
        "resume": False,
        "overrides": {managed_field: "attacker-controlled"},
    }

    with pytest.raises(ValueError, match="managed"):
        _config_module().build_training_config(_launch_spec(parameters=parameters))


def test_unknown_parameter_is_rejected_instead_of_becoming_a_shell_fragment() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        _config_module().build_training_config(
            _launch_spec(parameters={"epochs": 2, "command": "; rm -rf /"})
        )
