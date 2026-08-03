"""
dataset_frame_service.py
─────────────────────────
Load a stored table's rows as a plain DataFrame — the same shape attaching
it as a source anywhere else in the app already produces (a dataset source
in Calculs, a TCO's target_source, a flow's fixed source table). One
function, reused wherever something needs a table's *current* content
rather than a copy pinned into a session or a file.
"""
from __future__ import annotations

import pandas as pd
from sqlalchemy.orm import Session

from app import repository as repo

MAX_ROWS = 200_000  # matches the cap on any other attached source


def load_dataset_frame(s: Session, dataset_id: str) -> pd.DataFrame:
    """Raises `repository.NotFound` if the dataset doesn't exist."""
    from app.services import dataset_service as ds
    repo.get_dataset(s, dataset_id)   # NotFound propagates as-is
    total = repo.count_rows(s, dataset_id)
    rows = [r.data for r in repo.read_rows(s, dataset_id, offset=0, limit=min(total, MAX_ROWS))]
    df = pd.DataFrame(rows).astype("string").fillna("")
    df, _masked = ds.mask_encrypted_columns(df)
    return df
