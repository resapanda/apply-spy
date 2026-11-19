"""Engine and session helpers."""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from apply_spy.config import database_url
from apply_spy.db.models import Base


def make_engine(url: str | None = None) -> Engine:
    engine = create_engine(url or database_url())

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _record):
        # SQLite ignores foreign keys unless asked.
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    return engine


def _add_missing_columns(engine: Engine) -> None:
    """Add columns that newer versions of the tables have but an older database lacks.

    create_all only creates missing tables, so without this an existing database
    would break when a column is added to a model. Only nullable columns can be
    added this way (existing rows get an empty value); anything bigger needs a
    real migration.
    """
    existing_tables = set(inspect(engine).get_table_names())
    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            have = {column["name"] for column in inspect(connection).get_columns(table.name)}
            for column in table.columns:
                if column.name in have:
                    continue
                if not column.nullable:
                    raise RuntimeError(
                        f"Cannot add required column {table.name}.{column.name} to an "
                        "existing table automatically; this change needs a real migration."
                    )
                column_type = column.type.compile(dialect=engine.dialect)
                # Names come from our own models, never from user input.
                connection.execute(
                    text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {column_type}')
                )


def init_db(engine: Engine) -> None:
    """Create any missing tables and add any missing (nullable) columns."""
    Base.metadata.create_all(engine)
    _add_missing_columns(engine)


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    """One transaction: commit on success, roll back on error."""
    session = sessionmaker(engine, expire_on_commit=False)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
