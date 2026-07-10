from visiox_db.session import create_session_factory


def test_default_session_factory_reuses_engine_pool():
    first = create_session_factory()
    second = create_session_factory()

    assert first.kw["bind"] is second.kw["bind"]
