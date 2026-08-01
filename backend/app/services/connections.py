"""
The JSON body of a connection point, checked against the kind a caller
expects — shared by the flow runner and by session source-attachment, so a
hotfolder brick fed an smtp connection (or a "attach a BDD externe source"
request fed an `api` connection) fails with the same meaning wherever it is
asked for.
"""
from __future__ import annotations

import json


def parse_connection(name: str, raw_value: str | None, actual_kind: str | None,
                     expected_kind: str) -> dict:
    """The stored JSON body of `name`, or a `ValueError` naming exactly what's
    wrong — no connection with that name, the wrong kind, or a value that
    isn't a JSON object. Callers translate this into whatever error shape
    they need (`FlowError` in a flow, `HTTPException` in a route)."""
    if raw_value is None:
        raise ValueError(f"aucun point de connexion nommé « {name} »")
    if actual_kind != expected_kind:
        raise ValueError(f"« {name} » n'est pas une connexion {expected_kind}")
    try:
        data = json.loads(raw_value)
    except ValueError:
        raise ValueError(f"la connexion « {name} » n'est pas un JSON valide")
    if not isinstance(data, dict):
        raise ValueError(f"la connexion « {name} » n'est pas un objet JSON")
    return {**data, "_name": name}
