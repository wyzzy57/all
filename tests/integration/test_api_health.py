from fastapi.testclient import TestClient

from visiox_common.settings import get_settings
from visiox_api.main import create_app


def test_health_returns_service_status_without_dependency_secrets(monkeypatch, tmp_path):
    get_settings.cache_clear()
    try:
        management_proxy_token_file = tmp_path / "management-proxy-auth-token"
        management_proxy_token_file.write_text(f"{'a' * 64}\n", encoding="ascii")
        monkeypatch.setenv("VISIOX_ENV", "test")
        monkeypatch.setenv("VISIOX_POSTGRES_DSN", "postgresql+psycopg://visiox:visiox@postgres:5432/visiox")
        monkeypatch.setenv("VISIOX_REDIS_URL", "redis://redis:6379/0")
        monkeypatch.setenv("VISIOX_MINIO_ENDPOINT", "minio:9000")
        monkeypatch.setenv("VISIOX_REGISTRY_URL", "registry:5000")
        monkeypatch.setenv("VISIOX_LABEL_STUDIO_URL", "http://label-studio:8080")
        monkeypatch.setenv(
            "VISIOX_MANAGEMENT_PROXY_AUTH_TOKEN_FILE", str(management_proxy_token_file)
        )

        client = TestClient(create_app())

        response = client.get("/health")

        assert response.status_code == 200
        assert response.json() == {
            "service": "api-service",
            "status": "ok",
            "environment": "test",
            "dependencies": {
                "postgres": "configured",
                "redis": "configured",
                "minio": "configured",
                "registry": "configured",
                "label_studio": "configured",
            },
        }
        serialized = response.text.lower()
        assert "visiox:visiox" not in serialized
        assert "postgresql+psycopg" not in serialized
    finally:
        get_settings.cache_clear()
