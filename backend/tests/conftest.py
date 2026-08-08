"""Test bootstrap: point the artefact store at a throwaway SQLite file BEFORE
anything imports app.db (its engine reads the URL at import time), then bring
the schema to head through the real init path (Alembic). `setdefault` — not
assignment — so a caller-provided FX_DB_URL (e.g. pointing at Postgres for
the two-database check) is honored instead of silently overwritten.

Une base fournie de l'extérieur est ensuite **vidée**, et c'est ce qui rend la
suite rejouable. Sans ça, un second passage sur la même base PostgreSQL fait
tomber 56 tests : tous ceux qui supposent qu'aucun compte n'existe encore —
« le premier compte créé devient superadmin », l'amorçage ouvert, les
environnements vierges. Ces tests ont raison ; c'est l'état laissé par le
passage précédent qui a tort.

Le problème ne se voit jamais en CI, où chaque exécution démarre un conteneur
Postgres neuf. Il se voit sur un poste de développement où `FX_DB_URL` pointe
vers une base qu'on garde — et il coûte une heure à celui qui croit à une
régression.

On efface les lignes, pas le schéma : `DROP DATABASE` sur une base que
quelqu'un a désignée serait un dégât disproportionné pour un lancement de
tests, et vider les tables suffit à retrouver un état de départ.
"""
import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="fx_test_db_")
_fourni = "FX_DB_URL" in os.environ
os.environ.setdefault("FX_DB_URL", f"sqlite:///{_tmp}/store.db")

from app.db import init_db  # noqa: E402

MIGRATION_PATH = init_db()   # exercised by test_store.test_schema_via_alembic

if _fourni:
    from app.db import Base, engine  # noqa: E402

    with engine.begin() as _c:
        # Ordre inverse des dépendances : les tables filles d'abord, sinon une
        # clé étrangère refuse la suppression du parent.
        for _t in reversed(Base.metadata.sorted_tables):
            _c.execute(_t.delete())
