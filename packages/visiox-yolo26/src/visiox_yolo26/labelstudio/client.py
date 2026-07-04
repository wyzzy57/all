from typing import Any
from urllib.parse import quote

import httpx


class LabelStudioError(RuntimeError):
    pass


class LabelStudioClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        headers = {"Authorization": f"Token {token}"} if token else {}
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=timeout,
            transport=transport,
        )

    def create_project(self, title: str, label_config: str) -> dict[str, Any]:
        return self._request("POST", "/api/projects", json={"title": title, "label_config": label_config})

    def get_project(self, project_id: str | int) -> dict[str, Any]:
        return self._request("GET", f"/api/projects/{_safe_project_id(project_id)}")

    def import_tasks(self, project_id: str | int, tasks: list[dict[str, Any]]) -> dict[str, Any]:
        return self._request("POST", f"/api/projects/{_safe_project_id(project_id)}/import", json=tasks)

    def export_annotations(self, project_id: str | int) -> list[dict[str, Any]]:
        result = self._request("GET", f"/api/projects/{_safe_project_id(project_id)}/export")
        if not isinstance(result, list):
            raise LabelStudioError("Label Studio export response must be a list")
        return result

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "LabelStudioClient":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._client.request(method, path, **kwargs)
        if response.status_code < 200 or response.status_code >= 300:
            summary = response.text[:300]
            raise LabelStudioError(f"Label Studio request failed: {response.status_code} {summary}")
        if response.content:
            return response.json()
        return {}


def _safe_project_id(project_id: str | int) -> str:
    return quote(str(project_id), safe="")
