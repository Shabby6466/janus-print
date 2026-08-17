"""Engine and session management."""

from __future__ import annotations

import functools
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings
from .models import Base


@functools.lru_cache
def get_engine() -> Engine:
    url = get_settings().database_url
    kwargs: dict = {"pool_pre_ping": True, "future": True}
    if url.startswith("sqlite"):
        # A shared in-memory database only survives on a single connection, so it needs
        # StaticPool. A file-backed one must NOT use StaticPool: the deep-scan fallback
        # runs in a background thread when Redis is down, and two threads sharing one
        # SQLAlchemy connection corrupt each other's result rows.
        in_memory = ":memory:" in url or url.endswith("sqlite://")
        kwargs = {"connect_args": {"check_same_thread": False}}
        if in_memory:
            from sqlalchemy.pool import StaticPool

            kwargs["poolclass"] = StaticPool
    engine = create_engine(url, **kwargs)

    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _record):  # pragma: no cover - trivial
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

    return engine


@functools.lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    with session_scope() as session:
        yield session


# Arbitrary but fixed: any process creating this schema takes the same lock.
_SCHEMA_LOCK_ID = 0x6A616E75  # "janu"


def init_db() -> None:
    """Create any missing tables, safely when several processes start at once.

    The API and the worker both do this on boot. `create_all` checks before creating, but
    that check and the CREATE are not atomic, so two processes starting together race and
    one dies with a duplicate-key error on pg_type — leaving a stack that looks up except
    for the one container that matters. A Postgres advisory lock serialises them; it is
    held for the transaction and released automatically.
    """
    engine = get_engine()

    if engine.dialect.name != "postgresql":
        Base.metadata.create_all(engine)
        return

    from sqlalchemy import text

    with engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _SCHEMA_LOCK_ID})
        Base.metadata.create_all(connection)


def reset_engine() -> None:
    """Test hook."""
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
