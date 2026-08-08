"""
net_guard.py
────────────
Le seul endroit où l'application décide qu'une URL fournie par un
utilisateur a le droit d'être appelée. Trois briques sortent sur le réseau
avec une adresse que quelqu'un a tapée — `api_source.call`, la brique
`http` du runner, et la sonde de connexion — et elles passaient toutes les
trois par `urllib.request.urlopen` nu.

Ce que `urlopen` nu accepte, et qu'aucune des trois ne voulait offrir :

- **`file:///etc/passwd`.** L'ouvreur par défaut d'urllib embarque un
  `FileHandler`. Une brique `api` dont l'`url` commence par `file://` ne
  fait pas un appel réseau : elle lit le disque du conteneur et sert le
  contenu comme jeu de données. `FX_MASTER_KEY` montée en fichier, le
  `.env`, la base SQLite — tout devient une source de flux. `ftp://` et
  `data:` sont ouverts pour les mêmes raisons.
- **`http://169.254.169.254/`.** L'adresse de métadonnées d'AWS, GCP et
  Azure. Elle répond aux rôles d'instance : sur une machine cloud, c'est
  un vol de credentials en une brique.
- **Les redirections.** Le piège qui vide une allowlist naïve : l'ouvreur
  par défaut suit les 3xx sans rien revérifier. Un domaine public
  parfaitement autorisé répond `302 → http://169.254.169.254/` et la garde
  posée sur l'URL de départ n'a servi à rien.

### Ce qui est interdit sans recours, et ce qui ne l'est pas

La première version de ce module bloquait aussi, par défaut, toutes les
adresses non publiques — donc `10.0.0.0/8`, `192.168.0.0/16` et
`127.0.0.1`. Exécuter la suite de tests a montré ce que ça coûtait :
`test_a_flow_can_post_its_result_somewhere` monte un serveur sur
`127.0.0.1` et pousse des lignes dedans. Ce n'est pas un artefact de test,
c'est le produit : un outil d'intégration déployé sur site existe pour
parler aux API **internes** de la maison.

Une protection qui casse l'usage normal se fait désactiver en bloc au
premier ticket, et on perd aussi celle qui comptait. Le périmètre est donc
découpé selon ce qui a un usage légitime et ce qui n'en a aucun :

| Ce qui est refusé | Contournable ? | Pourquoi |
|---|---|---|
| Schéma autre que `http`/`https` | **Non** | Aucune brique n'a de raison de lire un fichier local |
| Adresse lien-local (`169.254/16`, `fe80::/10`) | **Non** | C'est l'adresse de métadonnées cloud, rien d'autre |
| Boucle locale et plages privées | Oui, refus **désactivé par défaut** | C'est l'usage normal du produit |

Les deux premières lignes sont sans variable d'environnement : il n'existe
aucune configuration qui rouvre `file://` ou l'adresse de métadonnées. La
troisième s'active par `FX_EGRESS_BLOCK_PRIVATE=1` pour un déploiement
exposé sur Internet, où le produit ne parle qu'à des partenaires publics.
`FX_EGRESS_ALLOWLIST` (hôtes séparés par des virgules) fait le cas le plus
strict : n'autoriser que `erp.interne.corp` et rien d'autre.

### Ce que ça ne protège pas

**Le réamorçage DNS.** Entre la résolution faite ici et la connexion faite
par urllib, un nom peut changer de réponse. S'en prémunir vraiment demande
d'épingler l'IP validée jusque dans le socket, donc un transport maison —
disproportionné ici, où l'attaquant plausible est déjà un éditeur
authentifié de l'environnement. La fenêtre est nommée plutôt que masquée.

**Une résolution DNS qui échoue ne bloque pas.** Ne pas pouvoir vérifier
n'est pas la même chose que refuser : si le nom ne résout pas, la connexion
ne se fera pas non plus, et l'erreur réseau réelle est plus utile qu'un
refus de politique qui désignerait la mauvaise cause.
"""

from __future__ import annotations

import ipaddress
import os
import socket
import urllib.error
import urllib.request
from urllib.parse import urlparse

ALLOWED_SCHEMES = ("http", "https")
MAX_REDIRECTS = 5

# Les noms que les fournisseurs cloud font pointer vers leur service de
# métadonnées. Bloqués par leur nom en plus de leur adresse : sur GCP,
# `metadata.google.internal` résout vers 169.254.169.254, mais le vérifier
# aussi par le nom évite de dépendre du résolveur du conteneur.
_METADATA_HOSTS = {
    "metadata.google.internal", "metadata.goog",
    "instance-data", "instance-data.ec2.internal",
}


class BlockedUrl(Exception):
    """L'URL est refusée par la politique de sortie — jamais appelée."""


def _block_private() -> bool:
    return os.environ.get("FX_EGRESS_BLOCK_PRIVATE", "").strip().lower() in ("1", "true", "yes")


def _allowlist() -> set[str]:
    raw = os.environ.get("FX_EGRESS_ALLOWLIST", "")
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


def _resolve(host: str, port: int | None) -> list[ipaddress._BaseAddress]:
    """Les adresses derrière un nom, ou une liste vide si la résolution
    échoue — voir la docstring du module : ne pas pouvoir vérifier n'est pas
    refuser."""
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, ValueError):
        return []
    return [ipaddress.ip_address(i[4][0]) for i in infos]


def check_url(url: str) -> str:
    """Valide une URL, ou lève `BlockedUrl` en nommant la raison.

    Renvoie l'URL telle quelle pour que l'appel s'écrive
    `urlopen(check_url(u))` — la garde se voit sur la ligne qui sort.
    """
    parsed = urlparse((url or "").strip())

    # ── 1. Le schéma : sans recours ───────────────────────────────────────
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise BlockedUrl(
            f"Schéma « {parsed.scheme or '(aucun)'} » refusé : seuls "
            f"{' et '.join(ALLOWED_SCHEMES)} sont autorisés.")

    host = (parsed.hostname or "").lower()
    if not host:
        raise BlockedUrl("URL sans hôte.")

    # ── 2. Les métadonnées cloud : sans recours ───────────────────────────
    if host in _METADATA_HOSTS:
        raise BlockedUrl(
            f"« {host} » est un service de métadonnées d'instance — refusé.")

    addrs = _resolve(host, parsed.port or None)
    for ip in addrs:
        if ip.is_link_local:
            raise BlockedUrl(
                f"L'hôte « {host} » résout vers une adresse lien-local ({ip}) : "
                f"c'est l'adresse des métadonnées d'instance — refusé.")

    # ── 3. L'allowlist, quand elle existe, prime sur tout le reste ────────
    allowlist = _allowlist()
    if allowlist:
        if host not in allowlist:
            raise BlockedUrl(
                f"Hôte « {host} » hors de la liste autorisée (FX_EGRESS_ALLOWLIST).")
        return url

    # ── 4. Le privé : refusé seulement si on l'a demandé ──────────────────
    if _block_private():
        for ip in addrs:
            if (ip.is_private or ip.is_loopback or ip.is_multicast
                    or ip.is_reserved or ip.is_unspecified):
                raise BlockedUrl(
                    f"L'hôte « {host} » résout vers une adresse non publique ({ip}), "
                    f"et FX_EGRESS_BLOCK_PRIVATE est actif.")
    return url


class _GuardedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Revalide chaque saut. Sans ça, la garde ne porte que sur le premier —
    et une redirection est précisément la façon dont on la contourne."""

    max_redirections = MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            check_url(newurl)
        except BlockedUrl as e:
            raise urllib.error.URLError(f"Redirection refusée : {e}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def opener() -> urllib.request.OpenerDirector:
    """Un ouvreur monté à la main, sans `FileHandler`, `FTPHandler` ni
    `DataHandler` : la ceinture qui rend `file://` impossible même si
    `check_url` était contourné un jour, et qui revalide les redirections.

    Monté handler par handler et non par `build_opener` : celui-ci *ajoute*
    aux gestionnaires par défaut au lieu de les remplacer, donc
    `build_opener(HTTPHandler)` garde `FileHandler` — exactement le
    contraire de l'effet recherché. C'est un test qui l'a montré, pas une
    relecture.

    `ProxyHandler` est conservé : derrière un proxy d'entreprise, rien ne
    sortirait sans lui. Contrepartie à connaître — quand un proxy est
    configuré, c'est lui qui résout le nom, donc la vérification d'adresse
    faite ici devient indicative et c'est la politique du proxy qui tranche.
    """
    o = urllib.request.OpenerDirector()
    for handler in (urllib.request.ProxyHandler(),
                    urllib.request.HTTPHandler(),
                    urllib.request.HTTPSHandler(),
                    urllib.request.HTTPDefaultErrorHandler(),
                    urllib.request.HTTPErrorProcessor(),
                    _GuardedRedirectHandler()):
        o.add_handler(handler)
    return o


def urlopen(req, timeout: float):
    """Le remplaçant direct de `urllib.request.urlopen` pour toute URL qui
    vient d'un utilisateur. `req` est une `Request` ou une chaîne."""
    url = req.full_url if isinstance(req, urllib.request.Request) else req
    check_url(url)
    return opener().open(req, timeout=timeout)
