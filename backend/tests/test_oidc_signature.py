"""
Verifying an OIDC id_token's signature against the provider's published
JWKS — the gap the README flagged: previously any well-formed JWT was
trusted just because it arrived over the token endpoint's TLS connection.
"""
import time

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.services import auth_service as auth
from app.db_models import AuthProvider


def _keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key, key.public_key()


def _jwk(public_key, kid):
    jwk = pyjwt.algorithms.RSAAlgorithm.to_jwk(public_key, as_dict=True)
    jwk.update({"kid": kid, "use": "sig", "alg": "RS256"})
    return jwk


def _sign(private_key, kid, claims):
    return pyjwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})


def _claims(**over):
    now = int(time.time())
    base = {"sub": "u1", "email": "julie@boite.fr", "iss": "https://idp.example",
            "aud": "client-123", "iat": now, "exp": now + 300}
    base.update(over)
    return base


@pytest.fixture(autouse=True)
def _clear_jwks_cache():
    auth._jwks_cache.clear()
    yield
    auth._jwks_cache.clear()


def _provider(jwks_url="https://idp.example/jwks"):
    return AuthProvider(name="corp", client_id="client-123", issuer="https://idp.example",
                        jwks_url=jwks_url)


def test_a_correctly_signed_token_is_accepted(monkeypatch):
    priv, pub = _keypair()
    jwks = {"keys": [_jwk(pub, "k1")]}
    monkeypatch.setattr(auth, "_fetch_jwks", lambda url, force=False: jwks["keys"])

    token = _sign(priv, "k1", _claims())
    claims = auth.decode_id_token(token, _provider())
    assert claims["email"] == "julie@boite.fr"


def test_a_token_signed_by_the_wrong_key_is_refused(monkeypatch):
    priv, _pub = _keypair()
    _other_priv, other_pub = _keypair()
    jwks = {"keys": [_jwk(other_pub, "k1")]}   # published key does not match the signer
    monkeypatch.setattr(auth, "_fetch_jwks", lambda url, force=False: jwks["keys"])

    token = _sign(priv, "k1", _claims())
    with pytest.raises(auth.AuthError):
        auth.decode_id_token(token, _provider())


def test_an_expired_token_is_refused(monkeypatch):
    priv, pub = _keypair()
    monkeypatch.setattr(auth, "_fetch_jwks", lambda url, force=False: [_jwk(pub, "k1")])

    token = _sign(priv, "k1", _claims(iat=int(time.time()) - 1000, exp=int(time.time()) - 500))
    with pytest.raises(auth.AuthError):
        auth.decode_id_token(token, _provider())


def test_a_token_for_the_wrong_audience_is_refused(monkeypatch):
    priv, pub = _keypair()
    monkeypatch.setattr(auth, "_fetch_jwks", lambda url, force=False: [_jwk(pub, "k1")])

    token = _sign(priv, "k1", _claims(aud="someone-else"))
    with pytest.raises(auth.AuthError):
        auth.decode_id_token(token, _provider())


def test_a_token_from_the_wrong_issuer_is_refused(monkeypatch):
    priv, pub = _keypair()
    monkeypatch.setattr(auth, "_fetch_jwks", lambda url, force=False: [_jwk(pub, "k1")])

    token = _sign(priv, "k1", _claims(iss="https://not-the-real-idp.example"))
    with pytest.raises(auth.AuthError):
        auth.decode_id_token(token, _provider())


def test_an_unknown_kid_refuses_after_retrying_the_fetch_once(monkeypatch):
    priv, _pub = _keypair()
    calls = []

    def fake_fetch(url, force=False):
        calls.append(force)
        return []   # the IdP publishes no matching key, even on retry

    monkeypatch.setattr(auth, "_fetch_jwks", fake_fetch)
    token = _sign(priv, "missing-kid", _claims())
    with pytest.raises(auth.AuthError):
        auth.decode_id_token(token, _provider())
    assert calls == [False, True]   # cached lookup first, then one forced refresh


def test_a_provider_without_a_jwks_url_falls_back_to_unverified_reading():
    """The degraded path for providers configured before verification
    existed — still readable, just not a trust upgrade."""
    priv, _pub = _keypair()
    token = _sign(priv, "whatever", _claims())
    claims = auth.decode_id_token(token, _provider(jwks_url=""))
    assert claims["email"] == "julie@boite.fr"


def test_no_provider_at_all_also_falls_back_to_unverified_reading():
    priv, _pub = _keypair()
    token = _sign(priv, "whatever", _claims())
    claims = auth.decode_id_token(token)
    assert claims["email"] == "julie@boite.fr"


def test_an_unsupported_algorithm_is_refused():
    """'none' or a weak/unlisted alg must never reach signature checking at
    all — an algorithm-confusion attack works by exploiting exactly this."""
    token = pyjwt.encode(_claims(), "", algorithm="none")
    with pytest.raises(auth.AuthError):
        auth.decode_id_token(token, _provider())


def test_the_jwks_is_actually_fetched_over_http_and_cached(monkeypatch):
    """End to end through _fetch_jwks itself, not the monkeypatched shortcut
    the other tests use — confirms the real HTTP + caching path works."""
    priv, pub = _keypair()
    jwks_body = {"keys": [_jwk(pub, "k1")]}
    calls = []

    class FakeResp:
        def raise_for_status(self): pass
        def json(self): return jwks_body

    def fake_get(url, timeout=10):
        calls.append(url)
        return FakeResp()

    monkeypatch.setattr(auth.httpx, "get", fake_get)

    token = _sign(priv, "k1", _claims())
    p = _provider()
    auth.decode_id_token(token, p)
    auth.decode_id_token(token, p)   # second call must hit the cache, not the network
    assert calls == ["https://idp.example/jwks"]
