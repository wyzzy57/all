from fastapi.testclient import TestClient

from visiox_common.settings import get_settings
from visiox_api.main import create_app


def test_health_returns_service_status_and_dependency_configuration(monkeypatch):
    get_settings.cache_clear()
    try:
        monkeypatch.setenv("VISIOX_ENV", "test")
        monkeypatch.setenv("VISIOX_POSTGRES_DSN", "postgresql+psycopg://visiox:visiox@postgres:5432/visiox")
        monkeypatch.setenv("VISIOX_REDIS_URL", "redis://redis:6379/0")
        monkeypatch.setenv("VISIOX_MINIO_ENDPOINT", "minio:9000")
        monkeypatch.setenv("VISIOX_REGISTRY_URL", "registry:5000")
        monkeypatch.setenv("VISIOX_LABEL_STUDIO_URL", "http://label-studio:8080")

        client = TestClient(create_app())

        response = client.get("/health")

        assert response.status_code == 200
        assert response.json() == {
            "service": "api-service",
            "status": "ok",
            "environment": "test",
            "dependencies": {
                "postgres": "postgresql+psycopg://visiox:visiox@postgres:5432/visiox",
                "redis": "redis://redis:6379/0",
                "minio": "minio:9000",
                "registry": "registry:5000",
                "label_studio": "http://label-studio:8080",
            },
        }
    finally:
        get_settings.cache_clear()
