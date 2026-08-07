from __future__ import annotations

from pathlib import Path
from typing import Any

from visiox_training.adapters.base import FrameworkAdapter
from visiox_training.capabilities import (
    FrameworkCapabilities,
    ModelCapability,
    OperationName,
    ParameterCapability,
    ResourceCapability,
    RuntimeComponentCapability,
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


class PaddleXAdapter(FrameworkAdapter):
    def __init__(
        self,
        training_image_digest: str,
        inference_image_digest: str,
        *,
        implemented_operations: set[OperationName] | None = None,
    ) -> None:
        training_digest, training_reason = runtime_image_readiness(
            training_image_digest, "VISIOX_PADDLEX_TRAINING_IMAGE_DIGEST"
        )
        inference_digest, inference_reason = runtime_image_readiness(
            inference_image_digest, "VISIOX_PADDLEX_INFERENCE_IMAGE_DIGEST"
        )
        supported_operations = {
            "train",
            "stop",
            "resume",
            "evaluate",
            "image_inference",
            "export",
            "deploy",
        }
        operations = operation_capabilities(
            supported=supported_operations,
            implemented=set(implemented_operations or ()),
            training_unavailable_reason=training_reason,
            inference_unavailable_reason=inference_reason,
        )
        available, unavailable_reason = baseline_operation_availability(operations)
        self._capabilities = FrameworkCapabilities(
            adapter_key="paddlex.object_detection.v1",
            adapter_version="1.0.0",
            framework="paddlex",
            display_name="PaddleX",
            framework_version="3.0.3",
            base_image_reference=(
                "nvidia/cuda:11.8.0-base-ubuntu22.04@sha256:"
                "79e5b2cf878ee9006f5b3738caeea34fdc7708a32db53fe3e80db0b48bd286a0"
            ),
            runtime_components=(
                RuntimeComponentCapability(key="PaddlePaddle", value="3.0.0"),
                RuntimeComponentCapability(key="Python", value="3.10"),
                RuntimeComponentCapability(key="CUDA", value="11.8.0"),
                RuntimeComponentCapability(key="cuDNN", value="8.9.6"),
            ),
            training_runtime_image_digest=training_digest,
            inference_runtime_image_digest=inference_digest,
            availability_baseline_operation="train",
            available=available,
            unavailable_reason=unavailable_reason,
            tasks=(
                TaskCapability(
                    task_type="object_detection",
                    models=(
                        ModelCapability(
                            model_key="pp-yoloe-s",
                            display_name="PP-YOLOE-S",
                            runtime_id="PP-YOLOE_plus-S",
                            family="PP-YOLOE",
                            variant="S",
                            source="paddlex",
                            revision="paddlex-model-zoo/3.0.3/PP-YOLOE_plus-S",
                        ),
                        ModelCapability(
                            model_key="rt-detr-l",
                            display_name="RT-DETR-L",
                            runtime_id="RT-DETR-L",
                            family="RT-DETR",
                            variant="L",
                            source="paddlex",
                            revision="paddlex-model-zoo/3.0.3/RT-DETR-L",
                        ),
                    ),
                    accepted_dataset_formats=("coco",),
                    convertible_dataset_formats=("label_studio", "yolo"),
                    resources=ResourceCapability(
                        resource_kinds=("cpu", "cuda"),
                        cpu_cores_min=4,
                        memory_mb_min=8192,
                        gpu_count_min=1,
                        gpu_memory_mb_min=8192,
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
                            name="batch_size",
                            value_type="integer",
                            default=8,
                            minimum=1,
                            maximum=256,
                            help_text="Images per training batch",
                            advanced_group="resources",
                        ),
                        ParameterCapability(
                            name="learning_rate",
                            value_type="number",
                            default=0.001,
                            minimum=0.0000001,
                            maximum=1.0,
                            help_text="Initial optimizer learning rate",
                            advanced_group="optimizer",
                        ),
                    ),
                    operations=operations,
                    observable_metrics=(
                        "loss",
                        "bbox_mAP",
                        "precision",
                        "recall",
                    ),
                    observable_artifacts=(
                        "best_model.pdparams",
                        "model.pdmodel",
                        "metrics.json",
                    ),
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
