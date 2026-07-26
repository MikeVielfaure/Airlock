"""Test bootstrap: point the artefact store at a throwaway SQLite file BEFORE
anything imports app.db (its engine reads the URL at import time), then bring
the schema to head through the real init path (Alembic)."""
import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="fx_test_db_")
os.environ["FX_DB_URL"] = f"sqlite:///{_tmp}/store.db"

from app.db import init_db  # noqa: E402

MIGRATION_PATH = init_db()   # exercised by test_store.test_schema_via_alembic
