"""
session.py
──────────
Persistent session store — the interactive workbench's raw/working
DataFrame, TCO, attached sources, edit history and so on, one row per
session in `work_sessions` (see `WorkSessionRow` in db_models.py).

This replaces what used to be an in-memory dict: a plain dict meant
`uvicorn --workers 2` split requests across two processes that never
shared a copy, and a restart lost every session mid-edit. The blob is
pickled whole rather than split field by field — `Session` carries
DataFrames, Series, sets and an arbitrary Pydantic `header_cfg`, and
pickling the object round-trips all of that natively instead of a bespoke
(de)serializer that would need special cases for the `set` fields and the
dict-of-Series ones. The blob is only ever written and read by this
backend, never accepted from an external caller, so this is the same trust
boundary as the rest of the app's data, not a new one.

The mutation contract stays the same shape callers already use: every
route used to do `sess = _session(sid)` then mutate attributes directly,
relying on it being the same in-memory object — mutation *was* persistence.
`store.session(s, sid)` is that same shape with an explicit save on a clean
exit: `with store.session(s, sid) as sess: sess.field = ...`. An exception
raised inside the block skips the save, so a request that fails partway
through does not persist a half-mutated session — the same "a partial load
writes nothing" reasoning already applied elsewhere in this app.
"""

from __future__ import annotations

import pickle
import time
import uuid
from contextlib import contextmanager
from dataclasses import MISSING, dataclass, field
from dataclasses import fields as dataclass_fields
from datetime import datetime, timedelta, timezone
from typing import Iterator, Optional

import pandas as pd
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session as DbSession

from app.db_models import WorkSessionRow

DEFAULT_TTL_SECONDS = 60 * 60


@dataclass
class Session:
    raw_df: pd.DataFrame                       # as loaded, never mutated
    work_df: pd.DataFrame                       # after header treatment
    file_type: str = "CSV"
    encoding: str = "utf-8"
    delimiter: str = ";"
    tco_df: Optional[pd.DataFrame] = None
    # Set only when tco_df was loaded *by reference* from the library (never
    # for a raw file upload) — lets "Correspondances" extend the artefact the
    # session actually uses instead of falling back to whatever the
    # environment's profile happens to have pinned, which may be a different
    # table entirely (or none).
    tco_artefact_id: Optional[str] = None
    # Extra sources joined against this session's own data for cross-source
    # SQL (name -> frame) — attached explicitly, never a second session.
    attached: dict = field(default_factory=dict)
    # A declared (local_column, source_column) pair per attached source —
    # lets a plain computed column address it directly as [name.field]
    # instead of only through a SQL block. Optional: a source with no key
    # here just isn't reachable outside SQL blocks, same as before.
    attached_keys: dict = field(default_factory=dict)
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

    def __setstate__(self, state: dict) -> None:
        """Un blob relu a été écrit par la version du code qui tournait
        *avant* le déploiement en cours. Or `pickle` restaure un dataclass
        en réinjectant son `__dict__` sans jamais appeler `__init__` : un
        champ ajouté depuis n'existe donc pas sur les blobs d'hier, et le
        premier accès lève `AttributeError`.

        Concrètement, sans ce crochet : on déploie une version qui ajoute un
        champ à `Session`, et toutes les sessions ouvertes — quelqu'un au
        milieu de la correction d'un fichier de 20 000 lignes — cassent
        jusqu'à l'expiration du TTL. Le champ manquant reprend donc sa
        valeur par défaut, ce qui est exactement le sens de « ce blob est
        antérieur à ce champ ».

        L'inverse est gratuit : un champ *supprimé* du dataclass reste dans
        le `__dict__` relu et n'y gêne personne.
        """
        restored = {}
        for f in dataclass_fields(self):
            if f.default_factory is not MISSING:      # type: ignore[misc]
                restored[f.name] = f.default_factory()  # type: ignore[misc]
            elif f.default is not MISSING:
                restored[f.name] = f.default
        restored.update(state)                         # le blob a toujours le dernier mot
        self.__dict__.update(restored)

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


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SessionStore:
    def __init__(self, ttl_seconds: int = DEFAULT_TTL_SECONDS):
        self._ttl = ttl_seconds

    def create(self, s: DbSession, raw_df: pd.DataFrame, **meta) -> str:
        self._sweep(s)
        sid = uuid.uuid4().hex
        sess = Session(raw_df=raw_df, work_df=raw_df.copy(), **meta)
        s.add(WorkSessionRow(id=sid, blob=pickle.dumps(sess)))
        s.commit()
        return sid

    def get(self, s: DbSession, sid: str) -> Session:
        row = s.get(WorkSessionRow, sid)
        if row is None:
            raise KeyError(sid)
        row.touched_at = _now()
        s.commit()
        # noqa S301 : le blob n'est jamais accepté d'un appelant externe — il
        # n'est écrit et relu que par ce backend, donc c'est la même frontière
        # de confiance que le reste des données de l'app. Un accès en écriture
        # à la table `work_sessions` vaut déjà exécution de code : c'est ce
        # constat, pas l'absence de risque, qui rend le choix acceptable.
        return pickle.loads(row.blob)  # noqa: S301

    def save(self, s: DbSession, sid: str, sess: Session) -> None:
        row = s.get(WorkSessionRow, sid)
        if row is None:
            raise KeyError(sid)
        sess.touched = time.time()
        row.blob = pickle.dumps(sess)
        row.touched_at = _now()
        s.commit()

    def drop(self, s: DbSession, sid: str) -> None:
        s.execute(delete(WorkSessionRow).where(WorkSessionRow.id == sid))
        s.commit()

    def count(self, s: DbSession) -> int:
        self._sweep(s)
        return int(s.scalar(select(func.count()).select_from(WorkSessionRow)) or 0)

    def _sweep(self, s: DbSession) -> None:
        cutoff = _now() - timedelta(seconds=self._ttl)
        s.execute(delete(WorkSessionRow).where(WorkSessionRow.touched_at < cutoff))

    @contextmanager
    def session(self, s: DbSession, sid: str) -> Iterator[Session]:
        """Get, yield for the caller to mutate freely, save on a clean exit
        only — see the module docstring for why an exception must not save."""
        sess = self.get(s, sid)
        yield sess
        self.save(s, sid, sess)


store = SessionStore()
