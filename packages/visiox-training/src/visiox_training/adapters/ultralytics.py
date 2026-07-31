from __future__ import annotations

from pathlib import Path
from typing import Any

from visiox_training.adapters.base import FrameworkAdapter
from visiox_training.capabilities import (
    FrameworkCapabilities,
    ModelCapability,
    ParameterCapability,
    ResourceCapability,
    TaskCapability,
    baseline_operation_availability,
    operation_capabilities,
    runtime_image_readiness,
)
from visiox_training.contracts import (
    ArtifactManifest,
    DatasetManifest,
    LaunchSpec,
    TelemetryEnvelope,
)
from visiox_training.errors import UnsupportedAdapterOperationError


class UltralyticsAdapter(FrameworkAdapter):
    def __init__(self, training_image_digest: str, inference_image_digest: str) -> None:
        training_digest, training_reason = runtime_image_readiness(
            training_image_digest, "VISIOX_ULTRALYTICS_TRAINING_IMAGE_DIGEST"
        )
        inference_digest, inference_reason = runtime_image_readiness(
            inference_image_digest, "VISIOX_DEPLOYMENT_IMAGE_DIGEST"
        )
        models = tuple(
            ModelCapability(
                model_key=f"yolo26{variant}",
                display_name=f"YOLO26-{variant.upper()}",
                runtime_id=f"yolo26{variant}.pt",
                family="YOLO26",
                variant=variant.upper(),
                source="ultralytics",
            )
            for variant in ("n", "s", "m", "l", "x")
        )
        operations = operation_capabilities(
            supported={
                "train",
                "stop",
                "resume",
                "evaluate",
                "image_inference",
                "export",
                "deploy",
            },
            implemented=set(),
            training_unavailable_reason=training_reason,
            inference_unavailable_reason=inference_reason,
        )
        available, unavailable_reason = baseline_operation_availability(operations)
        self._capabilities = FrameworkCapabilities(
            adapter_key="ultralytics.object_detection.v1",
            adapter_version="1.0.0",
            framework="ultralytics",
            display_name="Ultralytics",
            framework_version_constraint=">=8.3,<9.0",
            training_runtime_image_digest=training_digest,
            inference_runtime_image_digest=inference_digest,
            availability_baseline_operation="train",
            available=available,
            unavailable_reason=unavailable_reason,
            tasks=(
                TaskCapability(
                    task_type="object_detection",
                    models=models,
                    accepted_dataset_formats=("yolo",),
                    convertible_dataset_formats=("label_studio",),
                    resources=ResourceCapability(
                        resource_kinds=("cpu", "cuda"),
                        cpu_cores_min=2,
                        memory_mb_min=4096,
                        gpu_count_min=1,
                        gpu_memory_mb_min=4096,
                    ),
                    parameters=(
                        ParameterCapability(
                            name="epochs",
                            value_type="integer",
                            default=100,
                            minimum=1,
                            maximum=1000,
                            help_text="Number of complete training epochs",
                            advanced_group="training",
                        ),
                        ParameterCapability(
                            name="batch",
                            value_type="integer",
                            default=16,
                            minimum=-1,
                            help_text="Images per training batch; -1 enables auto batch",
                            advanced_group="resources",
                        ),
                        ParameterCapability(
                            name="imgsz",
                            value_type="integer",
                            default=640,
                            minimum=32,
                            maximum=4096,
                            help_text="Square training image size in pixels",
                            advanced_group="data",
                        ),
                    ),
                    operations=operations,
                    observable_metrics=(
                        "train/box_loss",
                        "metrics/precision(B)",
                        "metrics/recall(B)",
                        "metrics/mAP50(B)",
                        "metrics/mAP50-95(B)",
                    ),
                    observable_artifacts=("best.pt", "last.pt", "results.csv"),
                ),
            ),
        )

    @property
    def capabilities(self) -> FrameworkCapabilities:
        return self._capabilities

    def validate_dataset(self, manifest: DatasetManifest) -> None:
        self._unsupported("validate_dataset")

    def build_launch_spec(
        self, manifest: DatasetManifest, parameters: dict[str, Any]
    ) -> LaunchSpec:
        self._unsupported("build_launch_spec")

    def parse_telemetry(self, line: str) -> TelemetryEnvelope | None:
        self._unsupported("parse_telemetry")

    def collect_artifacts(self, output_dir: Path) -> ArtifactManifest:
        self._unsupported("collect_artifacts")

    def _unsupported(self, operation: str) -> None:
        raise UnsupportedAdapterOperationError(
            f"{operation} is cataloged in Task 3 but execution is not wired yet"
        )
