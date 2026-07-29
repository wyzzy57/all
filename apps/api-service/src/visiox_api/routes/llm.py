from __future__ import annotations

from typing import Any, Literal
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from visiox_api.services.llm_training import LlmTrainingConfigError, validate_llamafactory_config


router = APIRouter(prefix="/llm", tags=["llm-training"])


class ModelResolveRequest(BaseModel):
    source: Literal["huggingface", "modelscope"]
    model_id: str = Field(min_length=3, max_length=240)
    revision: str = Field(default="main", min_length=1, max_length=160)


class ModelResolveResponse(BaseModel):
    source: Literal["huggingface", "modelscope"]
    model_id: str
    requested_revision: str
    resolved_revision: str
    immutable_revision: bool
    pipeline_tag: str | None = None
    library_name: str | None = None
    license: str | None = None
    gated: bool = False
    private: bool = False
    size_bytes: int | None = None


@router.post("/models/resolve", response_model=ModelResolveResponse)
async def resolve_model(request: ModelResolveRequest) -> ModelResolveResponse:
    try:
        validated = validate_llamafactory_config(
            {
                "model_source": request.source,
                "model_id": request.model_id,
                "model_revision": request.revision,
            }
        )
    except LlmTrainingConfigError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    model_id = str(validated["model_id"])
    revision = str(validated["model_revision"])
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            if request.source == "huggingface":
                return await _resolve_huggingface(client, model_id, revision)
            return await _resolve_modelscope(client, model_id, revision)
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="Model registry request timed out") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Model registry is unavailable") from exc


async def _resolve_huggingface(
    client: httpx.AsyncClient,
    model_id: str,
    revision: str,
) -> ModelResolveResponse:
    encoded_id = "/".join(quote(part, safe="") for part in model_id.split("/"))
    encoded_revision = quote(revision, safe="")
    response = await client.get(
        f"https://huggingface.co/api/models/{encoded_id}/revision/{encoded_revision}"
    )
    if response.status_code == 404:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Hugging Face model or revision not found")
    response.raise_for_status()
    payload = response.json()
    siblings = payload.get("siblings") if isinstance(payload.get("siblings"), list) else []
    sizes = [item.get("size") for item in siblings if isinstance(item, dict)]
    size_bytes = sum(value for value in sizes if isinstance(value, int)) or None
    card_data = payload.get("cardData") if isinstance(payload.get("cardData"), dict) else {}
    resolved = str(payload.get("sha") or "")
    if not resolved:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Hugging Face did not return a commit SHA")
    return ModelResolveResponse(
        source="huggingface",
        model_id=str(payload.get("id") or model_id),
        requested_revision=revision,
        resolved_revision=resolved,
        immutable_revision=True,
        pipeline_tag=_optional_text(payload.get("pipeline_tag")),
        library_name=_optional_text(payload.get("library_name")),
        license=_optional_text(card_data.get("license")),
        gated=bool(payload.get("gated")),
        private=bool(payload.get("private")),
        size_bytes=size_bytes,
    )


async def _resolve_modelscope(
    client: httpx.AsyncClient,
    model_id: str,
    revision: str,
) -> ModelResolveResponse:
    owner, repo_name = model_id.split("/", 1)
    response = await client.get(
        f"https://modelscope.cn/openapi/v1/models/{quote(owner, safe='')}/{quote(repo_name, safe='')}"
    )
    if response.status_code == 404:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ModelScope model not found")
    response.raise_for_status()
    envelope = response.json()
    payload: dict[str, Any] = envelope.get("data") if isinstance(envelope, dict) else {}
    if not isinstance(payload, dict) or not payload.get("id"):
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="ModelScope returned an invalid response")
    tasks = payload.get("tasks") if isinstance(payload.get("tasks"), list) else []
    return ModelResolveResponse(
        source="modelscope",
        model_id=str(payload.get("id") or model_id),
        requested_revision=revision,
        resolved_revision=revision,
        immutable_revision=len(revision) == 40 and all(char in "0123456789abcdef" for char in revision.lower()),
        pipeline_tag=_optional_text(tasks[0]) if tasks else None,
        license=_optional_text(payload.get("license")),
        gated=bool(payload.get("gated")),
        private=bool(payload.get("private")),
        size_bytes=payload.get("file_size") if isinstance(payload.get("file_size"), int) else None,
    )


def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
