"""
Ce qui arrive aux sessions ouvertes quand on déploie.

`pickle` restaure un dataclass en réinjectant son `__dict__` sans appeler
`__init__` : un champ ajouté à `Session` depuis l'écriture du blob n'existe
donc pas au relecture. Sans `__setstate__`, la première session relue après
un déploiement lève `AttributeError` — au milieu du travail de quelqu'un.

Ces tests rejouent le scénario au lieu de le supposer.
"""
import pickle

import pandas as pd

from app.session import Session


def _blob_ecrit_avant(champs: tuple[str, ...], **kwargs) -> bytes:
    """Un blob tel que l'aurait écrit une version du code qui ne connaissait
    pas encore `champs`."""
    df = pd.DataFrame({"a": [1, 2, 3]})
    sess = Session(raw_df=df, work_df=df.copy(), **kwargs)
    for nom in champs:
        del sess.__dict__[nom]
    return pickle.dumps(sess)


def test_un_champ_ajoute_depuis_reprend_son_defaut():
    back = pickle.loads(_blob_ecrit_avant(("sensitivity", "deleted", "next_index")))
    assert back.sensitivity == {}
    assert back.deleted == set()
    assert back.next_index == 0


def test_la_session_reste_utilisable_apres_relecture():
    """Pas seulement « l'attribut existe » : les méthodes qui s'appuient
    dessus doivent fonctionner, sinon on a déplacé le plantage."""
    back = pickle.loads(_blob_ecrit_avant(("deleted", "next_index")))
    assert len(back.active_df()) == 3
    assert back.new_index() == 3


def test_les_champs_presents_ne_sont_jamais_ecrases():
    """Le défaut ne comble que ce qui manque — le blob a le dernier mot,
    sinon on remettrait à zéro l'état de travail à chaque relecture."""
    back = pickle.loads(_blob_ecrit_avant(("sensitivity",),
                                          file_type="XLSX", delimiter=",",
                                          edits_count=7))
    assert back.file_type == "XLSX"
    assert back.delimiter == ","
    assert back.edits_count == 7


def test_un_champ_supprime_du_code_ne_gene_pas():
    """Le sens inverse est gratuit, mais il mérite d'être verrouillé : un
    blob qui porte un champ que le code ne déclare plus doit se relire."""
    df = pd.DataFrame({"a": [1]})
    sess = Session(raw_df=df, work_df=df.copy())
    sess.__dict__["champ_d_une_ancienne_version"] = "peu importe"
    back = pickle.loads(pickle.dumps(sess))
    assert len(back.active_df()) == 1
