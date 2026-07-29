from __future__ import annotations

from visiox_api.services.observability.ultralytics import UltralyticsObservabilityAdapter


class LlamaFactoryObservabilityAdapter(UltralyticsObservabilityAdapter):
    engine = "llamafactory"
