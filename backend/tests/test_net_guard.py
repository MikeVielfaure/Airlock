"""
Ce que la garde de sortie doit refuser, et ce qu'elle doit laisser passer.

La seconde moitié compte autant que la première. Une première version de ce
module bloquait par défaut toutes les adresses non publiques et cassait
`test_a_flow_can_post_its_result_somewhere`, qui pousse des lignes vers un
serveur sur `127.0.0.1`. Ce test n'était pas en tort : parler à des serveurs
internes est ce que fait ce produit. D'où le découpage vérifié ici — ce qui
n'a aucun usage légitime est interdit sans recours, ce qui en a un est
autorisé par défaut et se restreint sur décision.
"""
import urllib.error
import urllib.request

import pytest

from app.services import net_guard
from app.services.net_guard import BlockedUrl, check_url


@pytest.fixture(autouse=True)
def _politique_par_defaut(monkeypatch):
    """Aucun test ne doit dépendre de l'environnement de la machine qui
    l'exécute."""
    monkeypatch.delenv("FX_EGRESS_BLOCK_PRIVATE", raising=False)
    monkeypatch.delenv("FX_EGRESS_ALLOWLIST", raising=False)


# ── Interdit sans recours : les schémas ────────────────────────────────────

@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "file:///app/.env",
    "ftp://interne/dump.sql",
    "data:text/plain;base64,aGVsbG8=",
    "gopher://interne:11211/",
    "/etc/passwd",                       # sans schéma du tout
])
def test_les_schemas_non_http_sont_refuses(url):
    """`file://` était le plus grave : l'ouvreur par défaut d'urllib le sert,
    donc une brique `api` lisait le disque au lieu d'appeler une API."""
    with pytest.raises(BlockedUrl, match="Schéma|hôte"):
        check_url(url)


@pytest.mark.parametrize("var,valeur", [
    ("FX_EGRESS_BLOCK_PRIVATE", "0"),
    ("FX_EGRESS_ALLOWLIST", "erp.interne.corp"),
])
def test_aucun_reglage_ne_rouvre_file(var, valeur, monkeypatch):
    """Le point de la séparation : les échappatoires portent sur les *plages
    d'adresses*, jamais sur les schémas."""
    monkeypatch.setenv(var, valeur)
    with pytest.raises(BlockedUrl, match="Schéma"):
        check_url("file:///etc/passwd")


# ── Interdit sans recours : les métadonnées d'instance ─────────────────────

@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/",
    "http://metadata.google.internal/computeMetadata/v1/",
    "http://[fe80::1]/",
])
def test_les_metadonnees_cloud_sont_refusees(url):
    with pytest.raises(BlockedUrl, match="lien-local|métadonnées"):
        check_url(url)


def test_les_metadonnees_restent_refusees_meme_avec_une_allowlist(monkeypatch):
    """Poser une allowlist ne doit pas devenir un moyen de s'autoriser
    l'adresse de métadonnées en la nommant."""
    monkeypatch.setenv("FX_EGRESS_ALLOWLIST", "169.254.169.254")
    with pytest.raises(BlockedUrl, match="lien-local"):
        check_url("http://169.254.169.254/latest/meta-data/")


# ── Autorisé par défaut : l'interne, parce que c'est le produit ────────────

@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8791/ingest",       # le cas de test_published
    "http://localhost:8000/api",
    "http://10.0.0.7/erp",
    "http://192.168.1.20:9000/commandes",
    "https://93.184.216.34/orders",
])
def test_l_interne_et_le_public_passent_par_defaut(url):
    assert check_url(url) == url


# ── Le mode strict, pour un déploiement exposé ─────────────────────────────

@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8000/",
    "http://10.0.0.7/admin",
    "http://192.168.1.1/",
    "http://172.16.0.5/",
    "http://[::1]:8000/",
])
def test_le_mode_strict_ferme_l_interne(url, monkeypatch):
    monkeypatch.setenv("FX_EGRESS_BLOCK_PRIVATE", "1")
    with pytest.raises(BlockedUrl, match="non publique"):
        check_url(url)


def test_le_mode_strict_laisse_passer_le_public(monkeypatch):
    monkeypatch.setenv("FX_EGRESS_BLOCK_PRIVATE", "1")
    assert check_url("https://93.184.216.34/orders")


def test_l_allowlist_est_exclusive(monkeypatch):
    monkeypatch.setenv("FX_EGRESS_ALLOWLIST", "erp.interne.corp")
    assert check_url("http://erp.interne.corp/commandes")
    with pytest.raises(BlockedUrl, match="hors de la liste"):
        check_url("https://api.partenaire.com/v1")


# ── Une résolution impossible n'est pas un refus ───────────────────────────

def test_un_nom_qui_ne_resout_pas_n_est_pas_bloque():
    """Ne pas pouvoir vérifier n'est pas refuser : la connexion échouera de
    toute façon, avec une erreur réseau qui désigne la bonne cause."""
    assert check_url("https://nom-qui-nexiste-vraiment-pas.invalid/x")


# ── Les redirections ───────────────────────────────────────────────────────

def test_l_ouvreur_n_a_ni_file_ni_ftp_handler():
    """Ceinture et bretelles : même si `check_url` était contourné, l'ouvreur
    ne sait plus servir un fichier local."""
    handlers = {type(h).__name__ for h in net_guard.opener().handlers}
    assert "FileHandler" not in handlers
    assert "FTPHandler" not in handlers
    assert "DataHandler" not in handlers


def test_une_redirection_vers_les_metadonnees_est_refusee():
    """Le contournement classique : l'URL de départ est publique et passe la
    garde, la redirection atterrit sur les métadonnées."""
    handler = net_guard._GuardedRedirectHandler()
    req = urllib.request.Request("https://93.184.216.34/depart")
    with pytest.raises(urllib.error.URLError, match="Redirection refusée"):
        handler.redirect_request(req, None, 302, "Found", {},
                                 "http://169.254.169.254/latest/meta-data/")


def test_une_redirection_vers_du_public_est_acceptee():
    handler = net_guard._GuardedRedirectHandler()
    req = urllib.request.Request("https://93.184.216.34/depart")
    assert handler.redirect_request(req, None, 302, "Found", {},
                                    "https://93.184.216.34/arrivee") is not None


# ── Le bout à bout ─────────────────────────────────────────────────────────

def test_urlopen_refuse_avant_tout_acces(tmp_path):
    """La preuve que le trou est bouché : le fichier existe, il est lisible,
    et l'appel ne le lit pas."""
    secret = tmp_path / "master.key"
    secret.write_text("FX_MASTER_KEY=ne-doit-jamais-sortir")
    with pytest.raises(BlockedUrl):
        net_guard.urlopen(urllib.request.Request(f"file://{secret}"), timeout=5)
