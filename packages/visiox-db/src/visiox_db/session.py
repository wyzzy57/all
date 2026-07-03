from collections.abc import Generator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from visiox_common.settings import get_settings


def create_db_engine(database_url: str | None = None) -> Engine:
    settings = get_settings()
    return create_engine(database_url or settings.postgres_dsn, pool_pre_ping=True)


def create_session_factory(engine: Engine | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=engine or create_db_engine(), autoflush=False, expire_on_commit=False)


def get_session() -> Generator[Session]:
    session_factory = create_session_factory()
    with session_factory() as session:
        yield session
