"""Engine + sessionmaker for the Postgres warehouse.

Reads DB URL from env (matches financeautomation/.env layout).
"""

from __future__ import annotations

import os
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


def _build_database_url() -> str:
    if explicit := os.environ.get("DATABASE_URL"):
        return explicit
    user = os.environ.get("PG_USER", "fapiao")
    pw = os.environ.get("PG_PASSWORD", "fapiao_dev_only")
    host = os.environ.get("PG_HOST", "127.0.0.1")
    port = os.environ.get("PG_PORT", "5433")
    db = os.environ.get("PG_DB", "fapiao")
    return f"postgresql+psycopg://{user}:{pw}@{host}:{port}/{db}"


def make_engine() -> Engine:
    url = _build_database_url()
    return create_engine(url, pool_pre_ping=True, future=True)


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = make_engine()
    return _engine


def get_sessionmaker() -> sessionmaker[Session]:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    return _SessionLocal


def session_scope() -> Iterator[Session]:
    """Generator-style context manager for ad-hoc scripts.

    Usage:
        with next(session_scope()) as s:
            s.add(...)
            s.commit()
    """
    sm = get_sessionmaker()
    s = sm()
    try:
        yield s
    finally:
        s.close()
