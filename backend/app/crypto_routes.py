"""
Confidential columns: keys, holders, and the act of looking.

Two decisions shape these routes.

**Holders are the access list**, not another role. Knowing who — by name — can
read salaries is easier to reason about than a permission diluted across a role
hierarchy, and easier to explain to whoever asks. The creator becomes the first
holder because a key nobody can use is a key that only destroys data.

**Revealing is an event, not a mode.** On pay data the question is rarely only
who *may* look, but who *did*. So a reveal is recorded — who, which columns,
which context, how many rows — and returns data for that one call rather than
flipping a switch that stays on. A mode left open stays open.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth_routes import current_env, require_user
from app.db import commit, get_session
from app.db_models import CryptoKey, KeyHolder, RevealEvent, User
from app.services import auth_service as auth
from app.services import crypto_service as crypto
from app.services import permissions as perms

router = APIRouter(prefix="/api/keys", tags=["confidentiality"])


class KeyIn(BaseModel):
    name: str
    label: str = ""
    environment: str = ""


class HolderIn(BaseModel):
    email: str


def _out(k: CryptoKey, s: Session, me: Optional[User] = None) -> dict:
    holders = []
    for h in k.holders:
        u = s.get(User, h.user_id)
        holders.append({"user_id": h.user_id, "email": u.email if u else "",
                        "display_name": u.display_name if u else ""})
    return {"id": k.id, "name": k.name, "label": k.label,
            "environment": k.environment, "active": k.active,
            "holders": holders,
            "i_hold": bool(me and any(h["user_id"] == me.id for h in holders)),
            "created_at": k.created_at.isoformat() if k.created_at else "",
            "revoked_at": k.revoked_at.isoformat() if k.revoked_at else ""}


def _key_or_404(s: Session, key_id: str) -> CryptoKey:
    k = s.get(CryptoKey, key_id)
    if k is None:
        raise HTTPException(404, "Key not found.")
    return k


def _is_holder(s: Session, key: CryptoKey, user: User) -> bool:
    """
    Only holders. A superadmin is deliberately *not* granted an implicit pass:
    the point of naming holders is that the list is the truth, and a silent
    bypass would make it a decoration.
    """
    if not getattr(user, "id", ""):
        return True                       # setup mode, no accounts yet
    return s.scalar(select(KeyHolder).where(KeyHolder.key_id == key.id,
                                            KeyHolder.user_id == user.id)) is not None


@router.get("/status")
def status():
    """Whether confidential columns can be used at all. The UI greys the option
    out when there is no master key — offering it would promise protection the
    install cannot deliver."""
    return {"available": crypto.crypto_available(),
            "reason": "" if crypto.crypto_available()
                      else "No FX_MASTER_KEY: keys must be held outside the database."}


@router.get("")
def list_keys(scope: str = Depends(current_env), user: User = Depends(require_user),
              s: Session = Depends(get_session)):
    q = select(CryptoKey)
    if scope != "*":
        q = q.where(CryptoKey.environment == scope)
    return [_out(k, s, user) for k in s.scalars(q)]


@router.post("")
def create_key(req: KeyIn, scope: str = Depends(current_env),
               user: User = Depends(require_user), s: Session = Depends(get_session)):
    """Create a key. Refuses without a master key rather than storing something
    that only looks protected."""
    if getattr(user, "id", "") and not perms.can(auth.role_in(s, user, scope), "keys.create"):
        raise HTTPException(403, f"Creating a key needs the 'admin' role in '{scope}'.")
    name = (req.name or "").strip()
    if not name:
        raise HTTPException(422, "A key needs a name.")
    env = req.environment or scope
    if s.scalar(select(CryptoKey).where(CryptoKey.name == name,
                                        CryptoKey.environment == env)) is not None:
        raise HTTPException(409, f"A key named '{name}' already exists in '{env}'.")
    try:
        wrapped = crypto.new_data_key()
    except crypto.CryptoUnavailable as e:
        raise HTTPException(503, str(e))

    k = CryptoKey(name=name, label=req.label, environment=env, wrapped_key=wrapped,
                  created_by=getattr(user, "id", ""))
    s.add(k)
    s.flush()
    if getattr(user, "id", ""):
        # The creator holds it: a key nobody can use only destroys data.
        s.add(KeyHolder(key_id=k.id, user_id=user.id))
    commit(s)
    return _out(k, s, user)


@router.post("/{key_id}/holders")
def add_holder(key_id: str, req: HolderIn, user: User = Depends(require_user),
               s: Session = Depends(get_session)):
    """
    Add a holder — only a current holder may.

    Deliberate: whoever can read the data decides who else can. Routing this
    through an administrator would let someone grant themselves access to a
    column they were never trusted with.
    """
    k = _key_or_404(s, key_id)
    if not _is_holder(s, k, user):
        raise HTTPException(403, "Only a holder of this key may add another.")
    target = auth.find_user(s, req.email)
    if target is None:
        raise HTTPException(404, f"No account for {req.email}.")
    if s.scalar(select(KeyHolder).where(KeyHolder.key_id == k.id,
                                        KeyHolder.user_id == target.id)) is None:
        s.add(KeyHolder(key_id=k.id, user_id=target.id))
        commit(s)
    return _out(k, s, user)


@router.delete("/{key_id}/holders/{user_id}")
def remove_holder(key_id: str, user_id: str, user: User = Depends(require_user),
                  s: Session = Depends(get_session)):
    k = _key_or_404(s, key_id)
    if not _is_holder(s, k, user):
        raise HTTPException(403, "Only a holder of this key may remove another.")
    if len(k.holders) <= 1:
        # The bus factor, refused rather than discovered later: the last holder
        # leaving makes the data unreadable forever.
        raise HTTPException(409, "A key must keep at least one holder — otherwise "
                                 "the data it protects becomes unreadable.")
    h = s.scalar(select(KeyHolder).where(KeyHolder.key_id == k.id,
                                         KeyHolder.user_id == user_id))
    if h is not None:
        s.delete(h)
        commit(s)
    return _out(k, s, user)


@router.delete("/{key_id}")
def revoke_key(key_id: str, confirm: str = "", user: User = Depends(require_user),
               s: Session = Depends(get_session)):
    """
    Destroy a key, and with it everything it protected.

    This is a feature — it answers an erasure request in one action — which is
    exactly why it demands the key's own name as confirmation. Configurations
    depending on it then refuse to run rather than emitting masked nonsense.
    """
    k = _key_or_404(s, key_id)
    if not _is_holder(s, k, user):
        raise HTTPException(403, "Only a holder may destroy this key.")
    if confirm != k.name:
        raise HTTPException(422, f"Confirm by passing the key name: confirm={k.name}. "
                                 f"Data encrypted with it becomes unreadable forever.")
    k.active = False
    k.wrapped_key = ""            # the material really goes
    k.revoked_at = datetime.now(timezone.utc)
    commit(s)
    return {"revoked": k.name}


class RevealIn(BaseModel):
    key_name: str
    values: List[str] = Field(default_factory=list)
    columns: List[str] = Field(default_factory=list)
    context: str = ""


@router.post("/reveal")
def reveal(req: RevealIn, scope: str = Depends(current_env),
           user: User = Depends(require_user), s: Session = Depends(get_session)):
    """
    Decrypt for this call, and record that it happened.

    Returns values rather than granting a session-wide mode: the trail then says
    what was actually seen, and nothing stays unlocked behind the person who
    opened it.
    """
    k = s.scalar(select(CryptoKey).where(CryptoKey.name == req.key_name,
                                         CryptoKey.environment == scope))
    if k is None:
        raise HTTPException(404, f"No key '{req.key_name}' in '{scope}'.")
    if not k.active or not k.wrapped_key:
        raise HTTPException(409, f"Key '{k.name}' has been destroyed — the data it "
                                 f"protected cannot be read again.")
    if not _is_holder(s, k, user):
        raise HTTPException(403, "You do not hold this key.")

    try:
        clear = [crypto.decrypt_value(v, k.wrapped_key) for v in req.values]
    except crypto.CryptoUnavailable as e:
        raise HTTPException(503, str(e))

    s.add(RevealEvent(user_id=getattr(user, "id", ""),
                      user_email=getattr(user, "email", ""),
                      key_name=k.name, environment=scope,
                      columns=list(req.columns), context=req.context[:200],
                      rows=len(req.values)))
    commit(s)
    return {"values": clear, "rows": len(clear)}


@router.get("/reveals")
def list_reveals(limit: int = 50, scope: str = Depends(current_env),
                 user: User = Depends(require_user), s: Session = Depends(get_session)):
    """Who looked at what. Readable by environment admins — an audit trail only
    its subject can read is not an audit trail."""
    if getattr(user, "id", "") and not perms.can(auth.role_in(s, user, scope), "keys.audit"):
        raise HTTPException(403, f"Reading the reveal trail needs 'admin' in '{scope}'.")
    rows = s.scalars(select(RevealEvent).where(RevealEvent.environment == scope)
                     .order_by(RevealEvent.created_at.desc())
                     .limit(max(1, min(limit, 500))))
    return [{"id": r.id, "user_email": r.user_email, "key_name": r.key_name,
             "columns": r.columns, "context": r.context, "rows": r.rows,
             "at": r.created_at.isoformat() if r.created_at else ""} for r in rows]
