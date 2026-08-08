"""
A live, best-effort reachability check for a connection point's draft value —
before it is even saved. Deliberately separate from the flow bricks
(`flow_runner.py`): a brick reads or writes real data and is part of a run
that gets journalled; this only asks "can we reach it?", with a short
timeout everywhere, and it must never raise — a bad host or a wrong password
is a result to report, not a server error.
"""
from __future__ import annotations

from typing import Any, Dict

TIMEOUT = 5.0


def test_connection(kind: str, data: Dict[str, Any]) -> Dict[str, Any]:
    try:
        if kind == "hotfolder":
            return _test_hotfolder(data)
        if kind == "smtp":
            return _test_smtp(data)
        if kind == "external_db":
            return _test_external_db(data)
        if kind == "sftp":
            return _test_sftp(data)
        if kind == "api":
            return _test_api(data)
        return {"ok": False, "message": f"'{kind}' n'a rien à tester."}
    except Exception as e:  # noqa: BLE001 — a failed test is a result, not a crash
        return {"ok": False, "message": f"{type(e).__name__}: {e}"}


def _test_hotfolder(data: Dict[str, Any]) -> Dict[str, Any]:
    import glob
    import os

    path = str(data.get("path") or "")
    if not path:
        return {"ok": False, "message": "Chemin manquant."}
    if not os.path.isdir(path):
        return {"ok": False, "message": f"« {path} » n'existe pas ou n'est pas un dossier."}
    count = len([f for f in glob.glob(os.path.join(path, "*")) if os.path.isfile(f)])
    return {"ok": True, "message": f"Dossier accessible — {count} fichier(s)."}


def _test_smtp(data: Dict[str, Any]) -> Dict[str, Any]:
    import smtplib

    host = str(data.get("host") or "")
    if not host:
        return {"ok": False, "message": "Hôte manquant."}
    port = int(data.get("port") or 587)
    user = str(data.get("user") or "")
    password = str(data.get("password") or "")
    with smtplib.SMTP(host, port, timeout=TIMEOUT) as smtp:
        if data.get("use_tls", True):
            smtp.starttls()
        if user:
            smtp.login(user, password)
    return {"ok": True, "message": f"Connexion à {host}:{port} réussie."}


def _test_external_db(data: Dict[str, Any]) -> Dict[str, Any]:
    import sqlalchemy

    url = str(data.get("url") or "")
    if not url:
        return {"ok": False, "message": "URL manquante."}
    engine = sqlalchemy.create_engine(url)
    try:
        with engine.connect() as c:
            c.execute(sqlalchemy.text("SELECT 1"))
    finally:
        engine.dispose()
    return {"ok": True, "message": "Connexion réussie."}


def _test_sftp(data: Dict[str, Any]) -> Dict[str, Any]:
    import io

    import paramiko

    host = str(data.get("host") or "")
    user = str(data.get("user") or "")
    remote_dir = str(data.get("remote_dir") or "")
    if not (host and user and remote_dir):
        return {"ok": False, "message": "Hôte, utilisateur et dossier distant requis."}
    port = int(data.get("port") or 22)
    transport = paramiko.Transport((host, port))
    try:
        if data.get("private_key"):
            pkey = paramiko.RSAKey.from_private_key(io.StringIO(str(data["private_key"])))
            transport.connect(username=user, pkey=pkey)
        else:
            transport.connect(username=user, password=str(data.get("password") or ""))
        sftp = paramiko.SFTPClient.from_transport(transport)
        try:
            count = len(sftp.listdir(remote_dir))
        finally:
            sftp.close()
    finally:
        transport.close()
    return {"ok": True, "message": f"Connexion réussie — {count} entrée(s) dans « {remote_dir} »."}


def _test_api(data: Dict[str, Any]) -> Dict[str, Any]:
    import urllib.error
    import urllib.request

    base_url = str(data.get("base_url") or "")
    if not base_url:
        return {"ok": False, "message": "URL de base manquante."}
    headers = {}
    if data.get("token"):
        headers[str(data.get("auth_header") or "Authorization")] = str(data["token"])
    from app.services import net_guard

    req = urllib.request.Request(base_url, headers=headers, method="GET")  # noqa: S310 — validé par net_guard.urlopen
    try:
        with net_guard.urlopen(req, timeout=TIMEOUT) as resp:
            status = resp.status
    except net_guard.BlockedUrl as e:
        # Une sonde est justement l'endroit où quelqu'un essaie une adresse
        # pour voir : elle doit répondre « refusé », pas la joindre.
        return {"ok": False, "message": str(e)}
    except urllib.error.HTTPError as e:
        # The server answered — that IS reachability, even for a 404/401
        # that a bare ping-style check would otherwise call a failure.
        status = e.code
    return {"ok": True, "message": f"Réponse HTTP {status}."}
