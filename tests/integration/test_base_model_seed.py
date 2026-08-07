from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from visiox_api.seed_base_models import (
    seed_paddlex_base_models,
    seed_yolo26_base_models,
)
from visiox_db.models import BaseModel
from visiox_storage.client import InMemoryObjectStorageClient


def test_seed_yolo26_base_models_prepares_all_models(tmp_path):
    database_path = tmp_path / "visiox-seed.db"
    database_url = f"sqlite:///{database_path}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    storage = InMemoryObjectStorageClient()

    with session_factory() as session:
      seed_yolo26_base_models(session, storage=storage)

    with session_factory() as session:
        models = session.scalars(select(BaseModel).order_by(BaseModel.task, BaseModel.scale)).all()

    assert len(models) == 30
    assert {model.status for model in models} == {"ready"}
    assert all(model.local_uri and model.local_uri.startswith("memory://models/base/") for model in models)
    assert len(storage.objects) == 30


def test_seed_yolo26_base_models_updates_existing_stale_models(tmp_path):
    database_path = tmp_path / "visiox-seed-existing.db"
    database_url = f"sqlite:///{database_path}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    storage = InMemoryObjectStorageClient()

    with session_factory() as session:
        session.add(
            BaseModel(
                id="yolo26-detect-n",
                family="yolo26",
                task="detect",
                scale="n",
                filename="yolo26n.pt",
                source_path="yolo26/detect/yolo26n.pt",
                status="pending",
            )
        )
        session.commit()

    with session_factory() as session:
        seed_yolo26_base_models(session, storage=storage)

    with session_factory() as session:
        model = session.get(BaseModel, "yolo26-detect-n")

    assert model is not None
    assert model.status == "ready"
    assert model.local_uri == "memory://models/base/yolo26-detect-n/yolo26n.pt"


def test_seed_yolo26_base_models_preserves_existing_real_weights(tmp_path):
    database_path = tmp_path / "visiox-seed-real.db"
    database_url = f"sqlite:///{database_path}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    storage = InMemoryObjectStorageClient()
    checksum = "a" * 64

    with session_factory() as session:
        session.add(
            BaseModel(
                id="yolo26-detect-n",
                family="yolo26",
                task="detect",
                scale="n",
                filename="yolo26n.pt",
                source_path="yolo26/detect/yolo26n.pt",
                local_uri="memory://models/base/yolo26-detect-n/yolo26n.pt",
                checksum=checksum,
                size_bytes=2 * 1024 * 1024,
                status="ready",
            )
        )
        session.commit()

    with session_factory() as session:
        seed_yolo26_base_models(session, storage=storage)

    with session_factory() as session:
        model = session.get(BaseModel, "yolo26-detect-n")

    assert model is not None
    assert model.checksum == checksum
    assert model.size_bytes == 2 * 1024 * 1024
    assert ("models", "base/yolo26-detect-n/yolo26n.pt") not in storage.objects


def test_seed_paddlex_base_models_registers_official_references_without_weights(tmp_path):
    database_path = tmp_path / "visiox-paddlex-seed.db"
    database_url = f"sqlite:///{database_path}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    storage = InMemoryObjectStorageClient()

    with session_factory() as session:
        seed_paddlex_base_models(session, storage=storage)

    with session_factory() as session:
        models = {
            model.id: model
            for model in session.scalars(select(BaseModel)).all()
        }

    assert set(models) == {"paddlex-pp-yoloe-s", "paddlex-rt-detr-l"}
    pp_yoloe = models["paddlex-pp-yoloe-s"]
    assert pp_yoloe.framework == "paddlex"
    assert pp_yoloe.model_family == "PP-YOLOE"
    assert pp_yoloe.variant == "S"
    assert pp_yoloe.artifact_format == "pdparams"
    assert pp_yoloe.source_path == "paddlex://official/PP-YOLOE_plus-S"
    assert pp_yoloe.artifact_metadata == {
        "config_path": "paddlex/configs/modules/object_detection/PP-YOLOE_plus-S.yaml",
        "revision": "paddlex-model-zoo/3.0.3/PP-YOLOE_plus-S",
        "runtime_model_id": "PP-YOLOE_plus-S",
        "source_id": "PaddlePaddle/PaddleX:PP-YOLOE_plus-S",
    }
    assert pp_yoloe.checksum is None
    assert pp_yoloe.local_uri is None
    assert pp_yoloe.status == "available"

    rt_detr = models["paddlex-rt-detr-l"]
    assert rt_detr.framework == "paddlex"
    assert rt_detr.model_family == "RT-DETR"
    assert rt_detr.variant == "L"
    assert rt_detr.artifact_format == "pdparams"
    assert rt_detr.source_path == "paddlex://official/RT-DETR-L"
    assert rt_detr.artifact_metadata == {
        "config_path": "paddlex/configs/modules/object_detection/RT-DETR-L.yaml",
        "revision": "paddlex-model-zoo/3.0.3/RT-DETR-L",
        "runtime_model_id": "RT-DETR-L",
        "source_id": "PaddlePaddle/PaddleX:RT-DETR-L",
    }
    assert rt_detr.checksum is None
    assert rt_detr.local_uri is None
    assert rt_detr.status == "available"
    assert storage.objects == {}
