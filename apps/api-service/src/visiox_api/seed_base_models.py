from __future__ import annotations

import json
import tempfile
from hashlib import sha256
from pathlib import Path
from typing import Protocol

from sqlalchemy.orm import Session

from visiox_common.settings import get_settings
from visiox_db.models import BaseModel
from visiox_db.session import create_session_factory
from visiox_storage.client import MinioObjectStorageClient


class SeedStorage(Protocol):
    def put_file(
        self,
        bucket: str,
        object_name: str,
        path: Path,
        content_type: str | None = None,
    ) -> str: ...


DEFAULT_SEED_PATH = Path("infra/seed/yolo26_base_models.json")


def seed_yolo26_base_models(
    session: Session,
    *,
    storage: SeedStorage | None = None,
    seed_path: Path = DEFAULT_SEED_PATH,
) -> int:
    records = json.loads(seed_path.read_text(encoding="utf-8"))
    prepared_count = 0

    with tempfile.TemporaryDirectory(prefix="visiox-base-model-seed-") as tmp_dir:
        tmp_path = Path(tmp_dir)
        for record in records:
            model_id = str(record["id"])
            filename = str(record["filename"])
            object_name = f"base/{model_id}/{filename}"
            payload = _placeholder_payload(record)
            checksum = sha256(payload).hexdigest()
            local_uri = str(record.get("local_uri") or f"minio://models/{object_name}")

            if storage is not None:
                model_path = tmp_path / filename
                model_path.write_bytes(payload)
                local_uri = storage.put_file(
                    "models",
                    object_name,
                    model_path,
                    content_type="application/octet-stream",
                )

            model = session.get(BaseModel, model_id)
            if model is None:
                model = BaseModel(id=model_id)

            model.family = str(record["family"])
            model.task = str(record["task"])
            model.scale = str(record["scale"])
            model.filename = filename
            model.source_path = str(record["source_path"])
            model.local_uri = local_uri
            model.checksum = checksum
            model.size_bytes = len(payload)
            model.status = "ready"
            session.add(model)
            prepared_count += 1

    session.commit()
    return prepared_count


def _placeholder_payload(record: dict[str, object]) -> bytes:
    model_id = str(record["id"])
    return f"visiox prepared yolo26 base model placeholder: {model_id}\n".encode()


def seed_yolo26_base_models_from_settings() -> int:
    settings = get_settings()
    storage = MinioObjectStorageClient(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )
    session_factory = create_session_factory()
    with session_factory() as session:
        return seed_yolo26_base_models(session, storage=storage)


if __name__ == "__main__":
    count = seed_yolo26_base_models_from_settings()
    print(f"Prepared {count} YOLO26 base models")
