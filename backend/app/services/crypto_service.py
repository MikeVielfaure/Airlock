"""
Confidentiality: marked columns, propagated taint, encrypted storage.

Two mechanisms, and it matters which does what.

**Propagation** is the one that carries the weight. A salary can be encrypted
perfectly and still leak through a computed column `[salaire] * 12`, an average
per person, a report row quoting the offending value, or a generated file. So a
column derived from a sensitive one *becomes* sensitive, automatically. This
works with no cryptography at all and is useful on its own.

**Encryption** exists for a different reason than the usual one. The adversary
here is not a stolen backup: it is a legitimate user of the application who
belongs to the environment but must not read one column. What makes encryption
worth its cost is the number of exits a value has — preview, report, export, TCO
suggestion, replay snapshot, dataset, pivot, generated file, API response. Masking
correctly in all of them, *and in the next one added*, is a discipline that
eventually slips. With encryption, the path someone forgets emits gibberish
rather than a salary: it fails closed instead of failing open.

What this does **not** protect against, plainly: the server decrypts in order to
clean, compute and validate, so it holds the ability to read. Someone who
controls the server, or an application superadmin, is not stopped by this.

The master key lives outside the database (`FX_MASTER_KEY`). Keys stored beside
the data they protect would be theatre. Without it, key creation refuses rather
than silently degrading — failing closed is the whole point.
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
from typing import Dict, Iterable, Optional, Set

from cryptography.fernet import Fernet, InvalidToken

MASK = "•••••"
PREFIX = "enc:v1:"
_COL_RE = re.compile(r"\[([^\[\]]+)\]")


class CryptoUnavailable(Exception):
    """No master key. Raised rather than falling back to plaintext."""


class KeyRevoked(Exception):
    """The key a configuration depends on is gone. Destroying a key destroys the
    data it protected — that is a feature (it answers an erasure request), so the
    configuration must refuse to run rather than silently emit gibberish."""


# ══════════════════════════════════════════════════════════════════════
# Master key & key wrapping
# ══════════════════════════════════════════════════════════════════════
def master_key() -> bytes:
    raw = os.getenv("FX_MASTER_KEY", "").strip()
    if not raw:
        raise CryptoUnavailable(
            "No FX_MASTER_KEY set. Confidential columns need a master key held "
            "outside the database — set it in the environment or a secret store.")
    # Accept a passphrase or a ready-made Fernet key; derive deterministically so
    # the same secret always yields the same key across restarts and replicas.
    if len(raw) == 44 and raw.endswith("="):
        return raw.encode()
    digest = hashlib.sha256(raw.encode()).digest()
    return base64.urlsafe_b64encode(digest)


def crypto_available() -> bool:
    try:
        master_key()
        return True
    except CryptoUnavailable:
        return False


def new_data_key() -> str:
    """A fresh per-key secret, returned wrapped by the master key. The plaintext
    key never leaves this function."""
    return Fernet(master_key()).encrypt(Fernet.generate_key()).decode()


def _unwrap(wrapped: str) -> Fernet:
    try:
        return Fernet(Fernet(master_key()).decrypt(wrapped.encode()))
    except InvalidToken:
        raise CryptoUnavailable(
            "This key cannot be unwrapped with the current FX_MASTER_KEY. "
            "Either the master key changed, or the data belongs to another install.")


# ══════════════════════════════════════════════════════════════════════
# Value-level encryption
# ══════════════════════════════════════════════════════════════════════
def encrypt_value(value, wrapped_key: str) -> str:
    """Encrypt one cell. Empty stays empty: hiding the *absence* of a value costs
    nothing to an attacker and would break every nullability check downstream."""
    text = "" if value is None else str(value)
    if not text:
        return ""
    if is_encrypted(text):
        return text                      # already ciphertext: never double-wrap
    return PREFIX + _unwrap(wrapped_key).encrypt(text.encode()).decode()


def decrypt_value(value, wrapped_key: str) -> str:
    text = "" if value is None else str(value)
    if not text or not is_encrypted(text):
        return text
    try:
        return _unwrap(wrapped_key).decrypt(text[len(PREFIX):].encode()).decode()
    except InvalidToken:
        # The wrong key, or tampered data. Returning the mask rather than raising
        # keeps a report readable instead of turning one bad cell into a 500.
        return MASK


def is_encrypted(value) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def mask_value(value) -> str:
    """What a non-holder sees. Same width regardless of content: a mask whose
    length followed the value would leak the value's length."""
    return MASK if (value is not None and str(value) != "") else ""


# ══════════════════════════════════════════════════════════════════════
# Propagation — the part that carries the weight
# ══════════════════════════════════════════════════════════════════════
def columns_in_expression(expr: str) -> Set[str]:
    """The `[column]` references an expression reads."""
    return {m.group(1).strip() for m in _COL_RE.finditer(expr or "")}


def taint_of_expression(expr: str, sensitivity: Dict[str, str]) -> Optional[str]:
    """
    The key a derived column inherits, or None.

    A column computed from a sensitive one is sensitive — that is the rule that
    stops `[salaire] * 12` from being a laundering mechanism. When an expression
    reads several sensitive columns protected by *different* keys the result is
    attributed to the first, and the caller is expected to surface it: mixing
    keys means the derived value cannot be revealed by holding just one of them.
    """
    for col in columns_in_expression(expr):
        key = sensitivity.get(col)
        if key:
            return key
    return None


def keys_in_expression(expr: str, sensitivity: Dict[str, str]) -> Set[str]:
    return {sensitivity[c] for c in columns_in_expression(expr) if c in sensitivity}


def propagate(sensitivity: Dict[str, str], derived: Dict[str, str]) -> Dict[str, str]:
    """
    Extend a sensitivity map with derived columns.

    `derived` maps a new column name to the expression producing it. Applied in
    declaration order so a chain — b from a, c from b — carries the mark all the
    way down rather than stopping at the first hop.
    """
    out = dict(sensitivity)
    for name, expr in derived.items():
        key = taint_of_expression(expr, out)
        if key:
            out[name] = key
    return out


def propagate_through_columns(sensitivity: Dict[str, str], name: str,
                              sources: Iterable[str]) -> Dict[str, str]:
    """Taint for operations that name their inputs rather than an expression —
    an aggregate over a column, a join carrying one across, a mapping link."""
    out = dict(sensitivity)
    for src in sources:
        if src in out:
            out[name] = out[src]
            break
    return out


# ══════════════════════════════════════════════════════════════════════
# Frame-level helpers
# ══════════════════════════════════════════════════════════════════════
def encrypt_frame(df, sensitivity: Dict[str, str], keys: Dict[str, str]):
    """Encrypt every sensitive column before the frame is stored anywhere."""
    out = df.copy()
    for col, key_name in sensitivity.items():
        if col not in out.columns:
            continue
        wrapped = keys.get(key_name)
        if wrapped is None:
            raise KeyRevoked(f"Key '{key_name}' is unavailable — column '{col}' "
                             f"cannot be written.")
        out[col] = [encrypt_value(v, wrapped) for v in out[col]]
    return out


def render_frame(df, sensitivity: Dict[str, str], keys: Dict[str, str],
                 reveal: Iterable[str] = ()):
    """
    Prepare a frame for display or export.

    Columns whose key is in `reveal` are decrypted; every other sensitive column
    is masked — including one whose key has been destroyed, which shows as a
    mask rather than as ciphertext.
    """
    allowed = set(reveal or ())
    out = df.copy()
    for col, key_name in sensitivity.items():
        if col not in out.columns:
            continue
        if key_name in allowed and key_name in keys:
            wrapped = keys[key_name]
            out[col] = [decrypt_value(v, wrapped) for v in out[col]]
        else:
            out[col] = [mask_value(v) for v in out[col]]
    return out
