from functools import lru_cache
from pathlib import Path
import re
from typing import Self
from urllib.parse import urlsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


_MANAGEMENT_PROXY_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{32,256}\Z")


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
    agent_authentication_timeout_seconds: float = Field(default=15, gt=0, le=60)
    agent_heartbeat_interval_seconds: int = Field(default=15, ge=5, le=300)
    agent_offline_after_seconds: int = Field(default=45, gt=0)
    agent_certificate_renew_before_days: int = 30
    agent_max_ws_message_bytes: int = 1024 * 1024
    management_proxy_auth_token_file: Path | None = None
    edge_credential_master_key_file: Path = Path("/run/secrets/visiox-edge-credential-master-key")
    edge_bootstrap_socket: Path = Path("/var/run/visiox/edge-bootstrap.sock")
    edge_ssh_connect_timeout_seconds: float = Field(default=10, gt=0, le=60)
    edge_ssh_auth_timeout_seconds: float = Field(default=10, gt=0, le=60)
    edge_ssh_banner_timeout_seconds: float = Field(default=10, gt=0, le=60)
    edge_ssh_max_output_bytes: int = Field(default=4 * 1024 * 1024, ge=1, le=4 * 1024 * 1024)
    edge_executor_stream: str = "stream:edge_executor.commands"

    @property
    def is_local_environment(self) -> bool:
        return self.environment == "local"

    def read_management_proxy_auth_token(self) -> str:
        if self.is_local_environment:
            return ""
        if self.management_proxy_auth_token_file is None:
            raise ValueError("management proxy authentication token is required for non-local environments")
        try:
            token = self.management_proxy_auth_token_file.read_text(encoding="ascii").removesuffix("\n").removesuffix("\r")
        except (OSError, UnicodeError) as error:
            raise ValueError(
                "management proxy authentication token is required for non-local environments"
            ) from error
        if not _MANAGEMENT_PROXY_TOKEN_PATTERN.fullmatch(token):
            raise ValueError("management proxy authentication token is invalid")
        return token

    def read_edge_credential_master_key(self) -> bytes:
        try:
            master_key = self.edge_credential_master_key_file.read_bytes()
        except OSError as error:
            raise ValueError("edge credential master key is unavailable") from error
        if len(master_key) != 32:
            raise ValueError("edge credential master key must contain exactly 32 raw bytes")
        return master_key

    @model_validator(mode="after")
    def validate_agent_public_ws_url(self) -> Self:
        if not self.is_local_environment and "agent_public_ws_url" not in self.model_fields_set:
            self.agent_public_ws_url = self.agent_public_ws_url.replace("ws://", "wss://", 1)

        parsed = urlsplit(self.agent_public_ws_url)
        required_schemes = {"ws", "wss"} if self.is_local_environment else {"wss"}
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

    @model_validator(mode="after")
    def validate_agent_liveness_thresholds(self) -> Self:
        minimum_offline_threshold = self.agent_heartbeat_interval_seconds * 3
        if self.agent_offline_after_seconds < minimum_offline_threshold:
            raise ValueError(
                "agent_offline_after_seconds must cover at least three heartbeat intervals"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
