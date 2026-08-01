"""
compute_service.py
──────────────────
Computed columns from a small, safe, spreadsheet-like expression language.

A user writes an expression that produces a new column, referencing existing
columns with [Brackets]:

    IF([civilite] == "M", "Monsieur", "Madame")
    CONCAT([nom], " ", [prenom])
    UPPER([code])
    [prix] + " EUR"

Safety: expressions are parsed with `ast` and validated against a strict
node + function whitelist before evaluation. No `eval` of arbitrary code,
no attribute access, no imports, no dunder — only the operations below.

To add a function: write it, register it in FUNCTIONS. Nothing else changes.
"""

import ast
import json
import math
import re
from typing import Callable

import pandas as pd


def _dot_lookup(const: dict, name: str) -> str | None:
    """`[connexion.champ]` — one (or more, `a.b.c`) levels into a variable
    whose value happens to be a JSON object, the shape every référentiel
    connection point (and any hand-written structured `value`) already
    stores. `None` for anything that doesn't resolve — a plain `[NOM]` with
    no dot never reaches this function at all, so existing expressions are
    untouched."""
    if "." not in name:
        return None
    base, _, rest = name.partition(".")
    raw = const.get(base)
    if raw is None:
        return None
    try:
        val = json.loads(raw)
    except (TypeError, ValueError):
        return None
    for step in rest.split("."):
        if not isinstance(val, dict):
            return None
        val = val.get(step)
    if val is None:
        return ""
    if isinstance(val, (dict, list)):
        return json.dumps(val)
    return str(val)


def _is_blank(v) -> bool:
    """True for nulls and empty/whitespace values (used by ISNULL/NOTNULL)."""
    if v is None:
        return True
    try:
        if isinstance(v, float) and math.isnan(v):
            return True
    except (TypeError, ValueError):
        pass
    return str(v).strip() == ""


# ── column reference:  [Col Name]  ->  _ref('Col Name') ───────────────
_COL_RE = re.compile(r"\[([^\[\]]+)\]")

_MOIS_NOM = ["janvier", "février", "mars", "avril", "mai", "juin",
             "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
_MOIS_COURT = ["janv.", "févr.", "mars", "avr.", "mai", "juin",
               "juil.", "août", "sept.", "oct.", "nov.", "déc."]
_JOUR_NOM = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
_JOUR_COURT = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]


def dynamic_tokens(now=None) -> dict[str, str]:
    """Built-in dynamic values usable as [TOKEN] in expressions and in column
    names. French names; English aliases for the common ones."""
    from datetime import datetime
    d = now or datetime.now()
    m = d.month
    tok = {
        "DATENOW": d.strftime("%Y-%m-%d"),
        "AUJOURDHUI": d.strftime("%Y-%m-%d"),
        "NOW": d.strftime("%Y-%m-%d %H:%M:%S"),
        "ANNEE": str(d.year),
        "YEAR": str(d.year),
        "MOIS": str(m),
        "MONTH": str(m),
        "MOIS2": f"{m:02d}",
        "MOIS_NOM": _MOIS_NOM[m - 1],
        "MOIS_COURT": _MOIS_COURT[m - 1],
        "JOUR": str(d.day),
        "DAY": str(d.day),
        "JOUR2": f"{d.day:02d}",
        "JOUR_NOM": _JOUR_NOM[d.weekday()],
        "JOUR_COURT": _JOUR_COURT[d.weekday()],
    }
    return tok


def _fn_if(cond, a, b):
    return a if cond else b


def _fn_concat(*parts) -> str:
    return "".join("" if p is None else str(p) for p in parts)


def _fn_default(value, fallback):
    s = "" if value is None else str(value)
    return fallback if s.strip() == "" else value


def _fn_num(value):
    """Best-effort cast to float (handles ',' decimals); 0.0 on failure."""
    try:
        return float(str(value).replace(",", ".").replace(" ", ""))
    except (ValueError, TypeError):
        return 0.0


def _fn_regex_extract(value, pattern, group=0):
    """Return the matched substring (or capture group) of `pattern` in value."""
    try:
        m = re.search(pattern, str(value))
        if not m:
            return ""
        g = int(group)
        return m.group(g) if (g or m.lastindex) else m.group(0)
    except (re.error, IndexError):
        return ""


def _fn_split(value, sep, index):
    parts = str(value).split(str(sep))
    i = int(index)
    return parts[i] if -len(parts) <= i < len(parts) else ""


def _fn_ltrim(value, chars=None):
    """Strip leading characters (whitespace by default, or the given set)."""
    s = str(value)
    return s.lstrip() if chars in (None, "") else s.lstrip(str(chars))


def _fn_rtrim(value, chars=None):
    """Strip trailing characters (whitespace by default, or the given set)."""
    s = str(value)
    return s.rstrip() if chars in (None, "") else s.rstrip(str(chars))


def _fn_substring(value, start, length=None):
    s = str(value)
    st = int(start)
    return s[st:st + int(length)] if length is not None else s[st:]


def _fn_between(value, lo, hi):
    return _fn_num(lo) <= _fn_num(value) <= _fn_num(hi)


def _fn_round(value, ndigits=0):
    r = round(_fn_num(value), int(ndigits))
    return int(r) if int(ndigits) == 0 else r


def _fn_coalesce(*args):
    for a in args:
        if a is not None and str(a).strip() != "":
            return a
    return ""


def _fn_datediff(a, b):
    """Whole days between two dates (a - b). '' if either can't be parsed."""
    from dateutil import parser as _p
    try:
        return (_p.parse(str(a), dayfirst=True) - _p.parse(str(b), dayfirst=True)).days
    except (ValueError, OverflowError, TypeError):
        return ""


def _fn_style(color="", bold="", italic=""):
    """A conditional-formatting cell rule's whole point: return a small,
    engine-agnostic token — the same format a cross-source SQL rule
    produces by hand with a bare CASE WHEN, since DuckDB has no access to
    this function at all (a separate engine, deliberately)."""
    return f"color:{color};bold:{1 if bold else 0};italic:{1 if italic else 0}"


def _fn_lookup_noop(_value, default=""):
    """Placeholder — replaced per request with the session's TCO map."""
    return default


def _fn_exists_noop(_name):
    """Placeholder — replaced per request with the real column set."""
    return ""


def _fn_col_noop(_name, default=""):
    """Placeholder — replaced per request with the real row accessor."""
    return default


# Whitelisted functions exposed to expressions (upper-cased, spreadsheet-style)
FUNCTIONS: dict[str, Callable] = {
    "IF": _fn_if,
    "CONCAT": _fn_concat,
    "UPPER": lambda x: str(x).upper(),
    "LOWER": lambda x: str(x).lower(),
    "TRIM": lambda x: str(x).strip(),
    "LTRIM": _fn_ltrim,
    "RTRIM": _fn_rtrim,
    "TITLE": lambda x: str(x).title(),
    "LEN": lambda x: len(str(x)),
    "DEFAULT": _fn_default,
    "NUM": _fn_num,
    "STR": lambda x: "" if x is None else str(x),
    "REPLACE": lambda x, a, b: str(x).replace(str(a), str(b)),
    "LEFT": lambda x, n: str(x)[: int(n)],
    "RIGHT": lambda x, n: str(x)[-int(n):] if int(n) else "",
    "SUBSTRING": _fn_substring,
    "SPLIT": _fn_split,
    "CONTAINS": lambda x, sub: str(sub) in str(x),
    "STARTSWITH": lambda x, p: str(x).startswith(str(p)),
    "ENDSWITH": lambda x, p: str(x).endswith(str(p)),
    "REGEX_EXTRACT": _fn_regex_extract,
    "BETWEEN": _fn_between,
    "ROUND": _fn_round,
    "COALESCE": _fn_coalesce,
    "ISNULL": lambda x="": "1" if _is_blank(x) else "",
    "ISBLANK": lambda x="": "1" if _is_blank(x) else "",
    "ISEMPTY": lambda x="": "1" if _is_blank(x) else "",
    "NOTNULL": lambda x="": "" if _is_blank(x) else "1",
    "NOTBLANK": lambda x="": "" if _is_blank(x) else "1",
    "DATEDIFF": _fn_datediff,
    "STYLE": _fn_style,
    "LOOKUP": _fn_lookup_noop,
    "EXISTS": _fn_exists_noop,
    "COL": _fn_col_noop,
}

# Allowed AST node types — anything outside this set is rejected.
_ALLOWED = (
    ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not,
    ast.USub, ast.UAdd, ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.Div,
    ast.Mod, ast.Compare, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt,
    ast.GtE, ast.In, ast.NotIn, ast.IfExp, ast.Call, ast.Name, ast.Load,
    ast.Constant, ast.List, ast.Tuple,
)


class ComputeError(Exception):
    pass


class ComputeService:
    """
    Evaluates the expression mini-language.

    `extra_functions` lets callers extend the whitelist with user-defined
    functions without touching the built-ins: the registry is per-instance, so
    one environment's functions can never leak into another's evaluation.
    """

    def __init__(self, extra_functions: dict[str, Callable] | None = None):
        self.extra = dict(extra_functions or {})
        self._funcs = {**FUNCTIONS, **self.extra}

    def _preprocess(self, expr: str) -> str:
        # [NAME] -> _ref('NAME'): resolved at eval time as a column value, then a
        # config variable, then a built-in dynamic token (DATENOW, MOIS, JOUR…).
        # IMPORTANT: only substitute OUTSIDE string literals, so regex character
        # classes like "[0-9]" inside quotes are left intact.
        out: list[str] = []
        buf: list[str] = []
        quote: str | None = None
        i, n = 0, len(expr)

        def _flush() -> None:
            if buf:
                out.append(_COL_RE.sub(lambda m: f"_ref({m.group(1)!r})", "".join(buf)))
                buf.clear()

        while i < n:
            c = expr[i]
            if quote:                       # inside a string literal
                out.append(c)
                if c == "\\" and i + 1 < n:  # keep escaped char verbatim
                    out.append(expr[i + 1]); i += 2; continue
                if c == quote:
                    quote = None
                i += 1
            elif c in ("'", '"'):           # entering a string literal
                _flush(); quote = c; out.append(c); i += 1
            else:
                buf.append(c); i += 1
        _flush()
        return "".join(out)

    def _validate(self, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if not isinstance(node, _ALLOWED):
                raise ComputeError(f"Unsupported syntax: {type(node).__name__}")
            if isinstance(node, ast.Name):
                if node.id not in ("_ref", "_c") and node.id not in self._funcs:
                    raise ComputeError(f"Unknown name: '{node.id}'")
            if isinstance(node, ast.Call):
                if not isinstance(node.func, ast.Name):
                    raise ComputeError("Only direct function calls are allowed.")

    def compile_expr(self, expr: str):
        """Parse + validate once; returns a code object ready to eval per row."""
        if not expr or not expr.strip():
            raise ComputeError("Empty expression.")
        processed = self._preprocess(expr)
        try:
            tree = ast.parse(processed, mode="eval")
        except SyntaxError as e:
            raise ComputeError(f"Syntax error: {e.msg}")
        self._validate(tree)
        return compile(tree, "<expr>", "eval")

    def evaluate(self, df: pd.DataFrame, expr: str,
                 lookup_map: dict[str, str] | None = None,
                 variables: dict[str, str] | None = None) -> pd.Series:
        """
        Evaluate `expr` for every row of `df`, returning a string Series.
        A row that errors yields '#ERR' rather than crashing the request.

        [NAME] resolves, in order, to: a column value, then a config variable,
        then a built-in dynamic token (DATENOW, MOIS, JOUR…). `lookup_map`
        (source -> label) backs LOOKUP() against the loaded TCO. [NAME.field]
        reaches one level (or more, NAME.a.b) into a variable whose value is a
        JSON object — the shape a référentiel connection point already stores.
        """
        code = self.compile_expr(expr)
        # Real nulls (NaN/NaT/None) become "" so [x] is empty rather than the
        # literal text "nan"/"None"; genuine text like "nan" is left intact.
        cols = {c: ["" if pd.isna(v) else str(v) for v in df[c].tolist()] for c in df.columns}
        n = len(df)
        out: list[str] = []

        lm = lookup_map or {}
        const = {**dynamic_tokens(), **(variables or {})}   # variables override tokens

        def _lookup(value, default=""):
            return lm.get(str(value), default)

        def _exists(name):
            return "1" if str(name) in cols else ""

        for i in range(n):
            def _ref(name: str, _i=i):
                series = cols.get(name)
                if series is not None:
                    return series[_i]
                if name in const:
                    return const[name]
                return _dot_lookup(const, name) or ""

            def _col(name, default="", _i=i):
                series = cols.get(str(name))
                return series[_i] if series is not None else default

            env = {"_ref": _ref, "_c": _ref, **self._funcs,
                   "LOOKUP": _lookup, "EXISTS": _exists, "COL": _col, "__builtins__": {}}
            try:
                val = eval(code, env)            # noqa: S307 — sandboxed namespace
                out.append("" if val is None else str(val))
            except Exception:                     # noqa: BLE001
                out.append("#ERR")
        return pd.Series(out, index=df.index)

    def validate_expression(self, expr: str) -> str | None:
        """Return None if the expression compiles, else an error message."""
        try:
            self.compile_expr(expr)
            return None
        except ComputeError as e:
            return str(e)

# ── name resolution (for dynamic column names in field matching) ──────
def resolve_name(candidate: str, ctx: dict[str, str]) -> str:
    """
    Resolve a field's source-name candidate to an actual column name.

      • "Ville"               -> "Ville"               (literal)
      • "[MOIS_COURT]"        -> "juin"                (token / variable substitution)
      • "Ventes_[ANNEE]"      -> "Ventes_2026"         (embedded token)
      • "=LEFT([MOIS_NOM],4)" -> "juin"                (full expression, prefix '=')
      • '=CONCAT("V_",[ANNEE])' -> "V_2026"

    `ctx` = dynamic tokens + config variables. Unknown tokens are left as-is,
    and any evaluation error falls back to the literal candidate (so it simply
    won't match rather than crash).
    """
    s = (candidate or "").strip()
    if s.startswith("="):
        try:
            cs = ComputeService()
            val = cs.evaluate(pd.DataFrame({"_": [""]}), s[1:], variables=ctx)
            return str(val.iloc[0])
        except Exception:  # noqa: BLE001
            return candidate
    if "[" in s:
        return _COL_RE.sub(lambda m: ctx.get(m.group(1), m.group(0)), candidate)
    return candidate
