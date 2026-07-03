from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VISIOX_", env_file=".env", extra="ignore")

    environment: str = Field(default="local", validation_alias="VISIOX_ENV")
    postgres_dsn: str = "postgresql+psycopg://visiox:visiox@postgres:5432/visiox"
    redis_url: str = "redis://redis:6379/0"
    minio_endpoint: str = "minio:9000"
    minio_access_key: str = "visiox"
    minio_secret_key: str = "visiox123"
    minio_secure: bool = False
    max_dataset_upload_bytes: int = 100 * 1024 * 1024
    max_dataset_image_bytes: int = 25 * 1024 * 1024
    max_dataset_zip_entries: int = 10_000
    max_dataset_zip_uncompressed_bytes: int = 500 * 1024 * 1024
    registry_url: str = "registry:5000"
    label_studio_url: str = "http://label-studio:8080"


@lru_cache
def get_settings() -> Settings:
    return Settings()
