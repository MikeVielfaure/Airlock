"""
diff_service.py
────────────────
Compare two frames on a key — added / removed / changed rows.

A dedicated report, not a `ComputeService` expression: the mono-row engine
stays mono-row on purpose (see compute_service.py's own docstring), so a
genuine row-vs-row comparison across two frames gets its own capability here,
the same reasoning that put cross-source SQL in duck_compute.py rather than
bending the single-row expression engine to do something it was never meant
to. Unlike the `[source.champ]` lookup's key (deliberately single-column,
because it feeds a per-row expression), the key here may be composite: this
is a report, not a value returned into a cell.
"""
from __future__ import annotations

import pandas as pd


def _norm(df: pd.DataFrame) -> pd.DataFrame:
    """NaN and a real empty string must compare equal — the same convention
    used everywhere else a value is compared as text in this app."""
    return df.apply(lambda col: col.map(lambda v: "" if pd.isna(v) else str(v)))


def _key_dict(keys: list[str], k) -> dict:
    if len(keys) == 1:
        return {keys[0]: k}
    return dict(zip(keys, k))


def diff_frames(left: pd.DataFrame, right: pd.DataFrame, keys: list[str],
                sensitive: frozenset = frozenset(),
                max_sample_rows: int = 2000) -> dict:
    """
    Compare `left` (this session) against `right` (an attached source) on
    `keys` — one or more columns forming the row's identity on both sides.

    Returns counts (added/removed/changed/identical) plus a capped sample of
    the actual differences, never the whole diff for a large table — this is
    a report to look at, not a bulk export.

    `sensitive` names columns declared confidential by the last run. They are
    excluded from the value comparison entirely, not merely masked in the
    output: masking both sides to the same "•••••" would make them compare
    equal by construction and silently report "identical" for a column we
    genuinely cannot read — a false green is worse than admitting we didn't
    check (the same lesson `require_columns` already learned the hard way).
    The caller is expected to have masked both frames before calling this —
    excluding the column here is what stops a mismatched mask on one side
    (e.g. only `left` masked) from being misread as a real difference.
    """
    if not keys:
        raise ValueError("Choisissez au moins une colonne clé.")
    missing_left = [k for k in keys if k not in left.columns]
    if missing_left:
        raise ValueError(f"Colonne(s) clé absente(s) côté session : {', '.join(missing_left)}.")
    missing_right = [k for k in keys if k not in right.columns]
    if missing_right:
        raise ValueError(f"Colonne(s) clé absente(s) côté source : {', '.join(missing_right)}.")

    l = _norm(left)
    r = _norm(right)

    # A duplicate key on either side makes "the" matching row ambiguous —
    # the same fail-closed stance as the source-key lookup's ambiguous case:
    # refuse to guess, rather than silently comparing against the wrong row.
    if l.duplicated(subset=keys).any():
        raise ValueError("La clé choisie n'est pas unique côté session — "
                         "dédupliquez avant de comparer.")
    if r.duplicated(subset=keys).any():
        raise ValueError("La clé choisie n'est pas unique côté source — "
                         "dédupliquez avant de comparer.")

    all_common = [c for c in l.columns if c in r.columns and c not in keys]
    excluded_sensitive = [c for c in all_common if c in sensitive]
    common_cols = [c for c in all_common if c not in sensitive]

    l_idx = l.set_index(keys)
    r_idx = r.set_index(keys)

    only_left = l_idx.index.difference(r_idx.index)
    only_right = r_idx.index.difference(l_idx.index)
    both = l_idx.index.intersection(r_idx.index)

    changed: list[tuple] = []
    identical = 0
    for k in both:
        lrow, rrow = l_idx.loc[k], r_idx.loc[k]
        diffs = {c: {"was": lrow[c], "now": rrow[c]} for c in common_cols if lrow[c] != rrow[c]}
        if diffs:
            changed.append((k, diffs))
        else:
            identical += 1

    sample: list[dict] = []
    for k in list(only_right)[:max_sample_rows]:
        sample.append({"key": _key_dict(keys, k), "status": "added"})
    for k in list(only_left)[:max_sample_rows]:
        sample.append({"key": _key_dict(keys, k), "status": "removed"})
    for k, diffs in changed[:max_sample_rows]:
        sample.append({"key": _key_dict(keys, k), "status": "changed", "changes": diffs})

    total = len(only_right) + len(only_left) + len(changed)

    return {
        "keys": keys,
        "left_rows": int(len(left)), "right_rows": int(len(right)),
        "added": int(len(only_right)), "removed": int(len(only_left)),
        "changed": len(changed), "identical": identical,
        "columns_compared": common_cols,
        "columns_excluded_sensitive": excluded_sensitive,
        "sample": sample[:max_sample_rows],
        "truncated": total > max_sample_rows,
    }
