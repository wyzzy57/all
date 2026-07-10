from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from visiox_api.seed_base_models import seed_yolo26_base_models
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
