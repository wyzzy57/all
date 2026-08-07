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


DEFAULT_YOLO26_SEED_PATH = Path("infra/seed/yolo26_base_models.json")
DEFAULT_PADDLEX_SEED_PATH = Path("infra/seed/paddlex_base_models.json")
_MIN_REAL_MODEL_SIZE_BYTES = 1024 * 1024


def seed_yolo26_base_models(
    session: Session,
    *,
    storage: SeedStorage | None = None,
    seed_path: Path = DEFAULT_YOLO26_SEED_PATH,
) -> int:
    records = json.loads(seed_path.read_text(encoding="utf-8"))
    return _seed_base_models(
        session,
        records=records,
        storage=storage,
        prepare_placeholders=True,
    )


def seed_paddlex_base_models(
    session: Session,
    *,
    storage: SeedStorage | None = None,
    seed_path: Path = DEFAULT_PADDLEX_SEED_PATH,
) -> int:
    del storage  # PaddleX weights are downloaded and cached by the edge runtime.
    records = json.loads(seed_path.read_text(encoding="utf-8"))
    return _seed_base_models(
        session,
        records=records,
        storage=None,
        prepare_placeholders=False,
    )


def _seed_base_models(
    session: Session,
    *,
    records: list[dict[str, object]],
    storage: SeedStorage | None,
    prepare_placeholders: bool,
) -> int:
    prepared_count = 0

    with tempfile.TemporaryDirectory(prefix="visiox-base-model-seed-") as tmp_dir:
        tmp_path = Path(tmp_dir)
        for record in records:
            model_id = str(record["id"])
            filename = str(record["filename"])
            object_name = f"base/{model_id}/{filename}"
            model = session.get(BaseModel, model_id)

            if model is not None and _has_real_artifact(model):
                _apply_model_metadata(model, record)
                session.add(model)
                prepared_count += 1
                continue

            if model is None:
                model = BaseModel(id=model_id)

            _apply_model_metadata(model, record)
            if prepare_placeholders:
                payload = _placeholder_payload(record)
                model.checksum = sha256(payload).hexdigest()
                model.size_bytes = len(payload)
                model.local_uri = str(
                    record.get("local_uri") or f"minio://models/{object_name}"
                )
                if storage is not None:
                    model_path = tmp_path / filename
                    model_path.write_bytes(payload)
                    model.local_uri = storage.put_file(
                        "models",
                        object_name,
                        model_path,
                        content_type="application/octet-stream",
                    )
            else:
                model.local_uri = None
                model.checksum = None
                model.size_bytes = None
            session.add(model)
            prepared_count += 1

    session.commit()
    return prepared_count


def _apply_model_metadata(model: BaseModel, record: dict[str, object]) -> None:
    model.family = str(record["family"])
    model.task = str(record["task"])
    model.scale = str(record["scale"])
    model.filename = str(record["filename"])
    model.framework = str(record.get("framework") or "ultralytics")
    model.model_family = str(record.get("model_family") or record["family"])
    model.variant = str(record.get("variant") or record["scale"])
    model.artifact_format = str(
        record.get("artifact_format") or Path(model.filename).suffix.removeprefix(".")
    )
    metadata = record.get("artifact_metadata")
    model.artifact_metadata = dict(metadata) if isinstance(metadata, dict) else {}
    model.source_path = str(record["source_path"])
    model.status = str(record.get("status") or "ready")


def _has_real_artifact(model: BaseModel) -> bool:
    checksum = (model.checksum or "").lower()
    return bool(
        model.status == "ready"
        and model.local_uri
        and (model.size_bytes or 0) >= _MIN_REAL_MODEL_SIZE_BYTES
        and len(checksum) == 64
        and all(character in "0123456789abcdef" for character in checksum)
    )


def _placeholder_payload(record: dict[str, object]) -> bytes:
    model_id = str(record["id"])
    return f"visiox prepared base model placeholder: {model_id}\n".encode()


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
