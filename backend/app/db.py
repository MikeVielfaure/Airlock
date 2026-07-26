"""
db.py
─────
Database setup for the persistence layer (configs, computed sets, TCOs, flows,
runs). SQLAlchemy 2.0 style.

Connection is chosen from the environment so the same code runs three ways:

  • Production / docker-compose : DATABASE_URL=postgresql+psycopg://user:pass@db/fx
  • Local dev without Postgres  : nothing set  → ./file_explorer.db (SQLite file)
  • Tests                       : FX_DB_URL=sqlite://  (in-memory, per-process)

Everything above the repository layer is storage-agnostic: the services speak
to `repository.py`, which speaks to these sessions. Swapping SQLite for Postgres
changes only this file's URL — nothing else.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool


def _url() -> str:
    # Explicit test override wins, then a real DATABASE_URL, then a local file.
    return (
        os.environ.get("FX_DB_URL")
        or os.environ.get("DATABASE_URL")
        or "sqlite:///./file_explorer.db"
    )


_URL = _url()
_is_sqlite = _URL.startswith("sqlite")
_is_memory = _URL in ("sqlite://", "sqlite:///:memory:")

# SQLite needs check_same_thread off for the FastAPI threadpool; an in-memory
# SQLite DB must use a StaticPool or every connection gets its own empty DB.
_connect_args = {"check_same_thread": False} if _is_sqlite else {}
_engine_kwargs: dict = {"connect_args": _connect_args, "future": True}
if _is_memory:
    _engine_kwargs["poolclass"] = StaticPool
elif not _is_sqlite:
    # Postgres: recycle stale connections, verify liveness before use.
    _engine_kwargs.update(pool_pre_ping=True, pool_recycle=1800)

engine = create_engine(_URL, **_engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    pass


def init_db() -> str:
    """
    Bring the schema to head. Alembic-first: run migrations when the scaffolding
    is present (the normal case), fall back to create_all if it isn't or if the
    upgrade fails (e.g. a pre-Alembic dev database) — never brick the app on
    migration plumbing. Returns which path was taken, for logs and tests.
    """
    from app import db_models  # noqa: F401  — register mappers
    from pathlib import Path
    ini = Path(__file__).resolve().parent.parent / "alembic.ini"
    if ini.exists():
        try:
            from alembic import command
            from alembic.config import Config
            cfg = Config(str(ini))
            cfg.set_main_option("script_location", str(ini.parent / "alembic"))
            cfg.set_main_option("sqlalchemy.url", _URL.replace("%", "%%"))
            command.upgrade(cfg, "head")
            return "alembic"
        except Exception:  # noqa: BLE001
            pass
    Base.metadata.create_all(bind=engine)
    return "create_all"


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commit on success, roll back on error, always close."""
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency — one session per request."""
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def commit(s: Session) -> None:
    """
    Make the transaction durable *before* the route builds its response.

    `get_session` also commits, but only when FastAPI tears the dependency down
    — which happens after the endpoint has returned. A client that writes and
    immediately reads back can therefore be served its own stale data: the write
    reports success, the very next GET still shows the previous contents. Every
    mutating route calls this explicitly so the answer it returns is the answer
    the database will give.
    """
    s.commit()
