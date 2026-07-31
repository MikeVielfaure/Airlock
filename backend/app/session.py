"""
session.py
──────────
In-memory session store.

Each upload gets a session that holds the raw DataFrame, the working
DataFrame (after header treatment / edits) and an optional TCO table.
Sessions expire after a TTL so the process doesn't grow without bound.

This is deliberately simple — single-process, in-memory. For a multi-worker
deployment, swap the dict for Redis (store DataFrames as parquet bytes) behind
the same `get` / `put` interface; nothing else changes.
"""

import time
import threading
import uuid
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd


@dataclass
class Session:
    raw_df: pd.DataFrame                       # as loaded, never mutated
    work_df: pd.DataFrame                       # after header treatment
    file_type: str = "CSV"
    encoding: str = "utf-8"
    delimiter: str = ";"
    tco_df: Optional[pd.DataFrame] = None
    # Extra sources joined against this session's own data for cross-source
    # SQL (name -> frame) — attached explicitly, never a second session.
    attached: dict = field(default_factory=dict)
    last_df: Optional[pd.DataFrame] = None      # last processed frame (for export)
    last_cols: Optional[list] = None            # displayed columns of last run
    last_validation: Optional[dict] = None      # final col -> status Series (full file)
    last_clean_mask: Optional[dict] = None      # final col -> bool Series (full file)
    last_computed: Optional[list] = None        # names of computed columns
    last_styles: Optional[dict] = None          # col -> per-row style token Series, of last run
    last_report: Optional[pd.DataFrame] = None  # full report DataFrame of last run
    identifier_fields: Optional[list] = None     # id columns used in last run
    last_field_types: Optional[dict] = None      # col -> declared type; the config IS the schema
    # What was done to this table, in order. Only *reproducible* gestures are
    # recorded: a validation run and a computed column are logic and replay on
    # any file; a hand-edited cell is data about this file alone and never
    # becomes a flow step.
    history: list = field(default_factory=list)
    sensitivity: dict = field(default_factory=dict)   # column -> key name, propagated
    missing_keys: list = field(default_factory=list)
    header_cfg: Optional[object] = None          # last HeaderConfig applied (re-applied on edit reset)
    edits_count: int = 0                         # manual cell edits since load / last reset
    deleted: set = field(default_factory=set)    # logically removed row indices (restorable)
    added: set = field(default_factory=set)      # rows created by hand, for badging
    next_index: int = 0                          # monotonic index source for new rows
    created: float = field(default_factory=time.time)
    touched: float = field(default_factory=time.time)

    def active_df(self) -> pd.DataFrame:
        """The working table minus logically deleted rows — what the pipeline
        reads and what gets written to a dataset. Deletions stay reversible
        because `work_df` itself is never truncated."""
        if not self.deleted:
            return self.work_df
        keep = [i for i in self.work_df.index if i not in self.deleted]
        return self.work_df.loc[keep]

    def new_index(self) -> int:
        """A fresh row index, never reused — the stable key the whole edit
        system leans on, so collisions must be impossible."""
        self.next_index = max(self.next_index,
                              (int(self.work_df.index.max()) + 1) if len(self.work_df) else 0)
        i = self.next_index
        self.next_index += 1
        return i


class SessionStore:
    def __init__(self, ttl_seconds: int = 60 * 60):
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._data: dict[str, Session] = {}

    def create(self, raw_df: pd.DataFrame, **meta) -> str:
        sid = uuid.uuid4().hex
        with self._lock:
            self._sweep_locked()
            self._data[sid] = Session(
                raw_df=raw_df,
                work_df=raw_df.copy(),
                **meta,
            )
        return sid

    def get(self, sid: str) -> Session:
        with self._lock:
            sess = self._data.get(sid)
            if sess is None:
                raise KeyError(sid)
            sess.touched = time.time()
            return sess

    def drop(self, sid: str) -> None:
        with self._lock:
            self._data.pop(sid, None)

    def count(self) -> int:
        with self._lock:
            self._sweep_locked()
            return len(self._data)

    def _sweep_locked(self) -> None:
        now = time.time()
        expired = [k for k, s in self._data.items() if now - s.touched > self._ttl]
        for k in expired:
            self._data.pop(k, None)


store = SessionStore()
