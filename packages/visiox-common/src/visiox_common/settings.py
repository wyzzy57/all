from functools import lru_cache
from pathlib import Path
from typing import Self
from urllib.parse import urlsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VISIOX_", env_file=".env", extra="ignore")

    environment: str = Field(default="local", validation_alias="VISIOX_ENV")
    postgres_dsn: str = "postgresql+psycopg://visiox:visiox@postgres:5432/visiox"
    redis_url: str = "redis://redis:6379/0"
    minio_endpoint: str = "minio:9000"
    minio_public_url: str | None = None
    minio_access_key: str = "visiox"
    minio_secret_key: str = "visiox123"
    minio_secure: bool = False
    max_dataset_upload_bytes: int = 100 * 1024 * 1024
    max_dataset_image_bytes: int = 25 * 1024 * 1024
    max_dataset_zip_entries: int = 10_000
    max_dataset_zip_uncompressed_bytes: int = 500 * 1024 * 1024
    registry_url: str = "registry:5000"
    label_studio_url: str = "http://label-studio:8080"
    label_studio_public_url: str | None = None
    label_studio_token: str = ""
    label_studio_sync_stream: str = "stream:label_sync.commands"
    seed_base_models_on_startup: bool = False
    mlflow_tracking_uri: str = "http://mlflow:5000"
    mlflow_public_url: str = "http://127.0.0.1:5001"
    tensorboard_public_url: str = "http://127.0.0.1:6006"
    tensorboard_histogram_interval: int = 5
    training_runs_root: Path = Path("/workspace/training-runs")
    observability_max_points: int = 2000
    observability_event_cache_size: int = 32
    observability_live_poll_seconds: int = 5
    agent_ca_cert_path: Path = Path("/var/lib/visiox/pki/ca.crt")
    agent_ca_key_path: Path = Path("/var/lib/visiox/pki/ca.key")
    agent_auto_generate_ca: bool = False
    agent_certificate_ttl_days: int = 365
    agent_enrollment_token_ttl_minutes: int = 15
    agent_public_ws_url: str = "ws://127.0.0.1:8000/agent/v1/connect"
    agent_gateway_enabled: bool = False
    agent_heartbeat_interval_seconds: int = 15
    agent_offline_after_seconds: int = 45
    agent_certificate_renew_before_days: int = 30
    agent_max_ws_message_bytes: int = 1024 * 1024

    @model_validator(mode="after")
    def validate_agent_public_ws_url(self) -> Self:
        if self.environment.lower() != "local" and "agent_public_ws_url" not in self.model_fields_set:
            self.agent_public_ws_url = self.agent_public_ws_url.replace("ws://", "wss://", 1)

        parsed = urlsplit(self.agent_public_ws_url)
        required_schemes = {"ws", "wss"} if self.environment.lower() == "local" else {"wss"}
        if (
            parsed.scheme.lower() not in required_schemes
            or not parsed.hostname
            or parsed.path != "/agent/v1/connect"
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "agent_public_ws_url must use the environment-appropriate WebSocket scheme "
                "and path /agent/v1/connect"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
