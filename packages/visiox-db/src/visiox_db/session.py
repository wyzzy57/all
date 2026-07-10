from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from visiox_common.settings import get_settings


@lru_cache(maxsize=8)
def _cached_db_engine(database_url: str) -> Engine:
    return create_engine(database_url, pool_pre_ping=True)


def create_db_engine(database_url: str | None = None) -> Engine:
    settings = get_settings()
    return _cached_db_engine(database_url or settings.postgres_dsn)


@lru_cache(maxsize=8)
def _cached_session_factory(database_url: str) -> sessionmaker[Session]:
    return sessionmaker(bind=create_db_engine(database_url), autoflush=False, expire_on_commit=False)


def create_session_factory(engine: Engine | None = None) -> sessionmaker[Session]:
    if engine is not None:
        return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    settings = get_settings()
    return _cached_session_factory(settings.postgres_dsn)


def get_session() -> Generator[Session]:
    session_factory = create_session_factory()
    with session_factory() as session:
        yield session
