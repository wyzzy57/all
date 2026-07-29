from collections.abc import Generator

from sqlalchemy.orm import Session

from visiox_db.session import get_session


def get_db_session() -> Generator[Session]:
    yield from get_session()
