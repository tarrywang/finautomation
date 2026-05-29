"""SQLite connection + schema management."""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from tax_auto.config import get_settings

# Import models so SQLModel.metadata sees them
from tax_auto.storage import models  # noqa: F401

if TYPE_CHECKING:
    from collections.abc import Iterator

_engine: Engine | None = None


def get_engine() -> Engine:
    """Cached SQLite engine with WAL mode."""
    global _engine
    if _engine is not None:
        return _engine

    s = get_settings()
    s.db_path.parent.mkdir(parents=True, exist_ok=True)

    _engine = create_engine(
        f"sqlite:///{s.db_path}",
        echo=False,
        connect_args={"check_same_thread": False},
    )

    # Enable WAL + foreign keys on every new connection
    @event.listens_for(_engine, "connect")
    def _enable_pragmas(dbapi_conn, _conn_record) -> None:  # type: ignore[no-untyped-def]
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

    return _engine


def init_db() -> None:
    """Create all tables. Idempotent."""
    SQLModel.metadata.create_all(get_engine())


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional session context."""
    sess = Session(get_engine())
    try:
        yield sess
        sess.commit()
    except Exception:
        sess.rollback()
        raise
    finally:
        sess.close()
