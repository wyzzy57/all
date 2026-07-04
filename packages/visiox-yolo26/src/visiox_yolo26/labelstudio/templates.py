from html import escape
from typing import Any


def build_label_config(task: str, class_schema: dict[str, Any]) -> str:
    names = class_schema.get("names") or []
    labels = "\n".join(f'    <Label value="{escape(str(name))}" />' for name in names)
    choices = "\n".join(f'    <Choice value="{escape(str(name))}" />' for name in names)

    if task == "detect":
        control = f'<RectangleLabels name="label" toName="image">\n{labels}\n  </RectangleLabels>'
    elif task == "segment":
        control = f'<PolygonLabels name="label" toName="image">\n{labels}\n  </PolygonLabels>'
    elif task == "semantic":
        control = f'<BrushLabels name="label" toName="image">\n{labels}\n  </BrushLabels>'
    elif task == "pose":
        control = f'<KeyPointLabels name="label" toName="image">\n{labels}\n  </KeyPointLabels>'
    elif task == "obb":
        control = f'<PolygonLabels name="label" toName="image">\n{labels}\n  </PolygonLabels>'
    elif task == "classify":
        control = f'<Choices name="label" toName="image" choice="single">\n{choices}\n  </Choices>'
    else:
        raise ValueError(f"Unsupported Label Studio task: {task}")

    return "\n".join(
        [
            "<View>",
            '  <Image name="image" value="$image" />',
            f"  {control}",
            "</View>",
        ]
    )
