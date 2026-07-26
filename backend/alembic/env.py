"""Alembic environment — wired to the app's metadata.

URL resolution must match app.db exactly, and for the same reason it exists
there: FX_DB_URL, then DATABASE_URL, then the sqlite fallback. When the app
boots it drives Alembic from Python and injects the URL itself, but the command
line (`alembic upgrade head`, `alembic revision --autogenerate`) reads this file
instead — and taking alembic.ini at face value would point every CLI migration
at the sqlite fallback while the app runs on PostgreSQL. An autogenerate run
against the resulting empty database would happily emit a migration that drops
every table.

render_as_batch is enabled on SQLite so future ALTERs work there too.
"""
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.db import Base
from app import db_models  # noqa: F401 — register tables on the metadata

config = context.config


def resolve_url() -> str:
    """The URL the application itself would use, unless the caller overrode it."""
    injected = config.get_main_option("sqlalchemy.url", "")
    from app.db import _URL as app_url
    env = os.getenv("FX_DB_URL") or os.getenv("DATABASE_URL")
    # A URL injected by app.db.init_db already reflects the environment; an
    # explicit -x url=... wins over everything.
    override = context.get_x_argument(as_dictionary=True).get("url")
    return override or env or injected or app_url


config.set_main_option("sqlalchemy.url", resolve_url().replace("%", "%%"))
if config.config_file_name is not None:
    try:
        fileConfig(config.config_file_name)
    except Exception:  # noqa: BLE001 — minimal ini without logging sections
        pass

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata,
                      literal_binds=True, render_as_batch=url.startswith("sqlite"))
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata,
                          render_as_batch=connection.dialect.name == "sqlite")
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
