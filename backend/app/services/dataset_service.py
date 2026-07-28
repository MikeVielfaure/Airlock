"""
dataset_service.py
──────────────────
Writing cleaned data into the database, and — more importantly — refusing to
when it would be wrong.

THE TWO KINDS OF FAILURE
════════════════════════
A write can fail for two reasons that feel similar and are not:

  • the DATA is wrong — a SIRET is malformed, a date won't parse, an identifier
    is empty. Those are per-row problems. You fix them by hand in the editable
    Data view, re-run, and write. The rows are the unit.

  • the SKELETON is wrong — the file has columns the dataset doesn't know, the
    header landed on the wrong line, the config points at a different sheet.
    No amount of hand-editing fixes that: every row is "wrong" because the
    shape is wrong. You go back to the source, look at what the file really
    contains, and change the config.

Telling a user "3000 errors" when the truth is "your header is one line off"
is the failure mode this module exists to avoid. So preflight classifies every
problem as `config` or `data`, and says what to do about it.

NO RUNTIME DDL — see db_models.Dataset for why rows are stored as JSON.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any, Iterable, Optional

import pandas as pd

MODES = ("replace", "append", "upsert")
POLICIES = ("reject", "block", "all")   # what to do with rows that failed validation


# ══════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════
def _cell(v: Any) -> str:
    """Everything lands as text: a dataset column holds what the file held.
    Typing is the config's job upstream, not a silent cast down here."""
    if v is None:
        return ""
    if isinstance(v, float) and math.isnan(v):
        return ""
    return str(v)


def key_hash(row: dict, key_fields: list[str]) -> Optional[str]:
    """Digest of the key columns. NULL-keyed rows are legal (append has no key),
    so this returns None rather than inventing one."""
    if not key_fields:
        return None
    parts = [_cell(row.get(k, "")).strip() for k in key_fields]
    if not any(parts):
        return None
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def _problem(severity: str, category: str, code: str, message: str, hint: str,
             *, columns: Optional[list] = None, rows: Optional[list] = None,
             count: int = 0) -> dict:
    return {"severity": severity, "category": category, "code": code,
            "message": message, "hint": hint,
            "columns": columns or [], "rows": (rows or [])[:50], "count": count}


def _looks_like_a_broken_header(columns: Iterable[str]) -> list[str]:
    """Pandas names columns it could not read `Unnamed: 3`. A table mostly made
    of those means the header row is wrong — the archetypal skeleton problem."""
    cols = [str(c) for c in columns]
    suspicious = [c for c in cols
                  if c.startswith("Unnamed:") or c.strip() == "" or c.lower() == "nan"]
    return suspicious


# ══════════════════════════════════════════════════════════════════════
# Preflight — the eloquent gate
# ══════════════════════════════════════════════════════════════════════
def preflight(df: pd.DataFrame, *, mode: str, key_fields: list[str],
              dataset_schema: Optional[dict], dataset_row_count: int = 0,
              error_rows: Optional[set] = None, policy: str = "reject",
              dataset_exists: bool = False) -> dict:
    """
    Decide whether this table may be written, and explain the verdict.

    Returns {ok, blocked_by, problems[], plan{}}. `blocked_by` is "config" when
    the shape is wrong, "data" when rows are, None when the write may proceed.
    """
    problems: list[dict] = []
    error_rows = error_rows or set()
    cols = [str(c) for c in df.columns]

    if mode not in MODES:
        raise ValueError(f"mode doit être {' | '.join(MODES)}")
    if policy not in POLICIES:
        raise ValueError(f"policy doit être {' | '.join(POLICIES)}")

    # ── structural: is this even a table? ──────────────────────────────
    if not cols:
        problems.append(_problem(
            "error", "config", "TABLE_VIDE",
            "Le tableau ne contient aucune colonne.",
            "Vérifie le chargement du fichier (délimiteur, feuille, ligne d'en-tête)."))

    broken = _looks_like_a_broken_header(cols)
    if broken and len(broken) >= max(2, len(cols) // 2):
        problems.append(_problem(
            "error", "config", "SQUELETTE_SUSPECT",
            f"{len(broken)} colonnes sur {len(cols)} n'ont pas de nom exploitable.",
            "La ligne d'en-tête est probablement mal placée. Ouvre la source pour "
            "voir le fichier tel qu'il est lu, puis corrige la configuration "
            "(ligne d'en-tête, feuille, délimiteur) — ce n'est pas corrigeable "
            "ligne par ligne.",
            columns=broken))

    # ── structural: schema drift against an existing dataset ───────────
    if dataset_exists and dataset_schema:
        known = [str(c) for c in dataset_schema.get("columns", [])]
        missing = [c for c in known if c not in cols]
        extra = [c for c in cols if c not in known]

        if missing:
            problems.append(_problem(
                "error", "config", "COLONNES_MANQUANTES",
                f"{len(missing)} colonne(s) attendue(s) par la table sont absentes "
                f"du tableau : {', '.join(missing[:6])}"
                + ("…" if len(missing) > 6 else "") + ".",
                "Le fichier ou la configuration ne produit pas ces colonnes. "
                "Compare avec la source, ajuste la config (ou choisis le mode "
                "« remplacer » pour redéfinir la table).",
                columns=missing))

        if extra:
            if mode == "replace":
                problems.append(_problem(
                    "warning", "config", "SCHEMA_REDEFINI",
                    f"{len(extra)} colonne(s) nouvelle(s) : {', '.join(extra[:6])}"
                    + ("…" if len(extra) > 6 else "") + ".",
                    "Le mode « remplacer » a le droit de redéfinir le schéma : "
                    "la table adoptera celui-ci.",
                    columns=extra))
            else:
                problems.append(_problem(
                    "error", "config", "COLONNES_INCONNUES",
                    f"{len(extra)} colonne(s) absente(s) de la table : "
                    f"{', '.join(extra[:6])}" + ("…" if len(extra) > 6 else "") + ".",
                    "Ajouter des colonnes à une table existante est une décision "
                    "de schéma, pas un effet de bord d'un import. Utilise "
                    "« remplacer » pour redéfinir la table, ou aligne la config.",
                    columns=extra))

    # ── structural: the key ────────────────────────────────────────────
    if mode == "upsert":
        if not key_fields:
            problems.append(_problem(
                "error", "config", "CLE_ABSENTE",
                "Le mode « fusionner » demande une clé, aucune n'est définie.",
                "Marque au moins un champ comme identifiant dans l'onglet Schema, "
                "ou choisis la clé à la main."))
        absent = [k for k in key_fields if k not in cols]
        if absent:
            problems.append(_problem(
                "error", "config", "CLE_HORS_TABLE",
                f"Colonne(s) de clé absente(s) du tableau : {', '.join(absent)}.",
                "La clé doit exister dans les colonnes écrites.",
                columns=absent))

    # ── data: rows that failed validation ──────────────────────────────
    n_rows = int(len(df))
    n_err = len({i for i in error_rows if i in set(df.index)})
    if n_err:
        if policy == "block":
            problems.append(_problem(
                "error", "data", "LIGNES_EN_ERREUR",
                f"{n_err} ligne(s) sur {n_rows} sont encore en erreur.",
                "Corrige-les à la main dans l'onglet Data (le mode éditable), "
                "relance la validation, puis réessaie. Tu peux aussi supprimer "
                "le lot filtré, ou changer la règle d'écriture pour ne prendre "
                "que les lignes propres.",
                rows=sorted(error_rows)[:50], count=n_err))
        elif policy == "reject":
            problems.append(_problem(
                "warning", "data", "LIGNES_REJETEES",
                f"{n_err} ligne(s) en erreur ne seront pas écrites.",
                "Corrige-les dans l'onglet Data si tu veux les inclure.",
                rows=sorted(error_rows)[:50], count=n_err))

    # ── data: key quality ──────────────────────────────────────────────
    usable_key = key_fields and all(k in cols for k in key_fields)
    if mode == "upsert" and usable_key:
        # `_cell` and not astype(str): on a nullable dtype astype leaves NaN as
        # a float, and float has no .strip(). Same trap as the identifier crash
        # fixed in v10 — one hardened path is not enough, every path needs it.
        keys = (df[key_fields].apply(
            lambda r: "\x1f".join(_cell(v).strip() for v in r), axis=1)
            if n_rows else pd.Series(dtype=str))
        empty_idx = [int(i) for i, v in keys.items() if not v.replace("\x1f", "").strip()]
        if empty_idx:
            problems.append(_problem(
                "error", "data", "CLE_VIDE",
                f"{len(empty_idx)} ligne(s) ont une clé vide.",
                "Une ligne sans identifiant ne peut pas être fusionnée. "
                "Renseigne-la dans l'onglet Data, ou supprime la ligne.",
                rows=empty_idx, count=len(empty_idx)))
        dupes = keys[keys.duplicated(keep=False) & (keys.str.strip() != "")]
        if len(dupes):
            groups = dupes.groupby(dupes).groups
            sample = [str(k).replace("\x1f", " | ") for k in list(groups)[:5]]
            problems.append(_problem(
                "error", "data", "CLE_DUPLIQUEE",
                f"{len(dupes)} ligne(s) partagent {len(groups)} clé(s) en double "
                f"(ex. {', '.join(sample)}).",
                "En fusion, deux lignes de même clé s'écraseraient l'une l'autre. "
                "Corrige ou supprime les doublons dans l'onglet Data.",
                rows=[int(i) for i in dupes.index[:50]], count=int(len(dupes))))

    if n_rows == 0:
        problems.append(_problem(
            "error", "data", "AUCUNE_LIGNE",
            "Le tableau ne contient aucune ligne à écrire.",
            "Vérifie les suppressions et les filtres appliqués."))

    # ── verdict ────────────────────────────────────────────────────────
    blocking = [p for p in problems if p["severity"] == "error"]
    blocked_by = None
    if blocking:
        # config first: a broken skeleton makes every data complaint noise
        blocked_by = "config" if any(p["category"] == "config" for p in blocking) else "data"

    to_write = n_rows if policy == "all" else n_rows - n_err
    plan = {
        "mode": mode, "policy": policy, "key_fields": key_fields,
        "columns": cols, "rows_in": n_rows,
        "rows_to_write": max(0, to_write), "rows_rejected": 0 if policy == "all" else n_err,
        "existing_rows": dataset_row_count,
        "will_delete": dataset_row_count if (mode == "replace" and dataset_exists) else 0,
        "creates_dataset": not dataset_exists,
    }
    return {"ok": blocked_by is None, "blocked_by": blocked_by,
            "problems": problems, "plan": plan}


# ══════════════════════════════════════════════════════════════════════
# Payload preparation
# ══════════════════════════════════════════════════════════════════════
def rows_payload(df: pd.DataFrame, *, key_fields: list[str],
                 error_rows: Optional[set] = None, policy: str = "reject") -> list[dict]:
    """The rows actually destined for the table, as {key_hash, data} dicts."""
    error_rows = error_rows or set()
    cols = [str(c) for c in df.columns]
    out: list[dict] = []
    for idx, row in zip(df.index, df.itertuples(index=False, name=None)):
        if policy != "all" and idx in error_rows:
            continue
        data = {c: _cell(v) for c, v in zip(cols, row)}
        out.append({"key_hash": key_hash(data, key_fields), "data": data})
    return out


def schema_of(df: pd.DataFrame, key_fields: list[str],
              types: Optional[dict] = None) -> dict:
    """The schema a dataset records. The config already declared all of this —
    field types and identifiers — so it is carried over rather than guessed."""
    return {"columns": [str(c) for c in df.columns],
            "types": {str(c): (types or {}).get(str(c), "string") for c in df.columns},
            "key": list(key_fields)}


def mask_encrypted_columns(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Replace any column whose stored values look like ciphertext with the
    mask sentinel, in place of the real value — shared by every route that
    turns a stored table into something else (a session, a joined source),
    so a confidential column can't come back in clear by a second path."""
    from app.services import crypto_service as _cs

    out = df.copy()
    masked = [c for c in out.columns
             if any(_cs.is_encrypted(v) for v in out[c].head(200))]
    for c in masked:
        out[c] = [_cs.MASK if _cs.is_encrypted(v) else v for v in out[c]]
    return out, masked
