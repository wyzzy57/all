from __future__ import annotations

import pytest

from visiox_yolo26.labelstudio.llm import (
    build_sft_import_task,
    normalize_sft_export_task,
)
from visiox_yolo26.labelstudio.templates import build_label_config


def test_builds_sft_template_and_prefilled_import_task():
    config = build_label_config("llm", {})
    task = build_sft_import_task(
        dataset_id="dataset-1",
        sample_id="sample-1",
        record={
            "source_row_id": "row-1",
            "messages": [
                {"role": "system", "content": "回答简洁"},
                {"role": "user", "content": "VisiOX 是什么？"},
                {"role": "assistant", "content": "一个模型训练平台。"},
            ],
        },
    )

    assert 'TextArea name="assistant"' in config
    assert 'required="true"' in config
    assert task["data"]["system"] == "回答简洁"
    assert task["data"]["user"] == "VisiOX 是什么？"
    assert task["data"]["assistant"] == "一个模型训练平台。"
    assert task["data"]["visiox_sample_id"] == "sample-1"


def test_normalizes_exported_assistant_to_openai_messages():
    normalized = normalize_sft_export_task(
        {
            "data": {
                "visiox_sample_id": "sample-1",
                "source_row_id": "row-1",
                "system": "回答简洁",
                "user": "问题",
            },
            "annotations": [
                {
                    "result": [
                        {
                            "from_name": "assistant",
                            "type": "textarea",
                            "value": {"text": ["最终答案"]},
                        }
                    ]
                }
            ],
        }
    )

    assert normalized.sample_id == "sample-1"
    assert normalized.source_row_id == "row-1"
    assert normalized.messages[-1] == {"role": "assistant", "content": "最终答案"}


@pytest.mark.parametrize("text", ["", "   ", None])
def test_rejects_missing_or_empty_assistant(text):
    results = [] if text is None else [{"from_name": "assistant", "value": {"text": [text]}}]
    with pytest.raises(ValueError, match="assistant response"):
        normalize_sft_export_task(
            {
                "data": {"visiox_sample_id": "sample-1", "user": "问题"},
                "annotations": [{"result": results}],
            }
        )
