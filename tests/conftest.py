import pytest

from apply_spy.db import init_db, make_engine


@pytest.fixture
def engine():
    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    yield engine
    engine.dispose()
