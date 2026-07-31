from __future__ import annotations

import json
import warnings
from datetime import UTC, datetime
from datetime import timedelta, timezone

import pytest
from pydantic import ValidationError

from visiox_training.capabilities import (
    FrameworkCapabilities,
    ModelCapability,
    ParameterCapability,
    ResourceCapability,
    TaskCapability,
)
from visiox_training.contracts import (
    ArtifactEntry,
    ArtifactManifest,
    DatasetManifest,
    LaunchSpec,
    TelemetryEnvelope,
)


def test_contracts_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DatasetManifest(
            dataset_id="dataset-1",
            task_type="object_detection",
            format="yolo",
            uri="s3://datasets/dataset-1",
            unexpected=True,
        )


@pytest.mark.parametrize(
    ("model", "fields"),
    [
        (
            ArtifactEntry,
            {
                "path": "weights/best.pt",
                "size_bytes": "1",
                "checksum_sha256": "a" * 64,
                "artifact_type": "model",
            },
        ),
        (
            TelemetryEnvelope,
            {
                "adapter_key": "fake.v1",
                "adapter_version": "1.0.0",
                "task_id": "task-1",
                "sequence": "1",
                "timestamp": datetime(2026, 7, 31, tzinfo=UTC),
                "event_type": "metrics",
            },
        ),
        (
            TelemetryEnvelope,
            {
                "adapter_key": "fake.v1",
                "adapter_version": "1.0.0",
                "task_id": "task-1",
                "sequence": 1,
                "timestamp": "2026-07-31T00:00:00Z",
                "event_type": "metrics",
            },
        ),
        (
            ParameterCapability,
            {
                "name": "epochs",
                "value_type": "integer",
                "required": 1,
            },
        ),
    ],
)
def test_contracts_and_capabilities_reject_coercible_values(
    model: type[object], fields: dict[str, object]
) -> None:
    with pytest.raises(ValidationError):
        model(**fields)  # type: ignore[operator]


@pytest.mark.parametrize(
    "invalid", [object(), {"items"}, float("nan"), float("inf"), -float("inf")]
)
def test_checksum_payloads_reject_non_json_or_non_finite_values(
    invalid: object,
) -> None:
    with pytest.raises(ValidationError):
        DatasetManifest(
            dataset_id="dataset-1",
            task_type="object_detection",
            format="yolo",
            uri="s3://datasets/dataset-1",
            metadata={"invalid": invalid},
        )

    with pytest.raises(ValidationError):
        TelemetryEnvelope(
            adapter_key="fake.v1",
            adapter_version="1.0.0",
            task_id="task-1",
            sequence=1,
            timestamp=datetime(2026, 7, 31, tzinfo=UTC),
            event_type="metrics",
            payload={"invalid": invalid},
        )

    with pytest.raises(ValidationError):
        ParameterCapability(
            name="value",
            value_type="number",
            default=invalid,
        )


def test_nested_contract_payloads_are_isolated_and_deeply_immutable() -> None:
    metadata = {"split": {"train": ["a.jpg", "b.jpg"]}}
    manifest = DatasetManifest(
        dataset_id="dataset-1",
        task_type="object_detection",
        format="yolo",
        uri="s3://datasets/dataset-1",
        metadata=metadata,
    )
    checksum = manifest.canonical_checksum_sha256()

    metadata["split"]["train"].append("external.jpg")  # type: ignore[index,union-attr]
    with pytest.raises(TypeError):
        manifest.metadata["new"] = True  # type: ignore[index]
    with pytest.raises(TypeError):
        manifest.metadata["split"]["train"][0] = "changed.jpg"  # type: ignore[index]

    assert manifest.canonical_checksum_sha256() == checksum
    assert manifest.model_dump(mode="json")["metadata"] == {
        "split": {"train": ["a.jpg", "b.jpg"]}
    }


def test_launch_environment_and_capability_defaults_are_deeply_immutable() -> None:
    launch = LaunchSpec(
        adapter_key="fake.v1",
        adapter_version="1.0.0",
        argv=("train",),
        env={"DEVICE": "0"},
    )
    parameter = ParameterCapability(
        name="schedule",
        value_type="string",
        default={"stages": [{"name": "warmup"}]},
    )

    with pytest.raises(TypeError):
        launch.env["DEVICE"] = "1"  # type: ignore[index]
    with pytest.raises(TypeError):
        parameter.default["stages"][0]["name"] = "changed"  # type: ignore[index]

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        dumped_default = parameter.model_dump(mode="json")["default"]

    assert dumped_default == {"stages": [{"name": "warmup"}]}


def test_contracts_keep_normal_json_schema_and_dump_behavior() -> None:
    manifest = DatasetManifest(
        dataset_id="dataset-1",
        task_type="object_detection",
        format="yolo",
        uri="s3://datasets/dataset-1",
        metadata={"classes": ["pepper"]},
    )

    assert (
        DatasetManifest.model_json_schema()["properties"]["metadata"]["type"]
        == "object"
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        dumped = manifest.model_dump(mode="json")

    assert dumped["metadata"] == {"classes": ["pepper"]}
    assert DatasetManifest.model_validate_json(manifest.model_dump_json()) == manifest


def test_model_copy_updates_are_revalidated_and_deeply_frozen() -> None:
    manifest = DatasetManifest(
        dataset_id="dataset-1",
        task_type="object_detection",
        format="yolo",
        uri="s3://datasets/dataset-1",
        metadata={"classes": ["pepper"]},
    )
    replacement = {"classes": ["leaf"]}

    copied = manifest.model_copy(update={"metadata": replacement})
    checksum = copied.canonical_checksum_sha256()
    replacement["classes"].append("external")

    with pytest.raises(TypeError):
        copied.metadata["classes"][0] = "changed"  # type: ignore[index]
    assert copied.model_dump(mode="json")["metadata"] == {"classes": ["leaf"]}
    assert copied.canonical_checksum_sha256() == checksum


@pytest.mark.parametrize("invalid", [object(), {"items"}, float("nan")])
def test_model_copy_rejects_invalid_json_updates(invalid: object) -> None:
    manifest = DatasetManifest(
        dataset_id="dataset-1",
        task_type="object_detection",
        format="yolo",
        uri="s3://datasets/dataset-1",
    )

    with pytest.raises(ValidationError):
        manifest.model_copy(update={"metadata": {"invalid": invalid}})


def test_model_copy_rejects_invalid_checksum_updates() -> None:
    manifest = ArtifactManifest(
        task_id="task-1",
        adapter_key="fake.v1",
        adapter_version="1.0.0",
    )

    with pytest.raises(ValidationError):
        manifest.model_copy(update={"checksum_sha256": "invalid"})


def test_model_copy_without_updates_preserves_safe_immutable_model() -> None:
    parameter = ParameterCapability(
        name="schedule",
        value_type="string",
        default={"stages": ["warmup"]},
    )

    copied = parameter.model_copy()

    assert copied == parameter
    with pytest.raises(TypeError):
        copied.default["stages"][0] = "changed"  # type: ignore[index]


@pytest.mark.parametrize(
    "model",
    [
        DatasetManifest(
            dataset_id="dataset-1",
            task_type="object_detection",
            format="yolo",
            uri="s3://datasets/dataset-1",
            metadata={"split": {"train": ["a.jpg"]}},
        ),
        LaunchSpec(
            adapter_key="fake.v1",
            adapter_version="1.0.0",
            argv=("train", "--epochs", "1"),
            env={"DEVICE": "0"},
            working_directory="runs/task-1",
        ),
        TelemetryEnvelope(
            adapter_key="fake.v1",
            adapter_version="1.0.0",
            task_id="task-1",
            sequence=1,
            timestamp=datetime(2026, 7, 31, tzinfo=UTC),
            event_type="metrics",
            payload={"metrics": {"losses": [0.25]}},
        ),
        ArtifactManifest(
            task_id="task-1",
            adapter_key="fake.v1",
            adapter_version="1.0.0",
            artifacts=(
                ArtifactEntry(
                    path="weights/best.pt",
                    size_bytes=1,
                    checksum_sha256="a" * 64,
                    artifact_type="model",
                ),
            ),
        ),
    ],
)
def test_canonical_model_copy_deep_is_checksum_stable(model: object) -> None:
    copied = model.model_copy(deep=True)  # type: ignore[attr-defined]

    assert copied is not model
    assert copied == model
    assert (
        copied.canonical_checksum_sha256()  # type: ignore[attr-defined]
        == model.canonical_checksum_sha256()  # type: ignore[attr-defined]
    )


def test_canonical_model_copy_deep_remains_deeply_immutable() -> None:
    dataset = DatasetManifest(
        dataset_id="dataset-1",
        task_type="object_detection",
        format="yolo",
        uri="s3://datasets/dataset-1",
        metadata={"split": {"train": ["a.jpg"]}},
    ).model_copy(deep=True)
    launch = LaunchSpec(
        adapter_key="fake.v1",
        adapter_version="1.0.0",
        argv=("train",),
        env={"DEVICE": "0"},
    ).model_copy(deep=True)
    telemetry = TelemetryEnvelope(
        adapter_key="fake.v1",
        adapter_version="1.0.0",
        task_id="task-1",
        sequence=1,
        timestamp=datetime(2026, 7, 31, tzinfo=UTC),
        event_type="metrics",
        payload={"metrics": {"losses": [0.25]}},
    ).model_copy(deep=True)
    artifacts = ArtifactManifest(
        task_id="task-1",
        adapter_key="fake.v1",
        adapter_version="1.0.0",
        artifacts=(
            ArtifactEntry(
                path="weights/best.pt",
                size_bytes=1,
                checksum_sha256="a" * 64,
                artifact_type="model",
            ),
        ),
    ).model_copy(deep=True)

    with pytest.raises(TypeError):
        dataset.metadata["split"]["train"][0] = "changed.jpg"  # type: ignore[index]
    with pytest.raises(TypeError):
        launch.env["DEVICE"] = "1"  # type: ignore[index]
    with pytest.raises(TypeError):
        launch.argv[0] = "changed"  # type: ignore[index]
    with pytest.raises(TypeError):
        telemetry.payload["metrics"]["losses"][0] = 1.0  # type: ignore[index]
    with pytest.raises(TypeError):
        artifacts.artifacts[0] = artifacts.artifacts[0]  # type: ignore[index]


def test_capability_model_copy_deep_with_nested_default_is_safe() -> None:
    parameter = ParameterCapability(
        name="schedule",
        value_type="string",
        default={"stages": [{"name": "warmup"}]},
    )

    copied = parameter.model_copy(deep=True)

    assert copied is not parameter
    assert copied == parameter
    assert copied.model_dump(mode="json") == parameter.model_dump(mode="json")
    with pytest.raises(TypeError):
        copied.default["stages"][0]["name"] = "changed"  # type: ignore[index]


def test_capability_model_copy_revalidates_and_freezes_updates() -> None:
    parameter = ParameterCapability(name="schedule", value_type="string")
    replacement = {"stages": ["warmup"]}

    copied = parameter.model_copy(update={"default": replacement})
    replacement["stages"].append("external")

    assert copied.model_dump(mode="json")["default"] == {"stages": ["warmup"]}
    with pytest.raises(ValidationError):
        parameter.model_copy(update={"required": 1})


def test_equal_telemetry_instants_normalize_to_utc_and_hash_identically() -> None:
    base = {
        "schema_version": "1.0",
        "adapter_key": "fake.v1",
        "adapter_version": "1.0.0",
        "task_id": "task-1",
        "sequence": 1,
        "event_type": "metrics",
        "payload": {"loss": 0.25},
    }
    timestamps = (
        "2026-07-31T12:34:56.123456Z",
        "2026-07-31T20:34:56.123456+08:00",
        "2026-07-31T07:34:56.123456-05:00",
    )
    envelopes = tuple(
        TelemetryEnvelope.model_validate_json(
            json.dumps({**base, "timestamp": timestamp})
        )
        for timestamp in timestamps
    )

    assert all(envelope.timestamp.tzinfo is UTC for envelope in envelopes)
    assert {envelope.canonical_checksum_sha256() for envelope in envelopes} == {
        envelopes[0].canonical_checksum_sha256()
    }
    assert {
        envelope.model_dump(mode="json")["timestamp"] for envelope in envelopes
    } == {"2026-07-31T12:34:56.123456Z"}


def test_python_datetime_offsets_also_normalize_to_utc() -> None:
    envelope = TelemetryEnvelope(
        adapter_key="fake.v1",
        adapter_version="1.0.0",
        task_id="task-1",
        sequence=1,
        timestamp=datetime(
            2026,
            7,
            31,
            20,
            34,
            56,
            123456,
            tzinfo=timezone(timedelta(hours=8)),
        ),
        event_type="metrics",
    )

    assert envelope.timestamp == datetime(2026, 7, 31, 12, 34, 56, 123456, tzinfo=UTC)
    assert envelope.timestamp.tzinfo is UTC


@pytest.mark.parametrize(
    "working_directory",
    [
        "",
        ".",
        "..",
        "./runs",
        "runs/../secret",
        "runs\\task-1",
        "C:/runs/task-1",
        "/runs/task-1",
        "runs//task-1",
        "runs/\x00task-1",
    ],
)
def test_launch_spec_rejects_unsafe_working_directories(
    working_directory: str,
) -> None:
    with pytest.raises(ValidationError):
        LaunchSpec(
            adapter_key="fake.v1",
            adapter_version="1.0.0",
            argv=("train",),
            working_directory=working_directory,
        )


def test_launch_spec_accepts_safe_relative_posix_working_directory() -> None:
    launch = LaunchSpec(
        adapter_key="fake.v1",
        adapter_version="1.0.0",
        argv=("train",),
        working_directory="runs/task-1",
    )

    assert launch.working_directory == "runs/task-1"


@pytest.mark.parametrize(
    "env",
    [
        {"": "value"},
        {"BAD=KEY": "value"},
        {"BAD\x00KEY": "value"},
        {"GOOD_KEY": "bad\x00value"},
    ],
)
def test_launch_spec_rejects_unsafe_environment_entries(env: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        LaunchSpec(
            adapter_key="fake.v1",
            adapter_version="1.0.0",
            argv=("train",),
            env=env,
        )


def test_equivalent_mapping_order_produces_stable_json_and_checksum() -> None:
    first = DatasetManifest(
        dataset_id="dataset-1",
        task_type="object_detection",
        format="yolo",
        uri="s3://datasets/dataset-1",
        metadata={"classes": ["pepper", "leaf"], "split": {"train": 8, "val": 2}},
    )
    second = DatasetManifest(
        uri="s3://datasets/dataset-1",
        format="yolo",
        task_type="object_detection",
        dataset_id="dataset-1",
        metadata={"split": {"val": 2, "train": 8}, "classes": ["pepper", "leaf"]},
    )

    assert first.schema_version == "1.0"
    assert first.canonical_json() == second.canonical_json()
    assert first.canonical_checksum_sha256() == second.canonical_checksum_sha256()
    assert len(first.canonical_checksum_sha256()) == 64


def test_manifest_checksum_does_not_digest_its_checksum_field() -> None:
    entry = ArtifactEntry(
        path="weights/best.pt",
        size_bytes=12,
        checksum_sha256="a" * 64,
        artifact_type="model",
    )
    first = ArtifactManifest(
        task_id="task-1",
        adapter_key="ultralytics.object_detection.v1",
        adapter_version="1.0.0",
        artifacts=(entry,),
        checksum_sha256="b" * 64,
    )
    second = first.model_copy(update={"checksum_sha256": "c" * 64})

    assert first.canonical_checksum_sha256() == second.canonical_checksum_sha256()


@pytest.mark.parametrize(
    "path",
    [
        "/etc/passwd",
        "../secret",
        "weights/../../secret",
        r"C:\\models\\best.pt",
        "C:/models/best.pt",
        r"weights\\best.pt",
        "./weights/best.pt",
        "weights//best.pt",
        "",
    ],
)
def test_artifact_entries_reject_unsafe_or_noncanonical_paths(path: str) -> None:
    with pytest.raises(ValidationError):
        ArtifactEntry(
            path=path,
            size_bytes=1,
            checksum_sha256="a" * 64,
            artifact_type="model",
        )


def test_artifact_entry_requires_bounded_collection_metadata() -> None:
    entry = ArtifactEntry(
        path="weights/best.pt",
        size_bytes=0,
        checksum_sha256="A" * 64,
        artifact_type="model",
    )

    assert entry.path == "weights/best.pt"
    assert entry.size_bytes == 0
    assert entry.checksum_sha256 == "a" * 64
    assert entry.artifact_type == "model"


def test_launch_spec_requires_structured_argv_and_adapter_identity() -> None:
    launch = LaunchSpec(
        adapter_key="ultralytics.object_detection.v1",
        adapter_version="1.0.0",
        argv=("yolo", "train", "epochs=10"),
        env={"CUDA_VISIBLE_DEVICES": "0"},
    )

    assert launch.schema_version == "1.0"
    assert launch.argv == ("yolo", "train", "epochs=10")

    with pytest.raises(ValidationError):
        LaunchSpec(
            adapter_key="ultralytics.object_detection.v1",
            adapter_version="1.0.0",
            argv="yolo train epochs=10",
        )


def test_telemetry_envelope_has_versioned_adapter_identity() -> None:
    envelope = TelemetryEnvelope(
        adapter_key="llamafactory.llm_sft.v1",
        adapter_version="1.0.0",
        task_id="task-2",
        sequence=3,
        timestamp=datetime(2026, 7, 31, tzinfo=UTC),
        event_type="metrics",
        payload={"loss": 0.25, "epoch": 2},
    )

    assert envelope.schema_version == "1.0"
    assert envelope.sequence == 3


def test_capability_models_are_immutable_and_cover_adapter_metadata() -> None:
    capabilities = FrameworkCapabilities(
        adapter_key="ultralytics.object_detection.v1",
        adapter_version="1.0.0",
        framework="ultralytics",
        tasks=(
            TaskCapability(
                task_type="object_detection",
                models=(
                    ModelCapability(model_key="yolo26n", display_name="YOLO26 Nano"),
                ),
                resources=ResourceCapability(
                    cpu_cores_min=2,
                    memory_mb_min=4096,
                    gpu_count_min=0,
                    gpu_memory_mb_min=0,
                ),
                parameters=(
                    ParameterCapability(
                        name="epochs",
                        value_type="integer",
                        required=False,
                        default=100,
                        minimum=1,
                    ),
                ),
            ),
        ),
    )

    with pytest.raises(ValidationError, match="frozen_instance"):
        capabilities.framework = "changed"  # type: ignore[misc]

    assert capabilities.tasks[0].parameters[0].minimum == 1
