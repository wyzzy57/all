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


_LLAMAFACTORY_BASE = (
    "hiyouga/llamafactory@sha256:"
    "d1ce6223bf300f3c6bef1bcf02809d3ed4472c1d232a771affd4471d872d6a6b"
)


class LlamaFactoryAdapter(FrameworkAdapter):
    def __init__(self, training_image_digest: str) -> None:
        training_digest, training_reason = runtime_image_readiness(
            training_image_digest, "VISIOX_LLM_TRAINING_IMAGE_DIGEST"
        )
        operations = operation_capabilities(
            supported={"train", "stop", "resume", "evaluate", "export"},
            implemented=set(),
            training_unavailable_reason=training_reason,
            inference_unavailable_reason=None,
        )
        available, unavailable_reason = baseline_operation_availability(operations)
        self._capabilities = FrameworkCapabilities(
            adapter_key="llamafactory.llm_sft.v1",
            adapter_version="1.0.0",
            framework="llamafactory",
            display_name="LLaMA-Factory",
            base_image_reference=_LLAMAFACTORY_BASE,
            training_runtime_image_digest=training_digest,
            availability_baseline_operation="train",
            available=available,
            unavailable_reason=unavailable_reason,
            tasks=(
                TaskCapability(
                    task_type="llm_sft",
                    models=tuple(
                        ModelCapability(
                            model_key=runtime_id.lower().replace("/", "-"),
                            display_name=display_name,
                            runtime_id=runtime_id,
                            family="Qwen3",
                            variant=variant,
                            sources=("huggingface", "modelscope"),
                        )
                        for display_name, runtime_id, variant in (
                            ("Qwen3 0.6B", "Qwen/Qwen3-0.6B", "0.6B"),
                            ("Qwen3 1.7B", "Qwen/Qwen3-1.7B", "1.7B"),
                            ("Qwen3 4B", "Qwen/Qwen3-4B", "4B"),
                        )
                    ),
                    accepted_dataset_formats=("alpaca", "sharegpt"),
                    convertible_dataset_formats=("openai_messages",),
                    resources=ResourceCapability(
                        resource_kinds=("cuda",),
                        cpu_cores_min=4,
                        memory_mb_min=16384,
                        gpu_count_min=1,
                        gpu_memory_mb_min=8192,
                    ),
                    parameters=(
                        ParameterCapability(
                            name="learning_rate",
                            value_type="number",
                            default=0.0001,
                            minimum=0.0000001,
                            maximum=1.0,
                            help_text="Initial optimizer learning rate",
                            advanced_group="optimizer",
                        ),
                        ParameterCapability(
                            name="num_train_epochs",
                            value_type="number",
                            default=3,
                            minimum=1,
                            maximum=100,
                            help_text="Number of complete training epochs",
                            advanced_group="training",
                        ),
                        ParameterCapability(
                            name="cutoff_len",
                            value_type="integer",
                            default=1024,
                            minimum=128,
                            maximum=32768,
                            help_text="Maximum token sequence length",
                            advanced_group="data",
                        ),
                        ParameterCapability(
                            name="lora_rank",
                            value_type="integer",
                            default=8,
                            minimum=1,
                            maximum=256,
                            help_text="Low-rank adapter dimension",
                            advanced_group="adapter",
                        ),
                    ),
                    operations=operations,
                    observable_metrics=(
                        "loss",
                        "eval_loss",
                        "learning_rate",
                        "epoch",
                        "grad_norm",
                    ),
                    observable_artifacts=(
                        "adapter_model.safetensors",
                        "adapter_config.json",
                        "trainer_state.json",
                        "training_args.yaml",
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
