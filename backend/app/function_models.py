"""
User-defined functions — the expression language, extended by its users.

A function is a named expression with named parameters, stored as an ordinary
versioned artefact. Written once, it is callable from every expression in the
app: computed columns, mapping links, `compute` and `filter` bricks. That is the
whole point — the places that evaluate expressions did not have to learn about
functions, because a function *is* an expression.

Two properties are deliberate:

  * **Parameters shadow columns, they do not merge with them.** Inside a
    function body `[montant]` is the argument named `montant`, never a column of
    the caller's table that happens to share the name. Without that isolation a
    function would behave differently depending on where it is called, which
    defeats reuse.

  * **Functions may call functions, up to a depth.** Composition is the reason
    to have them at all, but a cycle (`A` calling `B` calling `A`) must fail
    with a clear message rather than blowing the stack.
"""
from __future__ import annotations

import re
from typing import Callable, Dict, List

import pandas as pd
import yaml
from pydantic import BaseModel, Field, field_validator

MAX_CALL_DEPTH = 8
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class UserFunction(BaseModel):
    """`name(params…) = expr`, in the same mini-language as everything else."""
    name: str
    params: List[str] = Field(default_factory=list)
    expr: str
    description: str = ""

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        v = (v or "").strip()
        if not _NAME_RE.match(v):
            raise ValueError(f"'{v}' is not a valid function name (letters, digits, _)")
        return v.upper()          # expressions call functions in upper case

    @field_validator("params")
    @classmethod
    def _valid_params(cls, v: List[str]) -> List[str]:
        out = []
        for p in v:
            p = (p or "").strip()
            if not _NAME_RE.match(p):
                raise ValueError(f"'{p}' is not a valid parameter name")
            if p in out:
                raise ValueError(f"duplicate parameter '{p}'")
            out.append(p)
        return out

    @field_validator("expr")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not (v or "").strip():
            raise ValueError("a function needs an expression")
        return v


def function_to_yaml(f: UserFunction) -> str:
    return yaml.safe_dump(f.model_dump(), allow_unicode=True, sort_keys=False)


def function_from_yaml(text: str) -> UserFunction:
    return UserFunction(**(yaml.safe_load(text) or {}))


def build_registry(functions: List[UserFunction]) -> Dict[str, Callable]:
    """
    Turn stored functions into callables the expression engine can whitelist.

    Each callable evaluates its body with the arguments bound to the parameter
    names, in a one-row frame — reusing the very engine that evaluates every
    other expression, so a function cannot behave differently from inline code.
    """
    from app.services.compute_service import ComputeError, ComputeService

    registry: Dict[str, Callable] = {}
    depth = {"n": 0}

    def make(fn: UserFunction) -> Callable:
        def call(*args):
            if len(args) != len(fn.params):
                raise ComputeError(
                    f"{fn.name} expects {len(fn.params)} argument(s), got {len(args)}")
            if depth["n"] >= MAX_CALL_DEPTH:
                raise ComputeError(
                    f"{fn.name}: functions nested more than {MAX_CALL_DEPTH} deep "
                    f"(a cycle between functions?)")
            depth["n"] += 1
            try:
                # A one-row frame whose columns are the parameters: the body's
                # [param] references therefore resolve to the arguments, and to
                # nothing else.
                row = pd.DataFrame([{p: ("" if a is None else str(a))
                                     for p, a in zip(fn.params, args)}])
                engine = ComputeService(extra_functions=registry)
                return engine.evaluate(row, fn.expr).iloc[0]
            finally:
                depth["n"] -= 1
        call.__name__ = fn.name
        return call

    for fn in functions:
        registry[fn.name] = make(fn)
    return registry


def load_registry(session, environment: str = "") -> Dict[str, Callable]:
    """Every stored function of an environment, ready to extend the engine.
    A function that no longer parses is skipped rather than breaking every
    expression in the app."""
    from app import repository as repo

    out: List[UserFunction] = []
    try:
        arts = repo.list_artefacts(session, "function", environment=environment)
    except Exception:  # noqa: BLE001 — a missing table must not break evaluation
        return {}
    for art in arts:
        try:
            out.append(UserFunction(**repo.resolve_ref(session, art.id, None).body))
        except Exception:  # noqa: BLE001
            continue
    return build_registry(out)
