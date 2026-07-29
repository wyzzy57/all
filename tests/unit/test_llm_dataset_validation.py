from __future__ import annotations

import json

import pytest

from visiox_api.services.llm_datasets import (
    LlmDatasetError,
    preview_records,
    validate_llm_dataset_file,
)


def test_validates_alpaca_json_and_builds_stable_manifest(tmp_path):
    path = tmp_path / "train.json"
    path.write_text(
        json.dumps(
            [
                {"instruction": "介绍 VisiOX", "input": "一句话", "output": "这是一个训练平台。"},
                {"instruction": "空响应", "output": ""},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = validate_llm_dataset_file(path)

    assert result.format == "alpaca"
    assert len(result.records) == 1
    assert len(result.issues) == 1
    assert len(result.manifest_checksum) == 64
    assert result.schema_config["response"] == "output"
    assert result.records[0]["messages"][-1]["role"] == "assistant"
    assert result.records[0]["source_row_id"] == "row-1"


def test_validates_openai_messages_and_previews_dialog(tmp_path):
    path = tmp_path / "train.jsonl"
    path.write_text(
        json.dumps(
            {
                "messages": [
                    {"role": "system", "content": "回答要简洁"},
                    {"role": "user", "content": "你好"},
                    {"role": "assistant", "content": "你好！"},
                ]
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    result = validate_llm_dataset_file(path)
    preview = preview_records(result.records, result.format, limit=5)

    assert result.format == "openai_messages"
    assert preview[0]["messages"][1] == {"role": "user", "content": "你好"}
    assert preview[0]["token_estimate"] > 0


def test_validates_sharegpt_roles(tmp_path):
    path = tmp_path / "train.json"
    path.write_text(
        json.dumps(
            [
                {
                    "conversations": [
                        {"from": "human", "value": "2+2?"},
                        {"from": "gpt", "value": "4"},
                    ]
                }
            ]
        ),
        encoding="utf-8",
    )

    result = validate_llm_dataset_file(path)

    assert result.format == "sharegpt"
    assert result.records[0]["messages"][1] == {"role": "assistant", "content": "4"}


def test_rejects_unknown_dataset_shape(tmp_path):
    path = tmp_path / "train.json"
    path.write_text('[{"prompt":"hello","answer":"world"}]', encoding="utf-8")

    with pytest.raises(LlmDatasetError, match="unable to detect"):
        validate_llm_dataset_file(path)


def test_normalizes_csv_to_messages(tmp_path):
    path = tmp_path / "train.csv"
    path.write_text(
        "instruction,input,output,system\n"
        '总结,VisiOX 是训练平台,VisiOX 提供模型全流程能力,回答简洁\n',
        encoding="utf-8",
    )

    result = validate_llm_dataset_file(path)

    assert result.format == "alpaca"
    assert result.records == [
        {
            "source_row_id": "row-1",
            "messages": [
                {"role": "system", "content": "回答简洁"},
                {"role": "user", "content": "总结\n\nVisiOX 是训练平台"},
                {"role": "assistant", "content": "VisiOX 提供模型全流程能力"},
            ],
        }
    ]


def test_reports_duplicate_and_invalid_role_order_with_specific_codes(tmp_path):
    valid = {
        "messages": [
            {"role": "user", "content": "问题"},
            {"role": "assistant", "content": "答案"},
        ]
    }
    invalid_order = {
        "messages": [
            {"role": "assistant", "content": "抢答"},
            {"role": "user", "content": "问题"},
        ]
    }
    path = tmp_path / "train.json"
    path.write_text(json.dumps([valid, valid, invalid_order], ensure_ascii=False), encoding="utf-8")

    result = validate_llm_dataset_file(path)

    assert len(result.records) == 1
    assert [issue.code for issue in result.issues] == ["DUPLICATE_RECORD", "INVALID_ROLE_ORDER"]


def test_rejects_invalid_utf8(tmp_path):
    path = tmp_path / "train.jsonl"
    path.write_bytes(b'{"messages":[]}' + bytes([0xFF]))

    with pytest.raises(LlmDatasetError, match="UTF-8"):
        validate_llm_dataset_file(path)
