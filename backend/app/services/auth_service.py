"""
Authentication — one identity, several ways to prove it.

The shape this module defends: a `User` is the account, a password and an SSO
identity are both *proofs* attached to it, and `Membership` grants roles per
environment regardless of how the person signed in. Internal accounts and SSO
are therefore not two systems that must be kept in agreement; they are two doors
into the same room.

Three things are deliberate:

  * **Opaque server-side sessions.** The token carries no claims — the server
    holds what it means. Revocation is then a delete rather than a wait for
    expiry, which matters when the session is what proves which environments a
    request may touch.

  * **SSO group mappings.** A company keeps managing access in its own directory:
    a group in the IdP maps to (environment, role) here. Memberships created that
    way are marked `from_sso` and refreshed at every login, so removing someone
    from a group actually removes their access.

  * **No bootstrap hole.** While no user exists the app stays open, and the first
    account created becomes superadmin. An install that shipped locked with a
    default password would be worse: default passwords survive far longer than
    the deployment that set them.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import time

import bcrypt
import httpx
import jwt as _pyjwt
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db_models import (
    AuthProvider, AuthSession, Membership, User, UserIdentity,
)
from app.services.permissions import ROLE_RANK

SESSION_DAYS = 8
ROLES = ("admin", "editor", "operator", "viewer")


class AuthError(Exception):
    pass


# ── passwords ─────────────────────────────────────────────────────────
def hash_password(plain: str) -> str:
    if len(plain or "") < 8:
        raise AuthError("A password needs at least 8 characters.")
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: Optional[str]) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw((plain or "").encode(), hashed.encode())
    except ValueError:
        return False


# ── bootstrap ─────────────────────────────────────────────────────────
def no_users_yet(s: Session) -> bool:
    """While this is true the app stays open and the next account created is
    superadmin. It is the only moment an unauthenticated caller may write."""
    return (s.scalar(select(func.count()).select_from(User)) or 0) == 0


# ── users, identities, memberships ────────────────────────────────────
def create_user(s: Session, *, email: str, display_name: str = "",
                password: Optional[str] = None, superadmin: bool = False) -> User:
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        raise AuthError("A valid email is required.")
    if s.scalar(select(User).where(User.email == email)) is not None:
        raise AuthError(f"An account already exists for {email}.")
    u = User(email=email, display_name=display_name or email.split("@")[0],
             password_hash=hash_password(password) if password else None,
             is_superadmin=superadmin)
    s.add(u)
    s.flush()
    return u


def find_user(s: Session, email: str) -> Optional[User]:
    return s.scalar(select(User).where(User.email == (email or "").strip().lower()))


def set_membership(s: Session, user_id: str, environment: str, role: str,
                   from_sso: bool = False) -> Membership:
    if role not in ROLES:
        raise AuthError(f"Unknown role '{role}' (use {', '.join(ROLES)}).")
    m = s.scalar(select(Membership).where(Membership.user_id == user_id,
                                          Membership.environment == environment))
    if m is None:
        m = Membership(user_id=user_id, environment=environment, role=role,
                       from_sso=from_sso)
        s.add(m)
    else:
        m.role = role
        m.from_sso = from_sso
    s.flush()
    return m


def memberships_of(s: Session, user_id: str) -> dict:
    return {m.environment: m.role
            for m in s.scalars(select(Membership).where(Membership.user_id == user_id))}


def role_in(s: Session, user: User, environment: str) -> Optional[str]:
    """A superadmin is admin everywhere — otherwise creating an environment
    would lock its creator out of it."""
    if user.is_superadmin:
        return "admin"
    m = s.scalar(select(Membership).where(Membership.user_id == user.id,
                                          Membership.environment == environment))
    return m.role if m else None


# ── sessions ──────────────────────────────────────────────────────────
def open_session(s: Session, user: User, user_agent: str = "") -> AuthSession:
    token = secrets.token_urlsafe(32)
    # Only the hash is stored: a dump of the table cannot be replayed as a
    # login, the same reasoning as for passwords.
    sess = AuthSession(id=hashlib.sha256(token.encode()).hexdigest(), user_id=user.id,
                       expires_at=datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS),
                       user_agent=(user_agent or "")[:300])
    s.add(sess)
    user.last_login_at = datetime.now(timezone.utc)
    s.flush()
    sess.plain_token = token          # returned once, never stored
    return sess


def user_for_token(s: Session, token: str) -> Optional[User]:
    if not token:
        return None
    row = s.get(AuthSession, hashlib.sha256(token.encode()).hexdigest())
    if row is None:
        return None
    expires = row.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        s.delete(row)
        return None
    u = s.get(User, row.user_id)
    return u if (u and u.active) else None


def close_session(s: Session, token: str) -> None:
    row = s.get(AuthSession, hashlib.sha256((token or "").encode()).hexdigest())
    if row is not None:
        s.delete(row)


# ── SSO ───────────────────────────────────────────────────────────────
def _b64url(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


_JWKS_TTL_SECONDS = 300           # short enough to pick up a rotated key, long enough not to hammer the IdP
_jwks_cache: dict[str, tuple[float, list[dict]]] = {}
_ALLOWED_ALGS = ("RS256", "RS384", "RS512", "ES256", "ES384", "ES512")


def _fetch_jwks(jwks_url: str, *, force: bool = False) -> list[dict]:
    now = time.time()
    cached = _jwks_cache.get(jwks_url)
    if not force and cached and now - cached[0] < _JWKS_TTL_SECONDS:
        return cached[1]
    resp = httpx.get(jwks_url, timeout=10)
    resp.raise_for_status()
    keys = resp.json().get("keys", [])
    _jwks_cache[jwks_url] = (now, keys)
    return keys


def _find_jwk(jwks_url: str, kid: Optional[str]) -> dict:
    keys = _fetch_jwks(jwks_url)
    match = next((k for k in keys if not kid or k.get("kid") == kid), None)
    if match is None:
        # The key we want may simply have rotated in since our last fetch —
        # refresh once before concluding it truly is not published.
        keys = _fetch_jwks(jwks_url, force=True)
        match = next((k for k in keys if not kid or k.get("kid") == kid), None)
    if match is None:
        raise AuthError("The id_token's signing key is not among the provider's published keys.")
    return match


def decode_id_token(id_token: str, provider: Optional[AuthProvider] = None) -> dict:
    """
    Read the claims of an OIDC id_token — verified when the provider has a
    JWKS on file, since that is what makes a claim trustworthy rather than
    merely well-formed.

    Without a `provider` (or one with no `jwks_url` captured — never run
    discovery, or a discovery document missing `jwks_uri`), this falls back
    to reading the payload unverified. That degraded path is only ever
    reached after the token came straight from the token endpoint over TLS
    in the caller, and exists for providers configured before this
    verification existed — a fresh provider should always have a jwks_url.
    """
    parts = (id_token or "").split(".")
    if len(parts) != 3:
        raise AuthError("Malformed id_token.")

    if provider is None or not provider.jwks_url:
        try:
            return json.loads(_b64url(parts[1]))
        except Exception as exc:  # noqa: BLE001
            raise AuthError(f"Unreadable id_token: {exc}")

    try:
        header = _pyjwt.get_unverified_header(id_token)
    except Exception as exc:  # noqa: BLE001
        raise AuthError(f"Malformed id_token: {exc}")
    alg = header.get("alg", "")
    if alg not in _ALLOWED_ALGS:
        raise AuthError(f"Unsupported or missing id_token signing algorithm '{alg}'.")

    try:
        jwk = _find_jwk(provider.jwks_url, header.get("kid"))
        public_key = _pyjwt.PyJWK.from_dict(jwk).key
        return _pyjwt.decode(
            id_token, key=public_key, algorithms=[alg],
            audience=provider.client_id or None, issuer=provider.issuer or None,
            options={"require": ["exp", "iat"]})
    except AuthError:
        raise
    except _pyjwt.PyJWTError as exc:
        raise AuthError(f"id_token failed verification: {exc}")
    except Exception as exc:  # noqa: BLE001 — a JWKS fetch failure must refuse, not crash
        raise AuthError(f"Could not verify the id_token: {exc}")


def apply_claim_mappings(s: Session, user: User, provider: AuthProvider,
                         claims: dict) -> dict:
    """
    Turn the IdP's groups into memberships here.

    This is what "let the company manage it themselves" means concretely: access
    is granted in their directory, and every login re-applies it. Memberships
    marked `from_sso` that no longer match a claim are removed — otherwise
    removing someone from a group would leave their access behind, which is the
    failure mode that makes SSO integrations dangerous rather than useful.
    """
    groups = claims.get(provider.groups_claim) or []
    if isinstance(groups, str):
        groups = [g.strip() for g in groups.replace(",", " ").split() if g.strip()]
    groups = {str(g) for g in groups}

    wanted: dict[str, str] = {}
    for rule in provider.claim_mappings or []:
        if str(rule.get("group", "")) in groups:
            env = str(rule.get("environment", "")).strip()
            role = str(rule.get("role", "viewer"))
            if env and role in ROLES:
                # A person in two groups granting different roles on the same
                # environment keeps the stronger one.
                if env not in wanted or ROLE_RANK[role] > ROLE_RANK[wanted[env]]:
                    wanted[env] = role

    for env, role in wanted.items():
        set_membership(s, user.id, env, role, from_sso=True)

    stale = [m for m in s.scalars(select(Membership).where(
        Membership.user_id == user.id, Membership.from_sso.is_(True)))
        if m.environment not in wanted]
    for m in stale:
        s.delete(m)
    return wanted


def user_from_claims(s: Session, provider: AuthProvider, claims: dict) -> User:
    """Find or create the account behind an SSO sign-in, then refresh its roles."""
    subject = str(claims.get("sub") or "")
    email = str(claims.get("email") or "").strip().lower()
    if not subject:
        raise AuthError("The identity provider returned no subject.")

    ident = s.scalar(select(UserIdentity).where(UserIdentity.provider == provider.name,
                                                UserIdentity.subject == subject))
    user = s.get(User, ident.user_id) if ident else None

    if user is None and email:
        # Same person, already known by email: attach the identity rather than
        # create a duplicate account that would hold none of their roles.
        user = find_user(s, email)

    if user is None:
        if not provider.auto_provision:
            raise AuthError("No account for this identity, and auto-provisioning is off.")
        if not email:
            raise AuthError("The identity provider returned no email.")
        user = create_user(s, email=email,
                           display_name=str(claims.get("name") or ""),
                           superadmin=no_users_yet(s))

    if ident is None:
        s.add(UserIdentity(user_id=user.id, provider=provider.name, subject=subject))
        s.flush()

    apply_claim_mappings(s, user, provider, claims)
    return user
