"""
Une requête paramétrée et **réellement** en lecture seule contre une base
externe — liée par SQLAlchemy, jamais concaténée dans le texte SQL, donc une
valeur venue de n'importe où en amont ne peut pas devenir une injection. Le
seul moteur derrière la brique de flux `external_db` et une source de session
attachée : deux appelants, jamais deux façons de parler à une base.

### Pourquoi la lecture seule est appliquée, et non affirmée

La version précédente de ce fichier annonçait « read-only » dans sa docstring
et ne l'appliquait nulle part : `sqlalchemy.text(query)` exécute ce qu'on lui
donne. La paramétrisation, elle, était bien réelle — mais elle empêche une
*valeur* de devenir du SQL, pas une *requête* d'être un `DROP TABLE`. Comme
l'URL de connexion vient elle aussi de l'appelant, quiconque peut écrire un
flux pouvait écrire sur n'importe quelle base joignable depuis le conteneur.

Trois verrous, du plus faible au plus fort — et c'est délibérément dans cet
ordre qu'ils sont posés, le dernier étant le seul qui ne dépende pas de la
justesse de ce fichier :

1. **Une seule instruction.** `;` hors littéral est refusé : sans ça,
   `SELECT 1; DROP TABLE clients` passe la vérification du premier mot.
2. **Le verbe est `SELECT` ou `WITH`**, et aucun verbe d'écriture n'apparaît
   ensuite. Le second point n'est pas redondant : PostgreSQL accepte
   `WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x` — une écriture
   dont le premier mot est parfaitement innocent.
3. **La transaction elle-même est déclarée en lecture seule** quand le
   dialecte le sait (PostgreSQL, MySQL/MariaDB). C'est le verrou qui compte :
   il est tenu par le serveur de base, pas par l'analyse ci-dessus, donc il
   couvre ce que l'analyse n'a pas prévu.

### Ce que ça ne protège pas

Une fonction de lecture de fichier exposée par le serveur distant
(`pg_read_file`, `lo_import`) reste une lecture — donc autorisée par les trois
verrous. La réponse à celle-là n'est pas ici : c'est un **rôle SQL en lecture
seule** dans le DSN, ce que ce module ne peut pas imposer à la place de
l'exploitant. Sur un dialecte sans transaction en lecture seule (SQLite),
seuls les deux premiers verrous s'appliquent et ce rôle devient la seule
garantie réelle.
"""
from __future__ import annotations

import re

import pandas as pd
import sqlalchemy

# Les verbes qui écrivent ou changent le schéma. Cherchés en mot entier, donc
# une colonne `deleted_at` ou `update_time` ne déclenche rien.
_WRITE_VERBS = (
    "insert", "update", "delete", "merge", "upsert", "replace",
    "drop", "alter", "create", "truncate", "rename", "comment",
    "grant", "revoke", "vacuum", "analyze", "reindex", "cluster",
    "copy", "call", "do", "execute", "exec", "prepare", "declare",
    "attach", "detach", "pragma", "set", "reset", "lock", "listen",
    "notify", "begin", "commit", "rollback", "savepoint", "into",
)
_WRITE_RE = re.compile(r"\b(" + "|".join(_WRITE_VERBS) + r")\b", re.IGNORECASE)

_READ_VERBS = ("select", "with", "table", "values", "show", "explain", "describe")


class QueryTooLarge(Exception):
    """La requête a répondu plus de `max_rows` lignes."""


class QueryNotReadOnly(Exception):
    """La requête n'est pas une lecture — refusée avant d'atteindre la base."""


def _strip_noise(sql: str) -> str:
    """Retire commentaires, littéraux et identifiants entre guillemets, en
    remplaçant chacun par un espace.

    Les retirer *avant* d'analyser est ce qui rend l'analyse fiable dans les
    deux sens : un `;` dans une chaîne (`WHERE note = 'a;b'`) ne doit pas
    faire croire à deux instructions, et un `DROP` caché derrière un
    identifiant entre guillemets ne doit pas échapper à la recherche.
    """
    out, i, n = [], 0, len(sql)
    while i < n:
        c = sql[i]
        if c == "-" and sql.startswith("--", i):                    # -- jusqu'à la fin de ligne
            j = sql.find("\n", i)
            i = n if j < 0 else j
        elif c == "/" and sql.startswith("/*", i):                  # /* ... */
            j = sql.find("*/", i + 2)
            i = n if j < 0 else j + 2
            out.append(" ")
        elif c == "$":                                              # $tag$ ... $tag$ (PostgreSQL)
            m = re.match(r"\$[A-Za-z_0-9]*\$", sql[i:])
            if m:
                tag = m.group(0)
                j = sql.find(tag, i + len(tag))
                i = n if j < 0 else j + len(tag)
                out.append(" ")
            else:
                out.append(c)
                i += 1
        elif c in ("'", '"', "`"):                                  # littéral ou identifiant cité
            j, quote = i + 1, c
            while j < n:
                if sql[j] == "\\":                                  # échappement à la MySQL
                    j += 2
                    continue
                if sql[j] == quote:
                    if j + 1 < n and sql[j + 1] == quote:           # '' doublé = quote littérale
                        j += 2
                        continue
                    break
                j += 1
            i = j + 1
            out.append(" ")
        else:
            out.append(c)
            i += 1
    return "".join(out)


def assert_read_only(query: str) -> None:
    """Lève `QueryNotReadOnly` si la requête n'est pas une lecture unique.

    Séparé de `run_query` pour être testable seul et réutilisable par une
    validation à l'enregistrement d'un flux — refuser à l'écriture du flux
    plutôt qu'à son exécution en production, comme le fait déjà le graphe.
    """
    bare = _strip_noise(query or "").strip().rstrip(";").strip()
    if not bare:
        raise QueryNotReadOnly("Requête vide.")

    if ";" in bare:
        raise QueryNotReadOnly(
            "Une seule instruction par requête — le « ; » n'est pas autorisé.")

    verb = re.match(r"[A-Za-z]+", bare)
    if not verb or verb.group(0).lower() not in _READ_VERBS:
        raise QueryNotReadOnly(
            f"Seule une lecture est autorisée ici "
            f"({', '.join(v.upper() for v in _READ_VERBS[:4])}…), "
            f"pas « {(verb.group(0) if verb else bare[:20]).upper()} ».")

    found = _WRITE_RE.search(bare)
    if found:
        raise QueryNotReadOnly(
            f"« {found.group(0).upper()} » n'est pas une lecture. Une écriture "
            f"reste une écriture même dans un WITH.")


def _read_only_stmt(dialect: str) -> str | None:
    """Le SQL qui met la transaction en lecture seule, quand le dialecte
    connaît. `None` ailleurs : mieux vaut ne rien émettre qu'échouer sur une
    instruction que la base ne comprend pas."""
    if dialect.startswith(("postgresql", "mysql", "mariadb")):
        return "SET TRANSACTION READ ONLY"
    return None


def run_query(url: str, query: str, params: dict, max_rows: int) -> pd.DataFrame:
    """Chaque valeur revient en chaîne, `""` pour NULL — la forme que tout
    consommateur en aval attend déjà (les records d'une brique de flux, le
    DataFrame d'une source attachée). Lève `QueryNotReadOnly` avant tout
    accès réseau, `QueryTooLarge` au-delà de `max_rows` ; le reste (DSN
    invalide, SQL faux) remonte tel quel.
    """
    assert_read_only(query)                     # avant même d'ouvrir la connexion

    engine = sqlalchemy.create_engine(url)
    try:
        with engine.connect() as c:
            stmt = _read_only_stmt(engine.dialect.name)
            if stmt:
                c.execute(sqlalchemy.text(stmt))
            result = c.execute(sqlalchemy.text(query), params)
            cols = list(result.keys())
            rows = [dict(zip(cols, r)) for r in result.fetchmany(max_rows + 1)]
            c.rollback()                        # rien à valider : c'est une lecture
    finally:
        engine.dispose()
    if len(rows) > max_rows:
        raise QueryTooLarge(f"la requête renvoie plus de {max_rows} lignes — "
                            f"ajoutez une LIMIT ou affinez les params.")
    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame([{k: ("" if v is None else str(v)) for k, v in r.items()} for r in rows])
