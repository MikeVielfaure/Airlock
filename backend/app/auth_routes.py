"""
Authentication routes, delegated administration, and the guard that makes
environments real.

The guard is the important part. Until now `?env=rh` was a URL parameter:
anyone could type `?env=adv` and read another team's material. Hiding tabs
prevented mistakes, not access. `current_env` resolves the environment from the
**session**, checks the caller is a member, and refuses otherwise — which is
what turns a display convention into a boundary.
"""
from __future__ import annotations

import secrets
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import repository as repo
from app.db import commit, get_session
from app.db_models import AuthProvider, AuthSession, Membership, User
from app.services import auth_service as auth
from app.services import permissions as perms

router = APIRouter(prefix="/api/auth", tags=["auth"])
admin_router = APIRouter(prefix="/api/admin", tags=["admin"])


# ══════════════════════════════════════════════════════════════════════
# Dependencies
# ══════════════════════════════════════════════════════════════════════
def _token(request: Request, authorization: Any = "") -> str:
    """The bearer token, or the cookie. Callable both as a FastAPI dependency
    and as a plain helper, hence the defensive type check: called directly, the
    unresolved `Header(...)` default would arrive instead of a string."""
    if isinstance(authorization, str) and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    # Pas de repli sur un cookie : le front envoie un Bearer depuis
    # `sessionStorage` et rien dans ce projet n'a jamais appelé `set_cookie`.
    # Le repli `request.cookies.get("fx_session")` qui vivait ici acceptait
    # donc une authentification par cookie que personne n'émettait — un
    # canal d'entrée sans protection CSRF, pour zéro usage.
    return ""


def current_user(request: Request, authorization: str = Header(default=""),
                 s: Session = Depends(get_session)) -> Optional[User]:
    return auth.user_for_token(s, _token(request, authorization))


def require_user(user: Optional[User] = Depends(current_user),
                 s: Session = Depends(get_session)) -> User:
    """
    Demand a signed-in caller — unless nobody has ever signed up.

    An install that shipped locked behind a default account would be worse than
    open: default credentials outlive the deployment that set them. So the app
    stays open exactly until the first account exists, and closes by itself the
    moment it does.
    """
    if user is None:
        if auth.no_users_yet(s):
            return User(id="", email="setup@local", display_name="Setup",
                        is_superadmin=True, active=True)
        raise HTTPException(401, "Sign in to continue.")
    return user


def current_env(env: str = "", user: User = Depends(require_user),
                s: Session = Depends(get_session)) -> str:
    """
    The environment this request may touch.

    The parameter only *proposes*; membership decides. A caller asking for an
    environment they do not belong to is refused rather than silently served the
    default one — a silent fallback would hide the mistake and, worse, hide an
    attempt.
    """
    if not user.id:                       # setup mode, no accounts yet
        return env or repo.DEFAULT_ENV
    wanted = env or repo.DEFAULT_ENV
    if wanted == "*":
        if not user.is_superadmin:
            raise HTTPException(403, "Only a superadmin may read across environments.")
        return "*"
    if auth.role_in(s, user, wanted) is None:
        raise HTTPException(403, f"You are not a member of '{wanted}'.")
    return wanted


def require_capability(capability: str):
    """
    Guard a route on a named capability rather than on a role.

    The route then states *what it does* — `config.write`, `file.process` — and
    the policy that maps it to a role lives in one reviewable table. Changing who
    may edit configurations becomes a one-line edit there, not a hunt through
    every endpoint.
    """
    def _dep(env: str = "", user: User = Depends(require_user),
             s: Session = Depends(get_session)) -> User:
        if not user.id:                       # setup mode: no accounts yet
            return user
        scope = env or repo.DEFAULT_ENV
        role = auth.role_in(s, user, scope)
        if role is None:
            raise HTTPException(403, f"You are not a member of '{scope}'.")
        if not perms.can(role, capability):
            spec = perms.CAPABILITIES.get(capability, {})
            raise HTTPException(
                403, f"« {spec.get('label', capability)} » demande le rôle "
                     f"'{spec.get('min', '?')}' dans '{scope}' (vous êtes '{role}').")
        return user
    return _dep


# ══════════════════════════════════════════════════════════════════════
# Sign in / sign out
# ══════════════════════════════════════════════════════════════════════
class LoginIn(BaseModel):
    email: str
    password: str


class SignupIn(BaseModel):
    email: str
    password: str
    display_name: str = ""


def _me(s: Session, user: User, borrowed_from: str = "") -> dict:
    from app.services import permissions as _perms
    envs = auth.memberships_of(s, user.id) if user.id else {}
    return {"id": user.id, "email": user.email, "display_name": user.display_name,
            "is_superadmin": user.is_superadmin,
            "environments": envs,
            # What this person may do, per environment: the UI asks rather than
            # guessing from a role name.
            "capabilities": {e: _perms.capabilities_for(r) for e, r in envs.items()},
            "setup_mode": not user.id,
            "impersonated_by": borrowed_from}


def _borrowed_from(s: Session, token: str) -> str:
    """The email of whoever is borrowing this identity, if anyone."""
    import hashlib
    if not token:
        return ""
    row = s.get(AuthSession, hashlib.sha256(token.encode()).hexdigest())
    if row is None or not row.impersonated_by:
        return ""
    boss = s.get(User, row.impersonated_by)
    return boss.email if boss else "admin"


@router.get("/state")
def auth_state(request: Request, user: Optional[User] = Depends(current_user),
               s: Session = Depends(get_session)):
    """What the UI needs before drawing anything: is anyone signed in, is this a
    fresh install, and which SSO buttons to offer."""
    providers = [{"name": p.name, "kind": p.kind}
                 for p in s.scalars(select(AuthProvider).where(AuthProvider.enabled.is_(True)))]
    return {"authenticated": user is not None,
            "setup_needed": auth.no_users_yet(s),
            "providers": providers,
            "user": _me(s, user, _borrowed_from(s, _token(request))) if user else None}


@router.post("/signup")
def signup(req: SignupIn, request: Request, s: Session = Depends(get_session)):
    """Create an account. Open only while no user exists — the first one becomes
    superadmin — and reserved to a superadmin afterwards."""
    first = auth.no_users_yet(s)
    if not first:
        caller = auth.user_for_token(s, _token(request))
        if caller is None or not caller.is_superadmin:
            raise HTTPException(403, "Only a superadmin may create accounts.")
    try:
        u = auth.create_user(s, email=req.email, display_name=req.display_name,
                             password=req.password, superadmin=first)
    except auth.AuthError as e:
        raise HTTPException(422, str(e))
    commit(s)
    return {"id": u.id, "email": u.email, "is_superadmin": u.is_superadmin}


@router.post("/login")
def login(req: LoginIn, request: Request, s: Session = Depends(get_session)):
    u = auth.find_user(s, req.email)
    # One message for both cases on purpose: distinguishing them tells an
    # attacker which addresses exist.
    if u is None or not u.active or not auth.verify_password(req.password, u.password_hash):
        raise HTTPException(401, "Incorrect email or password.")
    sess = auth.open_session(s, u, request.headers.get("user-agent", ""))
    commit(s)
    return {"token": sess.plain_token, "user": _me(s, u)}


@router.post("/logout")
def logout(request: Request, s: Session = Depends(get_session)):
    auth.close_session(s, _token(request))
    commit(s)
    return {"ok": True}


@router.get("/capabilities")
def my_capabilities(env: str = "", user: User = Depends(require_user),
                    s: Session = Depends(get_session)):
    """
    What this person may do, here.

    Returned rather than inferred client-side from a role name: the policy has
    one home, and the interface greys out what would be refused instead of
    offering an action that fails.
    """
    scope = env or repo.DEFAULT_ENV
    role = "admin" if not user.id else auth.role_in(s, user, scope)
    return {"environment": scope, "role": role,
            "capabilities": perms.capabilities_for(role),
            "policy": perms.describe()}


@router.get("/me")
def me(user: User = Depends(require_user), s: Session = Depends(get_session)):
    return _me(s, user)


# ══════════════════════════════════════════════════════════════════════
# SSO
# ══════════════════════════════════════════════════════════════════════
class ProviderIn(BaseModel):
    name: str
    kind: str = "oidc"
    enabled: bool = True
    client_id: str = ""
    client_secret: str = ""
    discovery_url: str = ""
    authorize_url: str = ""
    token_url: str = ""
    jwks_url: str = ""
    issuer: str = ""
    scopes: str = "openid email profile"
    groups_claim: str = "groups"
    claim_mappings: List[Dict[str, str]] = Field(default_factory=list)
    auto_provision: bool = True


def _provider_out(p: AuthProvider) -> dict:
    return {"id": p.id, "name": p.name, "kind": p.kind, "enabled": p.enabled,
            "client_id": p.client_id, "has_secret": bool(p.client_secret),
            "discovery_url": p.discovery_url, "authorize_url": p.authorize_url,
            "token_url": p.token_url, "jwks_url": p.jwks_url, "issuer": p.issuer,
            "scopes": p.scopes, "groups_claim": p.groups_claim,
            "claim_mappings": p.claim_mappings or [], "auto_provision": p.auto_provision}


@admin_router.get("/providers")
def list_providers(user: User = Depends(require_user), s: Session = Depends(get_session)):
    if user.id and not user.is_superadmin:
        raise HTTPException(403, "Superadmin only.")
    return [_provider_out(p) for p in s.scalars(select(AuthProvider))]


@admin_router.post("/providers")
async def save_provider(req: ProviderIn, user: User = Depends(require_user),
                        s: Session = Depends(get_session)):
    """
    Configure an SSO connection — stored, not read from environment variables,
    so a customer adds their own IdP without a redeploy.
    """
    if user.id and not user.is_superadmin:
        raise HTTPException(403, "Superadmin only.")
    p = s.scalar(select(AuthProvider).where(AuthProvider.name == req.name))
    if p is None:
        p = AuthProvider(name=req.name)
        s.add(p)
    for f in ("kind", "enabled", "client_id", "discovery_url", "authorize_url",
              "token_url", "jwks_url", "issuer", "scopes", "groups_claim", "auto_provision"):
        setattr(p, f, getattr(req, f))
    if req.client_secret:                 # an empty field keeps the stored secret
        p.client_secret = req.client_secret
    p.claim_mappings = list(req.claim_mappings)

    # Discovery fills the endpoints so the operator pastes one URL, not four.
    if req.discovery_url and not (req.authorize_url and req.token_url):
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                doc = (await c.get(req.discovery_url)).json()
            p.authorize_url = doc.get("authorization_endpoint", p.authorize_url)
            p.token_url = doc.get("token_endpoint", p.token_url)
            p.jwks_url = doc.get("jwks_uri", p.jwks_url)
            p.issuer = doc.get("issuer", p.issuer)
        except Exception as e:  # noqa: BLE001 — configuring must not 500
            raise HTTPException(422, f"Could not read the discovery document: {e}")
    commit(s)
    return _provider_out(p)


@router.get("/sso/{name}/start")
def sso_start(name: str, redirect_uri: str = "", s: Session = Depends(get_session)):
    """The URL to send the browser to. Returned rather than redirected so the
    caller keeps control of the navigation."""
    p = s.scalar(select(AuthProvider).where(AuthProvider.name == name,
                                            AuthProvider.enabled.is_(True)))
    if p is None:
        raise HTTPException(404, f"No enabled provider named '{name}'.")
    if not p.authorize_url or not p.client_id:
        raise HTTPException(422, f"Provider '{name}' is not fully configured.")
    import secrets as _s
    import urllib.parse as _u
    state = _s.token_urlsafe(16)
    q = _u.urlencode({"response_type": "code", "client_id": p.client_id,
                      "redirect_uri": redirect_uri, "scope": p.scopes, "state": state})
    return {"url": f"{p.authorize_url}?{q}", "state": state}


class SsoCallbackIn(BaseModel):
    code: str
    redirect_uri: str = ""
    # Test/offline path: claims supplied directly instead of exchanging a code.
    claims: Optional[Dict[str, Any]] = None


@router.post("/sso/{name}/callback")
async def sso_callback(name: str, req: SsoCallbackIn, request: Request,
                       s: Session = Depends(get_session)):
    p = s.scalar(select(AuthProvider).where(AuthProvider.name == name,
                                            AuthProvider.enabled.is_(True)))
    if p is None:
        raise HTTPException(404, f"No enabled provider named '{name}'.")

    if req.claims is not None:
        # Only usable while the install has no accounts: otherwise anyone could
        # mint an identity by posting the claims they wish they had.
        if not auth.no_users_yet(s):
            raise HTTPException(403, "Direct claims are only accepted during setup.")
        claims = req.claims
    else:
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post(p.token_url, data={
                    "grant_type": "authorization_code", "code": req.code,
                    "redirect_uri": req.redirect_uri, "client_id": p.client_id,
                    "client_secret": p.client_secret})
            if r.status_code >= 400:
                raise HTTPException(401, f"The identity provider refused the code ({r.status_code}).")
            payload = r.json()
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"Could not reach the identity provider: {e}")
        try:
            claims = auth.decode_id_token(payload.get("id_token", ""), p)
        except auth.AuthError as e:
            raise HTTPException(401, str(e))

    try:
        user = auth.user_from_claims(s, p, claims)
    except auth.AuthError as e:
        raise HTTPException(403, str(e))
    sess = auth.open_session(s, user, request.headers.get("user-agent", ""))
    commit(s)
    return {"token": sess.plain_token, "user": _me(s, user)}


# ══════════════════════════════════════════════════════════════════════
# Delegated administration
# ══════════════════════════════════════════════════════════════════════
class MemberIn(BaseModel):
    email: str
    role: str = "viewer"


@admin_router.get("/environments/{env}/members")
def list_members(env: str, user: User = Depends(require_user),
                 s: Session = Depends(get_session)):
    if user.id and not perms.can(auth.role_in(s, user, env), "members.manage"):
        raise HTTPException(403, f"You do not administer '{env}'.")
    rows = s.scalars(select(Membership).where(Membership.environment == env))
    out = []
    for m in rows:
        u = s.get(User, m.user_id)
        out.append({"user_id": m.user_id, "email": u.email if u else "",
                    "display_name": u.display_name if u else "",
                    "role": m.role, "from_sso": m.from_sso})
    return out


@admin_router.post("/environments/{env}/members")
def set_member(env: str, req: MemberIn, user: User = Depends(require_user),
               s: Session = Depends(get_session)):
    """
    Grant a role inside one environment.

    Delegated on purpose: an environment's own admin manages its members without
    being able to touch another environment. Otherwise every access request
    lands on whoever holds the superadmin account, and that person becomes the
    bottleneck by the third team onboarded.
    """
    if user.id and not perms.can(auth.role_in(s, user, env), "members.manage"):
        raise HTTPException(403, f"You do not administer '{env}'.")
    target = auth.find_user(s, req.email)
    if target is None:
        raise HTTPException(404, f"No account for {req.email}.")
    existing = s.scalar(select(Membership).where(Membership.user_id == target.id,
                                                 Membership.environment == env))
    if existing is not None and existing.from_sso:
        raise HTTPException(
            409, "This access comes from the identity provider. Change it in the "
                 "directory — the next sign-in would undo an edit made here.")
    try:
        m = auth.set_membership(s, target.id, env, req.role)
    except auth.AuthError as e:
        raise HTTPException(422, str(e))
    commit(s)
    return {"user_id": m.user_id, "environment": env, "role": m.role}


@admin_router.delete("/environments/{env}/members/{user_id}")
def remove_member(env: str, user_id: str, user: User = Depends(require_user),
                  s: Session = Depends(get_session)):
    if user.id and not perms.can(auth.role_in(s, user, env), "members.manage"):
        raise HTTPException(403, f"You do not administer '{env}'.")
    m = s.scalar(select(Membership).where(Membership.user_id == user_id,
                                          Membership.environment == env))
    if m is not None:
        s.delete(m)
        commit(s)
    return {"removed": user_id}


@admin_router.get("/users")
def list_users(user: User = Depends(require_user), s: Session = Depends(get_session)):
    if user.id and not user.is_superadmin:
        raise HTTPException(403, "Superadmin only.")
    return [{"id": u.id, "email": u.email, "display_name": u.display_name,
             "is_superadmin": u.is_superadmin, "active": u.active,
             "environments": auth.memberships_of(s, u.id),
             "sso": [i.provider for i in u.identities]}
            for u in s.scalars(select(User))]


class SetPasswordIn(BaseModel):
    password: str


@admin_router.post("/users/{user_id}/password")
def set_user_password(user_id: str, req: SetPasswordIn,
                      user: User = Depends(require_user), s: Session = Depends(get_session)):
    """
    Reset someone's password — the account keeps its identity and role
    memberships, only the credential changes. Superadmin only: this bypasses
    the person entirely, so it must not be something an environment admin
    can do to a colleague.
    """
    if user.id and not user.is_superadmin:
        raise HTTPException(403, "Superadmin only.")
    target = s.get(User, user_id)
    if target is None:
        raise HTTPException(404, "Compte introuvable.")
    try:
        target.password_hash = auth.hash_password(req.password)
    except auth.AuthError as e:
        raise HTTPException(422, str(e))
    commit(s)
    return {"id": target.id, "email": target.email}


def _is_last_active_superadmin(s: Session, target: User) -> bool:
    """Losing every superadmin at once means nobody left who can create,
    deactivate, or delete an account — the install locks itself out."""
    if not (target.is_superadmin and target.active):
        return False
    others = s.scalars(select(User).where(User.is_superadmin == True,  # noqa: E712
                                          User.active == True, User.id != target.id))  # noqa: E712
    return next(iter(others), None) is None


@admin_router.post("/users/{user_id}/deactivate")
def deactivate_user(user_id: str, user: User = Depends(require_user),
                    s: Session = Depends(get_session)):
    """
    Block an account from signing in without erasing anything — memberships,
    grants and the audit trail (RevealEvent, who-granted-what) stay exactly
    as they are. The reversible default: prefer this over deleting outright.
    """
    if user.id and not user.is_superadmin:
        raise HTTPException(403, "Superadmin only.")
    target = s.get(User, user_id)
    if target is None:
        raise HTTPException(404, "Compte introuvable.")
    if target.id == user.id:
        raise HTTPException(422, "Vous ne pouvez pas désactiver votre propre compte.")
    if _is_last_active_superadmin(s, target):
        raise HTTPException(409, "C'est le dernier administrateur général actif — "
                                 "désignez-en un autre avant de désactiver celui-ci.")
    target.active = False
    s.execute(delete(AuthSession).where(AuthSession.user_id == target.id))
    commit(s)
    return {"id": target.id, "email": target.email, "active": target.active}


@admin_router.post("/users/{user_id}/reactivate")
def reactivate_user(user_id: str, user: User = Depends(require_user),
                    s: Session = Depends(get_session)):
    if user.id and not user.is_superadmin:
        raise HTTPException(403, "Superadmin only.")
    target = s.get(User, user_id)
    if target is None:
        raise HTTPException(404, "Compte introuvable.")
    target.active = True
    commit(s)
    return {"id": target.id, "email": target.email, "active": target.active}


class DeleteUserIn(BaseModel):
    confirm_email: str = ""


@admin_router.delete("/users/{user_id}")
def delete_user(user_id: str, req: DeleteUserIn,
               user: User = Depends(require_user), s: Session = Depends(get_session)):
    """
    Erase an account for good — memberships and SSO identities cascade with
    it (they describe the account, not history). What stays, deliberately:
    RevealEvent and every `granted_by`/`created_by` trail, because losing who
    did something the moment they leave would be worse than a name nobody
    holds any more — the same reasoning as deleting an environment.

    Typed confirmation because there is no undo: unlike deactivating, this
    cannot be walked back by flipping a flag.
    """
    from app.db_models import DatasetGrant, KeyHolder

    if user.id and not user.is_superadmin:
        raise HTTPException(403, "Superadmin only.")
    target = s.get(User, user_id)
    if target is None:
        raise HTTPException(404, "Compte introuvable.")
    if target.id == user.id:
        raise HTTPException(422, "Vous ne pouvez pas supprimer votre propre compte.")
    if _is_last_active_superadmin(s, target):
        raise HTTPException(409, "C'est le dernier administrateur général actif — "
                                 "désignez-en un autre avant de supprimer celui-ci.")
    if req.confirm_email != target.email:
        raise HTTPException(422, "Tapez l'adresse e-mail du compte pour confirmer la suppression.")

    s.execute(delete(AuthSession).where(AuthSession.user_id == target.id))
    s.execute(delete(KeyHolder).where(KeyHolder.user_id == target.id))
    s.execute(delete(DatasetGrant).where(DatasetGrant.subject_kind == "user",
                                         DatasetGrant.subject == target.id))
    email = target.email
    s.delete(target)   # cascades memberships + SSO identities via the ORM relationship
    commit(s)
    return {"deleted": email}


# ══════════════════════════════════════════════════════════════════════
# Sandbox: borrowing an identity to see what it sees
# ══════════════════════════════════════════════════════════════════════
class ImpersonateIn(BaseModel):
    email: str


@admin_router.post("/impersonate")
def impersonate(req: ImpersonateIn, request: Request,
                user: User = Depends(require_user), s: Session = Depends(get_session)):
    """
    Open a session *as* someone else, to check what a role actually shows.

    Designing roles blind is how a team ends up with an operator who cannot do
    their job, or a viewer who can. Trying it is the only reliable check — so
    this exists, deliberately, and with three limits:

      * **superadmin only**, and never onto another superadmin: borrowing an
        identity must not be a route to more power than one already has;
      * **recorded on the session**, so every request knows it is acting on
        someone's behalf and the interface cannot quietly forget to say so;
      * **short-lived** (one hour), because a forgotten borrowed session is
        indistinguishable from an account takeover in a log.
    """
    if getattr(user, "id", "") and not user.is_superadmin:
        raise HTTPException(403, "Seul un administrateur général peut emprunter une identité.")
    target = auth.find_user(s, req.email)
    if target is None:
        raise HTTPException(404, f"Aucun compte pour {req.email}.")
    if target.is_superadmin:
        raise HTTPException(409, "On n'emprunte pas l'identité d'un administrateur général.")
    if target.id == getattr(user, "id", ""):
        raise HTTPException(422, "C'est déjà vous.")

    sess = auth.open_session(s, target, request.headers.get("user-agent", ""))
    sess.impersonated_by = getattr(user, "id", "") or "setup"
    from datetime import datetime, timedelta, timezone
    sess.expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    commit(s)
    return {"token": sess.plain_token, "user": _me(s, target),
            "impersonating": True, "as_email": target.email,
            "by_email": getattr(user, "email", "")}


class QuickUserIn(BaseModel):
    """Everything needed to conjure a test account in one call.

    `password` omis fait tirer un mot de passe aléatoire, renvoyé dans la
    réponse. Le défaut constant qui vivait ici (`"motdepasse1"`) fabriquait
    exactement ce que ce projet refuse ailleurs : un compte réel, avec ses
    rôles réels, protégé par un secret connu d'avance. Un compte de test
    créé pour vérifier un rôle et oublié ensuite est un compte ordinaire du
    point de vue de la table `users` — « les mots de passe par défaut
    survivent au déploiement qui les a posés », comme le dit le README à
    propos de l'amorçage.
    """
    email: str
    password: Optional[str] = None
    display_name: str = ""
    memberships: Dict[str, str] = Field(default_factory=dict)   # environment -> role


@admin_router.post("/quick-user")
def quick_user(req: QuickUserIn, user: User = Depends(require_user),
               s: Session = Depends(get_session)):
    """
    Create an account *and* its roles in one call — the sandbox shortcut.

    Building a test user normally takes three round trips (create, then a
    membership per environment). For trying out a role design that friction is
    the difference between checking and guessing, so it is collapsed here.
    Existing accounts are updated rather than refused: iterating on a design
    means changing the same person's role repeatedly.
    """
    if getattr(user, "id", "") and not user.is_superadmin:
        raise HTTPException(403, "Superadmin only.")
    password = req.password or secrets.token_urlsafe(12)
    existing = auth.find_user(s, req.email)
    created = existing is None
    if existing is None:
        try:
            existing = auth.create_user(s, email=req.email,
                                        display_name=req.display_name,
                                        password=password)
        except auth.AuthError as e:
            raise HTTPException(422, str(e))
    for env, role in (req.memberships or {}).items():
        try:
            auth.set_membership(s, existing.id, env, role)
        except auth.AuthError as e:
            raise HTTPException(422, str(e))
    commit(s)
    return {"id": existing.id, "email": existing.email,
            "environments": auth.memberships_of(s, existing.id),
            # Le mot de passe n'est lisible qu'ici, à la création : il n'est
            # stocké que haché, donc un compte préexistant n'a rien à renvoyer.
            "password": password if created else ""}


@admin_router.get("/overview")
def overview(s: Session = Depends(get_session), user: User = Depends(require_user)):
    """
    Everything, at a glance, per environment.

    An administration console made only of separate screens forces its user to
    hold the picture in their head — how many people are in RH, whether ADV has
    a pinned configuration, which environment nobody has joined yet. This
    assembles that picture once, so the tabs become places you go to *change*
    something rather than places you go to find out.
    """
    from sqlalchemy import func

    from app.db_models import (Artefact, AuthProvider, CryptoKey, Dataset,
                               EnvironmentProfile, FlowRun, Membership)
    from app.env_routes import ALL_MODULES
    from app.services import permissions as perms

    if getattr(user, "id", "") and not user.is_superadmin:
        raise HTTPException(403, "Superadmin only.")

    # Every environment that shows up anywhere: named by a profile, owning an
    # artefact, holding a table, or merely having a member. An environment
    # nobody has joined is exactly the one worth seeing in a console.
    names: set[str] = {repo.DEFAULT_ENV}
    names |= {p.name for p in s.scalars(select(EnvironmentProfile))}
    names |= {e for (e,) in s.execute(select(Artefact.environment).distinct()) if e}
    names |= {e for (e,) in s.execute(select(Dataset.environment).distinct()) if e}
    names |= {e for (e,) in s.execute(select(Membership.environment).distinct()) if e}

    def count(model, **where):
        q = select(func.count()).select_from(model)
        for k, v in where.items():
            q = q.where(getattr(model, k) == v)
        return int(s.scalar(q) or 0)

    envs = []
    for name in sorted(names):
        prof = s.get(EnvironmentProfile, name)
        modules = list(prof.modules_json or []) if prof else []
        members = [
            {"email": (u.email if (u := s.get(User, m.user_id)) else ""),
             "role": m.role, "from_sso": m.from_sso,
             "last_login_at": (u.last_login_at.isoformat()
                               if u and u.last_login_at else "")}
            for m in s.scalars(select(Membership).where(Membership.environment == name))
        ]
        kinds = {k: count(Artefact, environment=name, kind=k, archived=False)
                 for k in ("config", "tco", "mapping", "graph", "function",
                           "edi_model", "computed")}
        envs.append({
            "name": name,
            "label": (prof.label if prof and prof.label else name),
            "has_profile": prof is not None,
            # An empty module list means "everything": say so plainly rather
            # than showing zero, which would read as "nothing available".
            "modules": modules or list(ALL_MODULES),
            "modules_restricted": bool(modules) and len(modules) < len(ALL_MODULES),
            "config_locked": bool(prof and prof.config_locked),
            "tco_editable": (prof.tco_editable if prof else True),
            "actions": len((prof.actions_json or []) if prof else []),
            "members": members,
            "roles": {r: sum(1 for m in members if m["role"] == r)
                      for r in ("admin", "editor", "operator", "viewer")},
            "artefacts": kinds,
            "tables": count(Dataset, environment=name, archived=False),
            "keys": count(CryptoKey, environment=name, active=True),
            "runs_error": count(FlowRun, environment=name, status="error"),
            # A deep link: the console hands out addresses rather than
            # instructions for reaching a screen.
            "url": f"/?env={name}",
        })

    users = []
    for u in s.scalars(select(User)):
        users.append({"id": u.id, "email": u.email, "display_name": u.display_name,
                      "is_superadmin": u.is_superadmin, "active": u.active,
                      "environments": auth.memberships_of(s, u.id),
                      "sso": [i.provider for i in u.identities],
                      "last_login_at": (u.last_login_at.isoformat()
                                        if u.last_login_at else "")})

    return {
        "environments": envs,
        "users": users,
        "modules": list(ALL_MODULES),
        "roles": list(auth.ROLES),
        # The policy itself, so the console can show what a role actually allows
        # instead of leaving its name to be interpreted.
        "policy": {r: perms.capabilities_for(r) for r in auth.ROLES},
        "capabilities": perms.describe(),
        "providers": [{"name": p.name, "enabled": p.enabled,
                       "mappings": len(p.claim_mappings or [])}
                      for p in s.scalars(select(AuthProvider))],
        "totals": {"environments": len(envs), "users": len(users),
                   "tables": count(Dataset, archived=False),
                   "artefacts": count(Artefact, archived=False)},
    }
