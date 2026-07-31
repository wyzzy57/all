from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict

from visiox_api.dependencies.auth import get_current_user
from visiox_api.services.framework_adapters import (
    FrameworkAdapterCatalog,
    get_framework_adapter_catalog,
)
from visiox_db.models.identity import User
from visiox_training.capabilities import FrameworkCapabilities


router = APIRouter(prefix="/frameworks", tags=["frameworks"])


class FrameworkCapabilityCatalogResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    task_kind: str | None
    adapters: tuple[FrameworkCapabilities, ...]


@router.get("/capabilities", response_model=FrameworkCapabilityCatalogResponse)
def get_framework_capabilities(
    task_kind: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    _actor: User = Depends(get_current_user),
    catalog: FrameworkAdapterCatalog = Depends(get_framework_adapter_catalog),
) -> FrameworkCapabilityCatalogResponse:
    return FrameworkCapabilityCatalogResponse(
        task_kind=task_kind,
        adapters=tuple(
            adapter.capabilities for adapter in catalog.list(task_kind=task_kind)
        ),
    )
