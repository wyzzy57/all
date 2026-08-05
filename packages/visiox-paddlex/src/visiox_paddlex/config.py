from __future__ import annotations

from typing import Literal

from visiox_training.contracts import CanonicalModel


class PaddleXDetectionConfigParameters(CanonicalModel):
    dataset_dir: Literal["/workspace/dataset"] = "/workspace/dataset"


class PaddleXConfigOverride(CanonicalModel):
    key: Literal["Global.dataset_dir"]
    value: Literal["/workspace/dataset"]


def build_paddlex_detection_config_overrides(
    parameters: PaddleXDetectionConfigParameters,
) -> tuple[PaddleXConfigOverride, ...]:
    return (
        PaddleXConfigOverride(
            key="Global.dataset_dir",
            value=parameters.dataset_dir,
        ),
    )
