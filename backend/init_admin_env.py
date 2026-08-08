"""
init_admin_env.py
─────────────────
Script d'initialisation : crée un compte administrateur général et configure
un environnement « Production » avec une sélection de modules activés.

### Le mot de passe ne peut plus être une constante

Ce script posait `password="adminpassword123"` en dur, sur un compte
**superadmin**, et l'imprimait. Comme il s'appelle `init_admin_env` et crée un
environnement nommé « Production », il ressemble exactement à une étape de
déploiement — donc l'instance partait avec un identifiant d'administrateur
général connu de quiconque a lu le dépôt.

C'est aussi l'inverse exact de ce que l'amorçage de l'application avait été
conçu pour éviter. Le README le dit dans ces termes : « Livrer verrouillé
derrière un mot de passe par défaut serait pire : les mots de passe par défaut
survivent au déploiement qui les a posés. » L'application reste ouverte tant
qu'aucun compte n'existe et se ferme dès le premier — précisément pour qu'il
n'y ait jamais de secret livré avec le code.

Désormais : `FX_ADMIN_PASSWORD` s'il est posé, sinon un mot de passe tiré au
hasard, affiché **une seule fois** au moment de la création. Rien à
retrouver dans un dépôt, rien à oublier de changer.
"""
import os
import secrets
import sys

# S'assurer d'être dans le dossier backend
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.db import commit, init_db, session_scope  # noqa: E402
from app.db_models import EnvironmentProfile, Membership  # noqa: E402
from app.services import auth_service as auth  # noqa: E402

ADMIN_EMAIL = os.getenv("FX_ADMIN_EMAIL", "admin@airlock.local")


def _password() -> tuple[str, bool]:
    """Le mot de passe à poser, et s'il a été tiré au sort (donc s'il faut
    l'afficher). Un mot de passe fourni par l'exploitant n'est jamais
    réimprimé : il le connaît déjà, et un journal de déploiement est un
    endroit durable."""
    fourni = os.getenv("FX_ADMIN_PASSWORD", "").strip()
    return (fourni, False) if fourni else (secrets.token_urlsafe(16), True)


def setup():
    init_db()
    with session_scope() as s:
        # 1. Compte Admin
        admin = auth.find_user(s, ADMIN_EMAIL)
        if not admin:
            mot_de_passe, tire = _password()
            admin = auth.create_user(
                s,
                email=ADMIN_EMAIL,
                display_name="Administrateur Airlock",
                password=mot_de_passe,
                superadmin=True,
            )
            print(f"[OK] Compte administrateur créé : {ADMIN_EMAIL}")
            if tire:
                print(f"     Mot de passe (affiché une seule fois) : {mot_de_passe}")
                print("     Notez-le maintenant : il n'est stocké que haché.")
            else:
                print("     Mot de passe : celui de FX_ADMIN_PASSWORD.")
        else:
            # Ne jamais réécrire le mot de passe d'un compte existant : ce
            # script doit pouvoir être relancé pour mettre à jour les modules
            # sans réinitialiser silencieusement un accès en place.
            print(f"[INFO] Compte administrateur existant : {ADMIN_EMAIL}")

        # 2. Environnement Production avec modules sélectionnés
        env_name = "Production"
        profile = s.get(EnvironmentProfile, env_name)
        active_modules = ["schema", "data", "report", "datasets", "canvas", "flows", "mapping"]

        if not profile:
            profile = EnvironmentProfile(
                name=env_name,
                label="Environnement de Production",
                description="Sas d'ingestion et contrôle de données de production.",
                modules_json=active_modules,
                config_locked=False,
                tco_editable=True,
            )
            s.add(profile)
            print(f"[OK] Environnement '{env_name}' créé avec modules : {active_modules}")
        else:
            profile.modules_json = active_modules
            profile.label = "Environnement de Production"
            print(f"[OK] Environnement '{env_name}' mis à jour avec modules : {active_modules}")

        from sqlalchemy import select
        m = s.scalars(select(Membership).where(Membership.user_id == admin.id,
                                               Membership.environment == env_name)).first()
        if not m:
            s.add(Membership(user_id=admin.id, environment=env_name, role="admin"))
            print(f"[OK] Rôle 'admin' attribué à {ADMIN_EMAIL} sur '{env_name}'")
        else:
            m.role = "admin"

        commit(s)
        print("[SUCCÈS] Initialisation admin & environnement terminée avec succès !")


if __name__ == "__main__":
    setup()
