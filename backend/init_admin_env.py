"""
init_admin_env.py
─────────────────
Script d'initialisation pour créer un compte administrateur et configurer un
environnement "Production" avec une sélection de modules activés.
"""
import sys
import os

# S'assurer d'être dans le dossier backend
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.db import session_scope, init_db, commit
from app.services import auth_service as auth
from app.db_models import EnvironmentProfile, User, Membership

def setup():
    init_db()
    with session_scope() as s:
        # 1. Compte Admin
        admin_email = "admin@airlock.local"
        admin = auth.find_user(s, admin_email)
        if not admin:
            admin = auth.create_user(
                s,
                email=admin_email,
                display_name="Administrateur Airlock",
                password="adminpassword123",
                superadmin=True
            )
            print(f"[OK] Compte administrateur créé : {admin_email} / adminpassword123")
        else:
            print(f"[INFO] Compte administrateur existant : {admin_email}")

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
                tco_editable=True
            )
            s.add(profile)
            print(f"[OK] Environnement '{env_name}' créé avec modules : {active_modules}")
        else:
            profile.modules_json = active_modules
            profile.label = "Environnement de Production"
            print(f"[OK] Environnement '{env_name}' mis à jour avec modules : {active_modules}")

        from sqlalchemy import select
        m = s.scalars(select(Membership).where(Membership.user_id == admin.id, Membership.environment == env_name)).first()
        if not m:
            s.add(Membership(user_id=admin.id, environment=env_name, role="admin"))
            print(f"[OK] Rôle 'admin' attribué à {admin_email} sur '{env_name}'")
        else:
            m.role = "admin"

        commit(s)
        print("[SUCCÈS] Initialisation admin & environnement terminée avec succès !")

if __name__ == "__main__":
    setup()
