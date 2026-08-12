from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from visiox_api.services.framework_adapters import (
    FrameworkAdapterCatalog,
    paddlex_operation_implementations,
)
from visiox_common.settings import Settings
from visiox_training.adapters.paddlex import PaddleXAdapter
from visiox_training.contracts import DatasetManifest
from visiox_training.capabilities import (
    FrameworkCapabilities,
    ModelCapability,
    OperationCapability,
    TaskCapability,
    runtime_image_readiness,
)
from visiox_training.errors import (
    UnsupportedAdapterOperationError,
    UnsupportedTaskFrameworkError,
)


VALID_DIGEST = "registry.example/visiox/runtime@sha256:" + "a" * 64


def _settings(**overrides: str) -> Settings:
    return Settings(_env_file=None, **overrides)


def _operation(adapter, name: str):
    task = adapter.capabilities.tasks[0]
    return next(operation for operation in task.operations if operation.name == name)


def test_catalog_is_deterministic_and_filters_only_compatible_adapters() -> None:
    catalog = FrameworkAdapterCatalog(_settings())

    assert tuple(adapter.adapter_key for adapter in catalog.list()) == (
        "llamafactory.llm_sft.v1",
        "paddlex.object_detection.v1",
        "ultralytics.object_detection.v1",
    )
    assert tuple(
        adapter.framework for adapter in catalog.list(task_kind="object_detection")
    ) == ("paddlex", "ultralytics")
    assert tuple(
        adapter.framework for adapter in catalog.list(task_kind="llm_sft")
    ) == ("llamafactory",)
    assert catalog.list(task_kind="unsupported") == ()

    with pytest.raises(UnsupportedTaskFrameworkError):
        catalog.registry.resolve(task_type="llm_sft", framework="paddlex")
    with pytest.raises(UnsupportedTaskFrameworkError):
        catalog.registry.resolve(task_type="object_detection", framework="llamafactory")


def test_catalog_publishes_exact_product_model_choices_and_versions() -> None:
    catalog = FrameworkAdapterCatalog(_settings())
    adapters = {adapter.framework: adapter for adapter in catalog.list()}

    ultralytics = adapters["ultralytics"].capabilities
    assert ultralytics.adapter_version == "1.0.0"
    assert ultralytics.framework_version is None
    assert ultralytics.framework_version_constraint == ">=8.3,<9.0"
    assert ultralytics.base_image_reference is None
    assert ultralytics.runtime_components == ()
    assert [model.runtime_id for model in ultralytics.tasks[0].models] == [
        "yolo26n.pt",
        "yolo26s.pt",
        "yolo26m.pt",
        "yolo26l.pt",
        "yolo26x.pt",
    ]

    paddlex = adapters["paddlex"].capabilities
    assert paddlex.adapter_key == "paddlex.object_detection.v1"
    assert paddlex.framework_version == "3.0.3"
    assert paddlex.framework_version_constraint is None
    assert paddlex.base_image_reference == (
        "nvidia/cuda:11.8.0-base-ubuntu22.04"
        "@sha256:79e5b2cf878ee9006f5b3738caeea34fdc7708a32db53fe3e80db0b48bd286a0"
    )
    assert {item.key: item.value for item in paddlex.runtime_components} == {
        "PaddlePaddle": "3.0.0",
        "Python": "3.10",
        "CUDA": "11.8.0",
        "cuDNN": "8.9.6",
    }
    assert [
        (
            model.display_name,
            model.runtime_id,
            model.family,
            model.variant,
            model.source,
            model.revision,
        )
        for model in paddlex.tasks[0].models
    ] == [
        (
            "PP-YOLOE-S",
            "PP-YOLOE_plus-S",
            "PP-YOLOE",
            "S",
            "paddlex",
            "paddlex-model-zoo/3.0.3/PP-YOLOE_plus-S",
        ),
        (
            "RT-DETR-L",
            "RT-DETR-L",
            "RT-DETR",
            "L",
            "paddlex",
            "paddlex-model-zoo/3.0.3/RT-DETR-L",
        ),
    ]
    assert [parameter.name for parameter in paddlex.tasks[0].parameters] == [
        "epochs",
        "batch_size",
        "learning_rate",
        "image_size",
        "workers",
        "amp",
        "resume",
    ]

    llama = adapters["llamafactory"].capabilities
    assert llama.adapter_key == "llamafactory.llm_sft.v1"
    assert llama.framework_version is None
    assert llama.framework_version_constraint is None
    assert llama.base_image_reference == (
        "hiyouga/llamafactory@sha256:"
        "d1ce6223bf300f3c6bef1bcf02809d3ed4472c1d232a771affd4471d872d6a6b"
    )
    assert llama.runtime_components == ()
    assert [model.runtime_id for model in llama.tasks[0].models] == [
        "Qwen/Qwen3-0.6B",
        "Qwen/Qwen3-1.7B",
        "Qwen/Qwen3-4B",
    ]
    assert {model.sources for model in llama.tasks[0].models} == {
        ("huggingface", "modelscope")
    }


def test_paddlex_models_publish_distinct_safe_yaml_templates() -> None:
    catalog = FrameworkAdapterCatalog(_settings())
    paddlex = next(
        adapter for adapter in catalog.list() if adapter.framework == "paddlex"
    ).capabilities
    models = {model.model_key: model for model in paddlex.tasks[0].models}

    assert set(models) == {"pp-yoloe-s", "rt-detr-l"}
    assert {model.config_format for model in models.values()} == {"yaml"}
    assert models["pp-yoloe-s"].config_template.startswith(
        "# PaddleX runtime model: PP-YOLOE_plus-S\n"
    )
    assert models["rt-detr-l"].config_template.startswith(
        "# PaddleX runtime model: RT-DETR-L\n"
    )
    assert models["pp-yoloe-s"].basic_parameter_names == (
        "epochs",
        "batch_size",
        "learning_rate",
        "image_size",
    )
    assert models["rt-detr-l"].basic_parameter_names == (
        "epochs",
        "batch_size",
        "learning_rate",
        "image_size",
    )
    assert models["pp-yoloe-s"].managed_parameter_names == (
        "mode",
        "model",
        "dataset_dir",
        "output",
        "device",
        "resume_path",
    )

    expected_fields = {
        "epochs",
        "batch_size",
        "learning_rate",
        "image_size",
        "workers",
        "amp",
        "resume",
    }
    parameters = {
        parameter.name: parameter for parameter in paddlex.tasks[0].parameters
    }
    for model in models.values():
        template = yaml.safe_load(model.config_template)
        assert set(template) == expected_fields
        assert template == {
            name: parameter.default for name, parameter in parameters.items()
        }
        for name, value in template.items():
            parameter = parameters[name]
            if parameter.value_type == "boolean":
                assert isinstance(value, bool)
            elif parameter.value_type == "integer":
                assert isinstance(value, int) and not isinstance(value, bool)
            elif parameter.value_type == "number":
                assert isinstance(value, int | float) and not isinstance(value, bool)
            if parameter.minimum is not None:
                assert value >= parameter.minimum
            if parameter.maximum is not None:
                assert value <= parameter.maximum
        assert set(model.basic_parameter_names) <= expected_fields
        assert not set(model.managed_parameter_names) & expected_fields


def test_ultralytics_models_publish_fixed_yaml_and_managed_fields() -> None:
    catalog = FrameworkAdapterCatalog(_settings())
    ultralytics = next(
        adapter for adapter in catalog.list() if adapter.framework == "ultralytics"
    ).capabilities
    models = ultralytics.tasks[0].models

    assert len({model.config_template for model in models}) == 1
    assert {model.config_format for model in models} == {"yaml"}
    assert {model.basic_parameter_names for model in models} == {
        ("epochs", "batch", "imgsz", "lr0")
    }
    assert {model.managed_parameter_names for model in models} == {
        ("task", "mode", "model", "data", "project", "name", "exist_ok", "device")
    }
    parameters = {
        parameter.name: parameter for parameter in ultralytics.tasks[0].parameters
    }
    template = yaml.safe_load(models[0].config_template)
    assert template == {
        name: parameter.default for name, parameter in parameters.items()
    }
    for name, value in template.items():
        parameter = parameters[name]
        if parameter.value_type == "boolean":
            assert isinstance(value, bool)
        elif parameter.value_type == "integer":
            assert isinstance(value, int) and not isinstance(value, bool)
        elif parameter.value_type == "number":
            assert isinstance(value, int | float) and not isinstance(value, bool)
        elif parameter.value_type == "string":
            assert isinstance(value, str)
        if parameter.minimum is not None:
            assert value >= parameter.minimum
        if parameter.maximum is not None:
            assert value <= parameter.maximum
        if parameter.choices:
            assert value in parameter.choices


def test_model_configuration_metadata_is_optional_and_deeply_immutable() -> None:
    plain = ModelCapability(model_key="plain", display_name="Plain")
    configured = ModelCapability(
        model_key="configured",
        display_name="Configured",
        config_format="yaml",
        config_template="epochs: 1\n",
        basic_parameter_names=["epochs"],
        managed_parameter_names=["model"],
    )

    assert plain.config_format is None
    assert plain.config_template is None
    assert plain.basic_parameter_names == ()
    assert plain.managed_parameter_names == ()
    with pytest.raises(TypeError):
        configured.basic_parameter_names[0] = "batch"


def test_ultralytics_parameter_catalog_matches_supported_training_surface() -> None:
    catalog = FrameworkAdapterCatalog(_settings())
    ultralytics = next(
        adapter for adapter in catalog.list() if adapter.framework == "ultralytics"
    ).capabilities

    assert [parameter.name for parameter in ultralytics.tasks[0].parameters] == [
        "epochs",
        "batch",
        "imgsz",
        "lr0",
        "workers",
        "amp",
        "resume",
        "warmup_epochs",
        "patience",
        "save_period",
        "optimizer",
        "cos_lr",
        "close_mosaic",
    ]


def test_capabilities_cover_dataset_parameters_resources_operations_and_outputs() -> (
    None
):
    catalog = FrameworkAdapterCatalog(_settings())

    for adapter in catalog.list():
        capability = adapter.capabilities
        task = capability.tasks[0]
        assert capability.display_name
        assert task.accepted_dataset_formats
        assert task.convertible_dataset_formats
        assert task.parameters
        assert all(parameter.help_text for parameter in task.parameters)
        assert all(parameter.advanced_group for parameter in task.parameters)
        assert task.resources.resource_kinds
        assert {operation.name for operation in task.operations} == {
            "train",
            "stop",
            "resume",
            "evaluate",
            "image_inference",
            "export",
            "deploy",
        }
        if adapter.framework == "paddlex":
            assert all(operation.implemented is True for operation in task.operations)
        elif adapter.framework == "llamafactory":
            assert all(operation.implemented is False for operation in task.operations)
        assert all(operation.available is False for operation in task.operations)
        assert capability.availability_baseline_operation == "train"
        assert capability.available is False
        assert "Tasks 4-12" not in capability.unavailable_reason
        assert task.observable_metrics
        assert task.observable_artifacts

    object_detection = catalog.list(task_kind="object_detection")
    assert all(
        adapter.capabilities.tasks[0].resources.gpu_count_min >= 1
        for adapter in object_detection
    )


def test_empty_and_invalid_training_digests_are_actionably_unavailable() -> None:
    empty = FrameworkAdapterCatalog(_settings())
    invalid = FrameworkAdapterCatalog(
        _settings(
            ultralytics_training_image_digest="registry/image:latest",
            paddlex_training_image_digest="sha256:" + "a" * 64,
            llm_training_image_digest="registry/image@sha256:not-a-digest",
        )
    )

    expected_settings = {
        "ultralytics": "VISIOX_ULTRALYTICS_TRAINING_IMAGE_DIGEST",
        "paddlex": "VISIOX_PADDLEX_TRAINING_IMAGE_DIGEST",
        "llamafactory": "VISIOX_LLM_TRAINING_IMAGE_DIGEST",
    }
    for catalog in (empty, invalid):
        for adapter in catalog.list():
            capability = adapter.capabilities
            env_name = expected_settings[adapter.framework]
            assert capability.available is False
            assert env_name in capability.unavailable_reason
            assert capability.training_runtime_image_digest is None
            train = _operation(adapter, "train")
            assert train.supported is True
            assert train.available is False
            assert env_name in train.unavailable_reason


def test_paddlex_operation_availability_derives_from_wiring_and_runtime_digests() -> None:
    training_ready = FrameworkAdapterCatalog(
        _settings(
            ultralytics_training_image_digest=VALID_DIGEST,
            paddlex_training_image_digest=VALID_DIGEST,
            llm_training_image_digest=VALID_DIGEST,
        )
    )
    paddlex = next(
        adapter for adapter in training_ready.list() if adapter.framework == "paddlex"
    )

    assert paddlex.capabilities.available is True
    assert paddlex.capabilities.training_runtime_image_digest == VALID_DIGEST
    assert _operation(paddlex, "train").supported is True
    assert _operation(paddlex, "train").implemented is True
    assert _operation(paddlex, "train").available is True
    assert _operation(paddlex, "train").unavailable_reason is None
    assert _operation(paddlex, "evaluate").available is True
    assert _operation(paddlex, "export").available is True
    assert _operation(paddlex, "deploy").available is False
    assert (
        "VISIOX_PADDLEX_INFERENCE_IMAGE_DIGEST"
        in _operation(paddlex, "deploy").unavailable_reason
    )

    fully_ready = FrameworkAdapterCatalog(
        _settings(
            paddlex_training_image_digest=VALID_DIGEST,
            paddlex_inference_image_digest=VALID_DIGEST,
        )
    )
    paddlex = next(
        adapter for adapter in fully_ready.list() if adapter.framework == "paddlex"
    )
    assert paddlex.capabilities.available is True
    assert _operation(paddlex, "deploy").supported is True
    assert _operation(paddlex, "deploy").implemented is True
    assert _operation(paddlex, "deploy").available is True
    assert _operation(paddlex, "image_inference").available is True
    assert paddlex.capabilities.inference_runtime_image_digest == VALID_DIGEST


def test_paddlex_digests_do_not_enable_unregistered_operations() -> None:
    adapter = PaddleXAdapter(
        VALID_DIGEST,
        VALID_DIGEST,
        implemented_operations=set(),
    )

    assert all(
        operation.implemented is False
        for operation in adapter.capabilities.tasks[0].operations
    )
    assert adapter.capabilities.available is False


def test_paddlex_operation_registry_references_real_callables() -> None:
    implementations = paddlex_operation_implementations()

    assert set(implementations) == {
        "train",
        "stop",
        "resume",
        "evaluate",
        "image_inference",
        "export",
        "deploy",
    }
    assert all(callable(handler) for handler in implementations.values())


def test_catalog_adapters_fail_unwired_execution_actionably() -> None:
    adapter = FrameworkAdapterCatalog(_settings()).list()[0]

    with pytest.raises(UnsupportedAdapterOperationError, match="Task 3"):
        adapter.validate_dataset(
            manifest=DatasetManifest(
                dataset_id="dataset-1",
                task_type="llm_sft",
                format="alpaca",
                uri="memory://dataset-1",
            )
        )
    with pytest.raises(UnsupportedAdapterOperationError, match="not wired"):
        adapter.collect_artifacts(Path("output"))


def test_catalog_capability_sequences_remain_deeply_immutable() -> None:
    capabilities = tuple(
        adapter.capabilities for adapter in FrameworkAdapterCatalog(_settings()).list()
    )
    capability = capabilities[0]
    paddlex = next(item for item in capabilities if item.framework == "paddlex")

    with pytest.raises(TypeError):
        capability.tasks[0].models[0] = capability.tasks[0].models[0]
    with pytest.raises(TypeError):
        paddlex.runtime_components[0] = paddlex.runtime_components[0]


@pytest.mark.parametrize(
    "capability",
    [
        OperationCapability(
            name="train",
            supported=True,
            implemented=True,
            available=True,
        ),
        FrameworkCapabilities(
            adapter_key="fake.v1",
            adapter_version="1.0.0",
            framework="fake",
            tasks=(TaskCapability(task_type="fake"),),
        ),
    ],
)
def test_availability_models_reject_inconsistent_updates(capability) -> None:
    with pytest.raises(ValidationError):
        capability.model_copy(
            update={
                "available": True,
                "unavailable_reason": "configured runtime is unavailable",
            }
        )


def test_operation_rejects_available_but_unsupported_state() -> None:
    with pytest.raises(ValidationError):
        OperationCapability(
            name="deploy",
            supported=False,
            implemented=False,
            available=True,
        )


@pytest.mark.parametrize(
    "value",
    [
        " registry.example/visiox/runtime@sha256:" + "a" * 64,
        "registry.example/visiox/runtime@sha256:" + "a" * 64 + " ",
        "registry.example/visiox run@sha256:" + "a" * 64,
        "registry.example/visiox/runtime@sha256:" + "a" * 63 + "\n",
        "registry.example/visiox/runtime\x00@sha256:" + "a" * 64,
        "https://registry.example/visiox/runtime@sha256:" + "a" * 64,
        "registry.example/visiox/runtime@@sha256:" + "a" * 64,
        "@sha256:" + "a" * 64,
        "registry.example//runtime@sha256:" + "a" * 64,
        "/registry.example/runtime@sha256:" + "a" * 64,
        "registry.example/runtime/@sha256:" + "a" * 64,
        "Registry.example/visiox/runtime@sha256:" + "a" * 64,
        "registry.example/visiox/Runtime@sha256:" + "a" * 64,
        "registry.example/visiox/image..bad@sha256:" + "a" * 64,
        "registry.example/visiox/-image@sha256:" + "a" * 64,
        "registry.example:abc/visiox/runtime@sha256:" + "a" * 64,
        "registry.example:0/visiox/runtime@sha256:" + "a" * 64,
        "registry.example:65536/visiox/runtime@sha256:" + "a" * 64,
        "registry.example/visiox/runtime:latest@sha256:" + "a" * 64,
        "registry.example/visiox/runtime@sha256:" + "a" * 63,
        "registry.example/visiox/runtime@sha512:" + "a" * 64,
    ],
)
def test_runtime_image_readiness_rejects_malformed_oci_references(value: str) -> None:
    reference, reason = runtime_image_readiness(value, "VISIOX_TEST_IMAGE_DIGEST")

    assert reference is None
    assert "VISIOX_TEST_IMAGE_DIGEST" in reason


@pytest.mark.parametrize(
    "name",
    [
        "registry.example/visiox/runtime",
        "127.0.0.1:5000/visiox/runtime",
        "registry:5000/visiox/runtime",
        "localhost:5000/runtime",
        "paddlepaddle/paddle",
    ],
)
def test_runtime_image_readiness_accepts_normal_names_and_canonicalizes_digest(
    name: str,
) -> None:
    reference, reason = runtime_image_readiness(
        f"{name}@sha256:{'A' * 64}", "VISIOX_TEST_IMAGE_DIGEST"
    )

    assert reference == f"{name}@sha256:{'a' * 64}"
    assert reason is None


def test_framework_capability_runtime_digest_fields_are_strict_and_canonical() -> None:
    fields = {
        "adapter_key": "fake.v1",
        "adapter_version": "1.0.0",
        "framework": "fake",
        "tasks": (TaskCapability(task_type="fake"),),
    }

    with pytest.raises(ValidationError):
        FrameworkCapabilities(
            **fields,
            training_runtime_image_digest="registry/image:latest",
        )

    capability = FrameworkCapabilities(
        **fields,
        training_runtime_image_digest=(
            "registry.example/visiox/runtime@sha256:" + "A" * 64
        ),
    )
    assert capability.training_runtime_image_digest == (
        "registry.example/visiox/runtime@sha256:" + "a" * 64
    )
