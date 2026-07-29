from __future__ import annotations

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

import visiox_api.routes.llm as llm_routes
from visiox_api.services.llm_training import LlmTrainingConfigError, validate_llamafactory_config


class FakeRegistryClient:
    def __init__(self, response: httpx.Response):
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, _url: str):
        return self.response


def _test_client(monkeypatch, payload: dict) -> TestClient:
    response = httpx.Response(
        200,
        json=payload,
        request=httpx.Request("GET", "https://registry.example/model"),
    )
    monkeypatch.setattr(
        llm_routes.httpx,
        "AsyncClient",
        lambda **_kwargs: FakeRegistryClient(response),
    )
    app = FastAPI()
    app.include_router(llm_routes.router)
    return TestClient(app)


def test_resolve_huggingface_model_returns_commit_metadata(monkeypatch):
    client = _test_client(
        monkeypatch,
        {
            "id": "Qwen/Qwen3-0.6B",
            "sha": "c1899de289a04d12100db370d81485cdf75e47ca",
            "pipeline_tag": "text-generation",
            "library_name": "transformers",
            "gated": False,
            "private": False,
            "cardData": {"license": "apache-2.0"},
            "siblings": [{"rfilename": "model.safetensors", "size": 1024}],
        },
    )

    response = client.post(
        "/llm/models/resolve",
        json={"source": "huggingface", "model_id": "Qwen/Qwen3-0.6B", "revision": "main"},
    )

    assert response.status_code == 200
    assert response.json()["immutable_revision"] is True
    assert response.json()["resolved_revision"] == "c1899de289a04d12100db370d81485cdf75e47ca"
    assert response.json()["license"] == "apache-2.0"


def test_resolve_modelscope_model_preserves_requested_revision(monkeypatch):
    client = _test_client(
        monkeypatch,
        {
            "success": True,
            "data": {
                "id": "Qwen/Qwen3-0.6B",
                "tasks": ["text-generation"],
                "license": "apache-2.0",
                "gated": False,
                "private": False,
                "file_size": 2048,
            },
        },
    )

    response = client.post(
        "/llm/models/resolve",
        json={"source": "modelscope", "model_id": "Qwen/Qwen3-0.6B", "revision": "master"},
    )

    assert response.status_code == 200
    assert response.json()["resolved_revision"] == "master"
    assert response.json()["immutable_revision"] is False


def test_llamafactory_config_rejects_invalid_sft_boundaries():
    try:
        validate_llamafactory_config({"stage": "dpo", "model_id": "Qwen/Qwen3-0.6B"})
    except LlmTrainingConfigError as exc:
        assert "supports SFT only" in str(exc)
    else:
        raise AssertionError("DPO configuration should be rejected in the first release")
