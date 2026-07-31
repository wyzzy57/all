from __future__ import annotations

from fastapi import Depends

from visiox_common.settings import Settings, get_settings
from visiox_training.adapters.base import FrameworkAdapter
from visiox_training.adapters.llamafactory import LlamaFactoryAdapter
from visiox_training.adapters.paddlex import PaddleXAdapter
from visiox_training.adapters.ultralytics import UltralyticsAdapter
from visiox_training.registry import AdapterRegistry, create_registry


class FrameworkAdapterCatalog:
    def __init__(self, settings: Settings) -> None:
        registry = create_registry()
        registry.register(LlamaFactoryAdapter(settings.llm_training_image_digest))
        registry.register(
            PaddleXAdapter(
                settings.paddlex_training_image_digest,
                settings.paddlex_inference_image_digest,
            )
        )
        registry.register(
            UltralyticsAdapter(
                settings.ultralytics_training_image_digest,
                settings.deployment_image_digest,
            )
        )
        self._registry = registry

    @property
    def registry(self) -> AdapterRegistry:
        return self._registry

    def list(self, task_kind: str | None = None) -> tuple[FrameworkAdapter, ...]:
        adapters = self._registry.list()
        if task_kind is None:
            return adapters
        return tuple(
            adapter
            for adapter in adapters
            if adapter.capabilities.supports_task(task_kind)
        )


def get_framework_adapter_catalog(
    settings: Settings = Depends(get_settings),
) -> FrameworkAdapterCatalog:
    return FrameworkAdapterCatalog(settings)
