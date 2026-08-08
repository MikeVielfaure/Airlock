"""
size_guard.py
─────────────
Refuser un fichier trop gros **avant** que pandas ne le charge.

### Pourquoi la limite en mégaoctets ne suffisait pas

`FX_MAX_UPLOAD_MB` plafonne le corps de la requête à 200 Mo. Mais ce qui tue
le processus, ce n'est pas le fichier — ce sont les lignes. Mesuré sur un CSV
à 12 colonnes, en échantillonnant le RSS pendant le traitement :

    100 000 lignes  →   +81 Mo de pic
    250 000 lignes  →  +404 Mo
    400 000 lignes  →  +850 Mo
    600 000 lignes  → +1548 Mo

Le coût par tranche de 100 000 lignes **augmente** (81, 161, 212, 258 Mo) :
ce n'est pas le DataFrame final, ce sont les copies intermédiaires du
nettoyage. Et 128 octets par ligne mesurés signifient que les 200 Mo
autorisés laissent passer **1,64 million de lignes**, soit un pic extrapolé
de 4,1 Go. Sur une machine de 4 Go, le processus est tué — pas ralenti, tué,
sans message, en emportant les requêtes en cours de tout le monde.

Le reste du projet plafonne déjà au bon endroit : `flow_runner.MAX_ROWS` et
`dataset_frame_service.MAX_ROWS` valent 200 000, avec le commentaire « a
runaway source must not eat the process ». C'est la même intention, appliquée
au seul chemin qui l'avait manquée.

### Pourquoi DuckDB

Compter les lignes en Python demanderait de lire le fichier — c'est-à-dire
exactement ce qu'on essaie d'éviter. DuckDB lit le CSV en colonnes, hors du
tas Python, et rend un `count(*)` sans rien matérialiser. Mesuré sur le même
fichier de 1,5 million de lignes (189 Mo) :

    pandas.read_csv       →  767 Mo
    DuckDB count(*)       →    0 Mo

Le refus coûte donc quelques dizaines de millisecondes et aucune mémoire, là
où la vérification naïve coûterait le crash qu'elle prétend éviter. DuckDB
est déjà une dépendance du projet (`duck_compute` s'en sert pour les colonnes
SQL) : rien de neuf n'entre ici.

### La valeur du plafond

500 000 par défaut, et non 200 000 comme sur le chemin des flux. Les mesures
montrent que 400 000 passe confortablement (+850 Mo) : aligner sur 200 000
refuserait des fichiers qui fonctionnent aujourd'hui. `FX_MAX_ROWS` permet à
l'exploitant de descendre sur une petite machine ou de monter sur une grosse
— la règle de conversion est dans le tableau ci-dessus.
"""

from __future__ import annotations

import os
import tempfile

MAX_ROWS_DEFAULT = 500_000


class TooManyRows(Exception):
    """Le fichier dépasse le plafond de lignes — refusé avant chargement."""

    def __init__(self, rows: int, limit: int):
        self.rows, self.limit = rows, limit
        # Les milliers se séparent par une espace en français — mais formatés
        # nombre par nombre : un `.replace(",", " ")` sur la phrase entière
        # mangeait aussi la virgule de « Découpez-le, ou passez par… ».
        n, m = f"{rows:,}".replace(",", "\u202f"), f"{limit:,}".replace(",", "\u202f")
        super().__init__(
            f"Ce fichier contient {n} lignes ; la limite est de {m}. "
            f"Découpez-le, ou passez par un flux avec une source qui lit par "
            f"lots. (Réglable par FX_MAX_ROWS côté serveur.)")


def max_rows() -> int:
    try:
        return max(1, int(os.environ.get("FX_MAX_ROWS", MAX_ROWS_DEFAULT)))
    except ValueError:
        return MAX_ROWS_DEFAULT


def count_csv_rows(raw: bytes, delimiter: str = ",") -> int | None:
    """Le nombre de lignes de données d'un CSV, sans le charger en mémoire.

    Renvoie `None` si DuckDB n'arrive pas à lire le fichier — un CSV mal
    formé, un encodage exotique, un séparateur inattendu. Dans ce cas on
    laisse passer : ce module est un garde-fou volumétrique, pas un
    validateur, et refuser un fichier parce qu'on n'a pas su le compter
    déplacerait le problème au lieu de le résoudre. Le parseur du projet,
    lui, sait gérer ces cas et dira ce qui ne va pas.
    """
    try:
        import duckdb
    except ImportError:
        return None

    tmp = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as fh:
            fh.write(raw)
            tmp = fh.name
        d = (delimiter or ",")[:1] or ","
        con = duckdb.connect()
        try:
            n = con.execute(
                "SELECT count(*) FROM read_csv_auto(?, delim=?, header=true, "
                "ignore_errors=true, all_varchar=true)", [tmp, d]).fetchone()
            return int(n[0]) if n else None
        finally:
            con.close()
    except Exception:                        # noqa: BLE001 — voir la docstring
        return None
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def check_csv(raw: bytes, delimiter: str = ",") -> int | None:
    """Lève `TooManyRows` si le fichier dépasse le plafond. Renvoie le nombre
    de lignes compté, ou `None` s'il n'a pas pu l'être."""
    n = count_csv_rows(raw, delimiter)
    limite = max_rows()
    if n is not None and n > limite:
        raise TooManyRows(n, limite)
    return n
