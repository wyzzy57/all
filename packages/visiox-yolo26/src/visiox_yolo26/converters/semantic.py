from typing import Any

from PIL import Image, ImageDraw

from visiox_db.models import Dataset, DatasetSample
from visiox_yolo26.converters.internal_schema import (
    ClassMap,
    ConversionWarning,
    ConversionError,
    polygon_points,
    require_dimensions,
    result_error,
)


def convert_sample(
    dataset: Dataset,
    sample: DatasetSample,
    results: list[dict[str, Any]],
    class_map: ClassMap,
) -> tuple[Image.Image, list[ConversionWarning]]:
    width, height = require_dimensions(dataset, sample)
    mask = Image.new("L", (width, height), 0)
    warnings: list[ConversionWarning] = []

    for result in results:
        if result.get("shape") not in {"polygon", "brush"}:
            raise result_error(f"unsupported semantic shape: {result.get('shape')}", dataset, sample, result)
        class_id = class_map.resolve(result, dataset.id, sample.id)
        candidate, candidate_mask = _candidate_mask(dataset, sample, result, class_id, width, height)
        if mask.getbbox() is not None:
            overlap = Image.new("L", (width, height), 0)
            overlap.paste(candidate_mask)
            overlap_pixels = overlap.load()
            mask_pixels = mask.load()
            has_overlap = any(
                overlap_pixels[x, y] != 0 and mask_pixels[x, y] != 0
                for y in range(height)
                for x in range(width)
            )
            if has_overlap:
                warnings.append(
                    ConversionWarning(
                        message="semantic mask overlap; later result overwrote earlier pixels",
                        dataset_id=dataset.id,
                        sample_id=sample.id,
                        source_result_id=str(result.get("source_result_id")) if result.get("source_result_id") else None,
                    )
                )
        mask.paste(candidate, mask=candidate_mask)
    return mask, warnings


def _candidate_mask(
    dataset: Dataset,
    sample: DatasetSample,
    result: dict[str, Any],
    class_id: int,
    width: int,
    height: int,
) -> tuple[Image.Image, Image.Image]:
    candidate = Image.new("L", (width, height), 0)
    candidate_mask = Image.new("L", (width, height), 0)
    if result.get("shape") == "brush" and result.get("mask") is not None:
        _draw_decoded_mask(candidate, candidate_mask, result, class_id, width, height)
        return candidate, candidate_mask

    points = [(round(x / 100 * width), round(y / 100 * height)) for x, y in polygon_points(result, dataset, sample)]
    ImageDraw.Draw(candidate).polygon(points, fill=class_id + 1)
    ImageDraw.Draw(candidate_mask).polygon(points, fill=255)
    return candidate, candidate_mask


def _draw_decoded_mask(
    candidate: Image.Image,
    candidate_mask: Image.Image,
    result: dict[str, Any],
    class_id: int,
    width: int,
    height: int,
) -> None:
    mask_payload = result.get("mask")
    if not isinstance(mask_payload, list) or len(mask_payload) != height:
        raise ConversionError(f"decoded brush mask height must be {height}")
    candidate_pixels = candidate.load()
    candidate_mask_pixels = candidate_mask.load()
    for y, row in enumerate(mask_payload):
        if not isinstance(row, list) or len(row) != width:
            raise ConversionError(f"decoded brush mask width must be {width}")
        for x, value in enumerate(row):
            if value:
                candidate_pixels[x, y] = class_id + 1
                candidate_mask_pixels[x, y] = 255
