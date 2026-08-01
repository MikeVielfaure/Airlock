"""
Who may do what — the whole policy, in one readable place.

Scattering `require_role("editor")` across routes has two failure modes this
module exists to avoid. First, the policy becomes unreadable: answering "what
can an operator do?" means grepping the codebase and hoping nothing was missed.
Second, the meaning drifts — `editor` ends up covering both *editing a
configuration* and *processing a file*, which are different jobs done by
different people. An HR team must run files; it must not rewrite the rules those
files are checked against.

So capabilities are named, listed here, and each one states the minimum role.
The table can be read, reviewed and tested exhaustively — and the UI can simply
ask what the caller may do instead of guessing from a role name.

The ladder stays four rungs deep on purpose. Four comprehensible levels beat
twenty checkboxes nobody configures correctly; the granularity lives in the
capabilities, not in the roles.
"""
from __future__ import annotations

from typing import Dict, List, Optional

# viewer → operator → editor → admin
ROLE_RANK: Dict[str, int] = {"viewer": 0, "operator": 1, "editor": 2, "admin": 3}


CAPABILITIES: Dict[str, dict] = {
    # ── working with data ────────────────────────────────────────────
    "file.upload": {"min": "operator", "label": "Charger un fichier"},
    "file.process": {"min": "operator", "label": "Contrôler et nettoyer un fichier"},
    "file.edit_cells": {"min": "operator", "label": "Corriger des cellules et des lignes"},
    "file.export": {"min": "viewer", "label": "Exporter le résultat"},
    "report.read": {"min": "viewer", "label": "Lire le rapport"},

    # ── the correspondence table ─────────────────────────────────────
    # Deliberately operator: fixing an unmapped value is part of doing the work,
    # not of designing it. This is exactly the HR case — they must be able to
    # complete the table without being able to rewrite the configuration.
    "tco.append": {"min": "operator", "label": "Compléter la table de correspondance"},
    "tco.replace": {"min": "editor", "label": "Remplacer une table de correspondance"},

    # ── designing the treatment ──────────────────────────────────────
    "config.read": {"min": "viewer", "label": "Consulter une configuration"},
    "config.write": {"min": "editor", "label": "Créer ou modifier une configuration"},
    # Destroying shared material is an admin act: a configuration others depend
    # on outlives whoever wrote it.
    "config.delete": {"min": "admin", "label": "Archiver une configuration"},
    "mapping.write": {"min": "editor", "label": "Créer ou modifier un mapping"},
    "function.write": {"min": "editor", "label": "Créer ou modifier une fonction"},
    "edi_model.write": {"min": "editor", "label": "Créer ou modifier un modèle EDI"},

    # ── flows ────────────────────────────────────────────────────────
    # Running is not designing: an operator may launch a flow someone else built.
    "flow.run": {"min": "operator", "label": "Exécuter un flux"},
    "flow.write": {"min": "editor", "label": "Créer ou modifier un flux"},
    "flow.replay": {"min": "operator", "label": "Rejouer une exécution"},
    "ops.read": {"min": "operator", "label": "Consulter le tableau d'exploitation"},

    # ── the database ─────────────────────────────────────────────────
    "dataset.read": {"min": "viewer", "label": "Lire une table"},
    "dataset.write": {"min": "operator", "label": "Écrire dans une table"},
    # Creating a table is not the same as filling one: an operator feeds the
    # tables the business defined, an editor may invent new ones.
    "dataset.create": {"min": "editor", "label": "Créer une table"},
    # Enforced per-dataset, not here: `repo.can_on_dataset(perm, "manage")` in
    # `dataset_routes.py::set_dataset_grant` — an owner may share a table they
    # manage even below this rank, so a blanket role check would be both wrong
    # (too strict for an owner) and not enough (too loose for a non-owner
    # operator). Listed for discoverability, not as the actual gate.
    "dataset.share": {"min": "operator", "label": "Partager une table dont on est propriétaire"},
    "dataset.delete": {"min": "admin", "label": "Archiver une table"},

    # ── administration ───────────────────────────────────────────────
    "variables.read": {"min": "editor", "label": "Voir les points de connexion"},
    "variables.write": {"min": "admin", "label": "Modifier les points de connexion"},
    "env.profile": {"min": "admin", "label": "Configurer l'environnement"},
    "members.manage": {"min": "admin", "label": "Gérer les membres"},
    "keys.create": {"min": "admin", "label": "Créer une clé de confidentialité"},
    "keys.audit": {"min": "admin", "label": "Lire le journal des révélations"},
    # Note: *using* a key is not a capability at all — it depends on holding the
    # key, not on a role. Roles and key holders are separate on purpose, so that
    # being an administrator never implies being able to read salaries.
}


def can(role: Optional[str], capability: str) -> bool:
    """Whether a role satisfies a capability. Unknown capability → refused: a
    typo must not silently grant access."""
    spec = CAPABILITIES.get(capability)
    if spec is None or role is None:
        return False
    return ROLE_RANK.get(role, -1) >= ROLE_RANK.get(spec["min"], 99)


def capabilities_for(role: Optional[str]) -> List[str]:
    return sorted(c for c in CAPABILITIES if can(role, c))


def describe() -> List[dict]:
    """The policy, for a settings screen — so it can be reviewed by someone who
    will never read the code."""
    return [{"capability": c, "min_role": spec["min"], "label": spec["label"]}
            for c, spec in sorted(CAPABILITIES.items())]
