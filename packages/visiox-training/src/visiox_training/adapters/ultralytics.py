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


_BASIC_PARAMETER_NAMES = ("epochs", "batch", "imgsz", "lr0")
_MANAGED_PARAMETER_NAMES = (
    "task",
    "mode",
    "model",
    "data",
    "project",
    "name",
    "exist_ok",
    "device",
)
_CONFIG_TEMPLATE = """epochs: 100
batch: 16
imgsz: 640
lr0: 0.01
workers: 8
amp: true
resume: false
warmup_epochs: 3.0
patience: 100
save_period: -1
optimizer: auto
cos_lr: false
close_mosaic: 10
"""


class UltralyticsAdapter(FrameworkAdapter):
    def __init__(
        self,
        training_image_digest: str,
        inference_image_digest: str,
        *,
        implemented_operations: set[OperationName] | None = None,
    ) -> None:
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
                config_format="yaml",
                config_template=_CONFIG_TEMPLATE,
                basic_parameter_names=_BASIC_PARAMETER_NAMES,
                managed_parameter_names=_MANAGED_PARAMETER_NAMES,
            )
            for variant in ("n", "s", "m", "l", "x")
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
                        ParameterCapability(
                            name="lr0",
                            value_type="number",
                            default=0.01,
                            minimum=0.0000001,
                            maximum=1.0,
                            help_text="Initial optimizer learning rate",
                            advanced_group="optimizer",
                        ),
                        ParameterCapability(
                            name="workers",
                            value_type="integer",
                            default=8,
                            minimum=0,
                            maximum=256,
                            help_text="Data loading worker processes",
                            advanced_group="resources",
                        ),
                        ParameterCapability(
                            name="amp",
                            value_type="boolean",
                            default=True,
                            help_text="Enable automatic mixed precision training",
                            advanced_group="resources",
                        ),
                        ParameterCapability(
                            name="resume",
                            value_type="boolean",
                            default=False,
                            help_text="Resume from the managed last checkpoint",
                            advanced_group="training",
                        ),
                        ParameterCapability(
                            name="warmup_epochs",
                            value_type="number",
                            default=3.0,
                            minimum=0.0,
                            maximum=10000.0,
                            help_text="Number of learning-rate warmup epochs",
                            advanced_group="optimizer",
                        ),
                        ParameterCapability(
                            name="patience",
                            value_type="integer",
                            default=100,
                            minimum=0,
                            maximum=10000,
                            help_text="Epochs without improvement before early stopping",
                            advanced_group="training",
                        ),
                        ParameterCapability(
                            name="save_period",
                            value_type="integer",
                            default=-1,
                            minimum=-1,
                            maximum=10000,
                            help_text="Checkpoint interval in epochs; -1 disables periodic saves",
                            advanced_group="checkpointing",
                        ),
                        ParameterCapability(
                            name="optimizer",
                            value_type="string",
                            default="auto",
                            choices=(
                                "auto",
                                "SGD",
                                "MuSGD",
                                "Adam",
                                "Adamax",
                                "AdamW",
                                "NAdam",
                                "RAdam",
                                "RMSProp",
                            ),
                            help_text="Optimizer used for training",
                            advanced_group="optimizer",
                        ),
                        ParameterCapability(
                            name="cos_lr",
                            value_type="boolean",
                            default=False,
                            help_text="Use a cosine learning-rate schedule",
                            advanced_group="optimizer",
                        ),
                        ParameterCapability(
                            name="close_mosaic",
                            value_type="integer",
                            default=10,
                            minimum=0,
                            maximum=10000,
                            help_text="Disable mosaic augmentation for the final epochs",
                            advanced_group="augmentation",
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
