"""
Identity: one account, several ways to prove it — and the guard that turns
environments from a display convention into a boundary.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
CFG = "fields:\n  A:\n    type: string\n"


def _signup(email, pwd="motdepasse1", token=None):
    h = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post("/api/auth/signup", json={"email": email, "password": pwd}, headers=h)


def _login(email, pwd="motdepasse1"):
    r = client.post("/api/auth/login", json={"email": email, "password": pwd})
    return r.json().get("token", ""), r


def _h(tok):
    return {"Authorization": f"Bearer {tok}"}


def _wipe_identity():
    from app.db import session_scope
    from app.db_models import AuthProvider, AuthSession, Membership, User, UserIdentity
    with session_scope() as s:
        for model in (AuthSession, UserIdentity, Membership, AuthProvider, User):
            for row in s.query(model).all():
                s.delete(row)
        s.commit()


@pytest.fixture(autouse=True)
def _clean():
    """
    Each test starts from a known identity state — and leaves one behind.

    Cleaning afterwards is not tidiness: the moment an account exists the whole
    application closes, so a leftover superadmin from this module would answer
    401 to every other test file.
    """
    _wipe_identity()
    yield
    _wipe_identity()


# ── bootstrap ────────────────────────────────────────────────────────
def test_the_app_is_open_until_the_first_account_then_closes_itself():
    """An install shipping locked behind a default password would be worse:
    default credentials outlive the deployment that set them."""
    assert client.get("/api/auth/state").json()["setup_needed"] is True
    assert client.get("/api/artefacts/config").status_code == 200   # still open

    first = _signup("chef@boite.fr")
    assert first.status_code == 200 and first.json()["is_superadmin"] is True

    assert client.get("/api/auth/state").json()["setup_needed"] is False
    assert client.get("/api/artefacts/config").status_code == 401   # closed now


def test_only_a_superadmin_may_create_further_accounts():
    _signup("chef@boite.fr")
    assert _signup("intrus@boite.fr").status_code == 403
    tok, _ = _login("chef@boite.fr")
    assert _signup("marie@boite.fr", token=tok).status_code == 200


def test_a_wrong_password_and_an_unknown_account_answer_the_same():
    """Distinguishing them would tell an attacker which addresses exist."""
    _signup("chef@boite.fr")
    a = client.post("/api/auth/login", json={"email": "chef@boite.fr", "password": "faux"})
    b = client.post("/api/auth/login", json={"email": "personne@boite.fr", "password": "faux"})
    assert a.status_code == b.status_code == 401
    assert a.json()["detail"] == b.json()["detail"]


def test_a_short_password_is_refused():
    assert _signup("chef@boite.fr", pwd="court").status_code == 422


def test_logging_out_revokes_the_token_immediately():
    _signup("chef@boite.fr")
    tok, _ = _login("chef@boite.fr")
    assert client.get("/api/auth/me", headers=_h(tok)).status_code == 200
    client.post("/api/auth/logout", headers=_h(tok))
    assert client.get("/api/auth/me", headers=_h(tok)).status_code == 401


# ── the guard: env stops being declarative ───────────────────────────
def test_asking_for_an_environment_you_do_not_belong_to_is_refused():
    _signup("chef@boite.fr")
    chef, _ = _login("chef@boite.fr")
    _signup("marie@rh.fr", token=chef)
    client.post("/api/admin/environments/rh/members",
                json={"email": "marie@rh.fr", "role": "editor"}, headers=_h(chef))
    marie, _ = _login("marie@rh.fr")

    assert client.get("/api/artefacts/config?env=rh", headers=_h(marie)).status_code == 200
    # the whole point: typing another team's name no longer works
    ko = client.get("/api/artefacts/config?env=adv", headers=_h(marie))
    assert ko.status_code == 403 and "adv" in ko.json()["detail"]


def test_reading_across_every_environment_is_superadmin_only():
    _signup("chef@boite.fr")
    chef, _ = _login("chef@boite.fr")
    _signup("marie@rh.fr", token=chef)
    client.post("/api/admin/environments/rh/members",
                json={"email": "marie@rh.fr", "role": "admin"}, headers=_h(chef))
    marie, _ = _login("marie@rh.fr")
    assert client.get("/api/artefacts/config?env=*", headers=_h(marie)).status_code == 403
    assert client.get("/api/artefacts/config?env=*", headers=_h(chef)).status_code == 200


def test_a_viewer_may_read_but_not_write():
    _signup("chef@boite.fr")
    chef, _ = _login("chef@boite.fr")
    _signup("paul@rh.fr", token=chef)
    client.post("/api/admin/environments/rh/members",
                json={"email": "paul@rh.fr", "role": "viewer"}, headers=_h(chef))
    paul, _ = _login("paul@rh.fr")
    assert client.get("/api/artefacts/config?env=rh", headers=_h(paul)).status_code == 200
    ko = client.post("/api/artefacts/config?env=rh",
                     json={"name": "x", "yaml": CFG, "environment": "rh"}, headers=_h(paul))
    assert ko.status_code == 403


# ── delegated administration ─────────────────────────────────────────
def test_an_environment_admin_manages_only_their_own():
    """Otherwise every access request lands on the superadmin, who becomes the
    bottleneck by the third team onboarded."""
    _signup("chef@boite.fr")
    chef, _ = _login("chef@boite.fr")
    for e in ("rh@boite.fr", "adv@boite.fr"):
        _signup(e, token=chef)
    client.post("/api/admin/environments/rh/members",
                json={"email": "rh@boite.fr", "role": "admin"}, headers=_h(chef))
    rh, _ = _login("rh@boite.fr")

    _signup("nouvelle@rh.fr", token=chef)
    ok = client.post("/api/admin/environments/rh/members",
                     json={"email": "nouvelle@rh.fr", "role": "operator"}, headers=_h(rh))
    assert ok.status_code == 200
    ko = client.post("/api/admin/environments/adv/members",
                     json={"email": "nouvelle@rh.fr", "role": "admin"}, headers=_h(rh))
    assert ko.status_code == 403


# ── SSO: same account, roles from the directory ──────────────────────
def test_sso_groups_become_memberships_and_are_refreshed_at_each_login():
    from app.db import session_scope
    from app.db_models import AuthProvider
    from app.services import auth_service as auth

    with session_scope() as s:
        p = AuthProvider(name="corp", client_id="id", groups_claim="groups",
                         claim_mappings=[{"group": "RH-SAISIE", "environment": "rh",
                                          "role": "editor"},
                                         {"group": "ADV-LECTURE", "environment": "adv",
                                          "role": "viewer"}])
        s.add(p)
        s.commit()

        u = auth.user_from_claims(s, p, {"sub": "abc", "email": "julie@boite.fr",
                                         "groups": ["RH-SAISIE", "ADV-LECTURE"]})
        s.commit()
        assert auth.memberships_of(s, u.id) == {"rh": "editor", "adv": "viewer"}

        # removed from a group in the directory → access actually goes away
        u2 = auth.user_from_claims(s, p, {"sub": "abc", "email": "julie@boite.fr",
                                          "groups": ["RH-SAISIE"]})
        s.commit()
        assert auth.memberships_of(s, u2.id) == {"rh": "editor"}
        assert u2.id == u.id                       # same account throughout


def test_an_sso_signin_attaches_to_an_existing_account_instead_of_duplicating():
    from app.db import session_scope
    from app.db_models import AuthProvider
    from app.services import auth_service as auth

    _signup("chef@boite.fr")
    with session_scope() as s:
        chef = auth.find_user(s, "chef@boite.fr")
        auth.set_membership(s, chef.id, "rh", "admin")
        p = AuthProvider(name="corp", client_id="id", claim_mappings=[])
        s.add(p)
        s.commit()
        same = auth.user_from_claims(s, p, {"sub": "xyz", "email": "chef@boite.fr"})
        s.commit()
        # the account — and its roles — are kept
        assert same.id == chef.id
        assert "rh" in auth.memberships_of(s, same.id)


def test_an_sso_granted_role_is_not_editable_by_hand():
    """The next sign-in would silently undo the edit, so refuse it outright."""
    from app.db import session_scope
    from app.db_models import AuthProvider
    from app.services import auth_service as auth

    _signup("chef@boite.fr")
    chef_tok, _ = _login("chef@boite.fr")
    with session_scope() as s:
        p = AuthProvider(name="corp", client_id="id",
                         claim_mappings=[{"group": "G", "environment": "rh", "role": "viewer"}])
        s.add(p)
        s.commit()
        auth.user_from_claims(s, p, {"sub": "s1", "email": "julie@boite.fr", "groups": ["G"]})
        s.commit()

    r = client.post("/api/admin/environments/rh/members",
                    json={"email": "julie@boite.fr", "role": "admin"}, headers=_h(chef_tok))
    assert r.status_code == 409
    assert "directory" in r.json()["detail"].lower()


def test_direct_claims_are_only_accepted_while_the_install_is_empty():
    from app.db import session_scope
    from app.db_models import AuthProvider
    with session_scope() as s:
        s.add(AuthProvider(name="corp", client_id="id", claim_mappings=[]))
        s.commit()
    _signup("chef@boite.fr")
    r = client.post("/api/auth/sso/corp/callback",
                    json={"code": "x", "claims": {"sub": "s", "email": "faux@boite.fr"}})
    assert r.status_code == 403


# ── sandbox: borrowing an identity to check a role design ────────────
def test_a_superadmin_can_borrow_an_identity_and_it_is_visible():
    """Designing roles blind is how a team ends up with an operator who cannot
    do their job. Trying it is the only reliable check."""
    _signup("chef@boite.fr")
    chef, _ = _login("chef@boite.fr")
    client.post("/api/admin/quick-user",
                json={"email": "marie@rh.fr", "memberships": {"rh": "operator"}},
                headers=_h(chef))

    r = client.post("/api/admin/impersonate", json={"email": "marie@rh.fr"},
                    headers=_h(chef))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["as_email"] == "marie@rh.fr" and body["impersonating"] is True

    borrowed = body["token"]
    state = client.get("/api/auth/state", headers=_h(borrowed)).json()
    assert state["user"]["email"] == "marie@rh.fr"
    # The interface must never quietly forget whose seat it is in.
    assert state["user"]["impersonated_by"] == "chef@boite.fr"
    # And it really is her rights, not the admin's
    assert state["user"]["environments"] == {"rh": "operator"}
    assert "config.write" not in state["user"]["capabilities"]["rh"]


def test_borrowing_is_refused_to_non_superadmins_and_onto_superadmins():
    _signup("chef@boite.fr")
    chef, _ = _login("chef@boite.fr")
    client.post("/api/admin/quick-user",
                json={"email": "rh@x.fr", "memberships": {"rh": "admin"}}, headers=_h(chef))
    rh, _ = _login("rh@x.fr")

    assert client.post("/api/admin/impersonate", json={"email": "chef@boite.fr"},
                       headers=_h(rh)).status_code == 403
    # borrowing a superadmin's identity must not be a route to more power
    assert client.post("/api/admin/impersonate", json={"email": "chef@boite.fr"},
                       headers=_h(chef)).status_code == 409


def test_a_test_account_and_its_roles_are_created_in_one_call():
    _signup("chef@boite.fr")
    chef, _ = _login("chef@boite.fr")
    r = client.post("/api/admin/quick-user",
                    json={"email": "essai@x.fr",
                          "memberships": {"rh": "operator", "adv": "viewer"}},
                    headers=_h(chef))
    assert r.status_code == 200
    assert r.json()["environments"] == {"rh": "operator", "adv": "viewer"}

    # iterating on a design means changing the same person's role repeatedly
    again = client.post("/api/admin/quick-user",
                        json={"email": "essai@x.fr", "memberships": {"rh": "editor"}},
                        headers=_h(chef))
    assert again.json()["environments"]["rh"] == "editor"


def test_a_superadmin_can_reset_anyones_password():
    _signup("chef@boite.fr")
    chef, _ = _login("chef@boite.fr")
    client.post("/api/admin/quick-user",
                json={"email": "marie@rh.fr", "memberships": {"rh": "operator"}},
                headers=_h(chef))
    marie_id = [u for u in client.get("/api/admin/users", headers=_h(chef)).json()
               if u["email"] == "marie@rh.fr"][0]["id"]

    r = client.post(f"/api/admin/users/{marie_id}/password",
                    json={"password": "nouveaumdp1"}, headers=_h(chef))
    assert r.status_code == 200, r.text

    # the old password no longer works, the new one does
    assert _login("marie@rh.fr", "motdepasse1")[1].status_code == 401
    assert _login("marie@rh.fr", "nouveaumdp1")[1].status_code == 200


def test_resetting_a_password_is_superadmin_only():
    _signup("chef@boite.fr")
    chef, _ = _login("chef@boite.fr")
    client.post("/api/admin/quick-user",
                json={"email": "op@rh.fr", "memberships": {"rh": "operator"}}, headers=_h(chef))
    op, _ = _login("op@rh.fr")
    marie_id = [u for u in client.get("/api/admin/users", headers=_h(chef)).json()
               if u["email"] == "op@rh.fr"][0]["id"]
    r = client.post(f"/api/admin/users/{marie_id}/password",
                    json={"password": "nouveaumdp1"}, headers=_h(op))
    assert r.status_code == 403


def test_a_reset_password_still_needs_eight_characters():
    _signup("chef@boite.fr")
    chef, _ = _login("chef@boite.fr")
    chef_id = client.get("/api/auth/me", headers=_h(chef)).json()["id"]
    r = client.post(f"/api/admin/users/{chef_id}/password",
                    json={"password": "court"}, headers=_h(chef))
    assert r.status_code == 422


def test_deactivating_an_account_blocks_sign_in_without_erasing_it():
    _signup("chef@boite.fr")
    chef, _ = _login("chef@boite.fr")
    client.post("/api/admin/quick-user",
                json={"email": "marie@rh.fr", "memberships": {"rh": "operator"}},
                headers=_h(chef))
    marie_tok, _ = _login("marie@rh.fr")
    marie_id = [u for u in client.get("/api/admin/users", headers=_h(chef)).json()
               if u["email"] == "marie@rh.fr"][0]["id"]

    r = client.post(f"/api/admin/users/{marie_id}/deactivate", headers=_h(chef))
    assert r.status_code == 200, r.text

    # the login itself is refused, and the token she already held stops working
    assert _login("marie@rh.fr")[1].status_code == 401
    assert client.get("/api/auth/me", headers=_h(marie_tok)).status_code == 401

    # nothing about the account was erased — membership survives
    users = client.get("/api/admin/users", headers=_h(chef)).json()
    marie = [u for u in users if u["email"] == "marie@rh.fr"][0]
    assert marie["active"] is False and marie["environments"] == {"rh": "operator"}

    resurrect = client.post(f"/api/admin/users/{marie_id}/reactivate", headers=_h(chef))
    assert resurrect.status_code == 200
    assert _login("marie@rh.fr")[1].status_code == 200


def _del_user(user_id, confirm_email, token):
    return client.request("DELETE", f"/api/admin/users/{user_id}",
                          json={"confirm_email": confirm_email}, headers=_h(token))


def test_the_last_active_superadmin_cannot_be_deactivated_or_deleted():
    _signup("chef@boite.fr")
    chef, _ = _login("chef@boite.fr")
    chef_id = client.get("/api/auth/me", headers=_h(chef)).json()["id"]

    # not even by someone else, and not by themselves either
    assert client.post(f"/api/admin/users/{chef_id}/deactivate", headers=_h(chef)).status_code == 422
    assert _del_user(chef_id, "chef@boite.fr", chef).status_code == 422  # self-delete refused first

    client.post("/api/admin/quick-user",
                json={"email": "second@boite.fr", "password": "secondpwd1", "memberships": {}},
                headers=_h(chef))
    # promote "second" so chef stops being the only one, then chef can be dealt with
    from app.db import session_scope
    from app.db_models import User
    with session_scope() as s:
        s.query(User).filter(User.email == "second@boite.fr").update({"is_superadmin": True})

    other, _ = _login("second@boite.fr", "secondpwd1")
    r = _del_user(chef_id, "chef@boite.fr", other)
    assert r.status_code == 200, r.text


def test_deleting_an_account_needs_the_email_typed_and_is_irreversible():
    _signup("chef@boite.fr")
    chef, _ = _login("chef@boite.fr")
    client.post("/api/admin/quick-user",
                json={"email": "paul@rh.fr", "memberships": {"rh": "viewer"}}, headers=_h(chef))
    paul_id = [u for u in client.get("/api/admin/users", headers=_h(chef)).json()
              if u["email"] == "paul@rh.fr"][0]["id"]

    assert _del_user(paul_id, "faux@x.fr", chef).status_code == 422

    r = _del_user(paul_id, "paul@rh.fr", chef)
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] == "paul@rh.fr"

    users = client.get("/api/admin/users", headers=_h(chef)).json()
    assert "paul@rh.fr" not in {u["email"] for u in users}
    # gone for good — no account left to sign into
    assert _login("paul@rh.fr")[1].status_code == 401


def test_deleting_users_is_superadmin_only():
    _signup("chef@boite.fr")
    chef, _ = _login("chef@boite.fr")
    client.post("/api/admin/quick-user",
                json={"email": "op@rh.fr", "memberships": {"rh": "operator"}}, headers=_h(chef))
    client.post("/api/admin/quick-user",
                json={"email": "cible@rh.fr", "memberships": {"rh": "viewer"}}, headers=_h(chef))
    op, _ = _login("op@rh.fr")
    cible_id = [u for u in client.get("/api/admin/users", headers=_h(chef)).json()
               if u["email"] == "cible@rh.fr"][0]["id"]
    assert _del_user(cible_id, "cible@rh.fr", op).status_code == 403


def test_the_state_endpoint_says_what_the_caller_may_do():
    _signup("chef@boite.fr")
    chef, _ = _login("chef@boite.fr")
    client.post("/api/admin/quick-user",
                json={"email": "op@x.fr", "memberships": {"rh": "operator"}},
                headers=_h(chef))
    op, _ = _login("op@x.fr")
    caps = client.get("/api/auth/state", headers=_h(op)).json()["user"]["capabilities"]["rh"]
    assert "file.process" in caps and "config.write" not in caps


# ── the console: one picture instead of five screens ─────────────────
def test_the_overview_assembles_the_whole_picture():
    _signup("chef@boite.fr")
    chef, _ = _login("chef@boite.fr")
    client.post("/api/admin/quick-user",
                json={"email": "marie@rh.fr", "memberships": {"apercu-rh": "operator"}},
                headers=_h(chef))
    client.post("/api/admin/quick-user",
                json={"email": "paul@rh.fr", "memberships": {"apercu-rh": "viewer"}},
                headers=_h(chef))
    client.post("/api/environments/apercu-rh/profile",
                json={"modules": ["data", "report", "tco"]}, headers=_h(chef))

    o = client.get("/api/admin/overview", headers=_h(chef))
    assert o.status_code == 200, o.text
    body = o.json()

    rh = [e for e in body["environments"] if e["name"] == "apercu-rh"][0]
    assert rh["roles"] == {"admin": 0, "editor": 0, "operator": 1, "viewer": 1}
    assert rh["modules_restricted"] is True
    assert rh["url"] == "/?env=apercu-rh"          # a deep link, not instructions
    assert {m["email"] for m in rh["members"]} == {"marie@rh.fr", "paul@rh.fr"}

    # the policy travels with it, so a role name need not be interpreted
    assert "file.process" in body["policy"]["operator"]
    assert "config.write" not in body["policy"]["operator"]
    assert body["totals"]["users"] >= 3


def test_the_environment_detail_reports_each_members_last_login():
    _signup("chef@boite.fr")
    chef, _ = _login("chef@boite.fr")
    client.post("/api/admin/quick-user",
                json={"email": "marie@rh.fr", "memberships": {"apercu-rh": "operator"}},
                headers=_h(chef))
    marie, _ = _login("marie@rh.fr")

    rh = [e for e in client.get("/api/admin/overview", headers=_h(chef)).json()["environments"]
          if e["name"] == "apercu-rh"][0]
    marie_row = [m for m in rh["members"] if m["email"] == "marie@rh.fr"][0]
    assert marie_row["last_login_at"]


def test_an_environment_nobody_joined_still_appears():
    """That is exactly the one worth seeing in a console."""
    _signup("chef@boite.fr")
    chef, _ = _login("chef@boite.fr")
    client.post("/api/environments",
                json={"name": "apercu-vide", "template": "complet"}, headers=_h(chef))
    names = [e["name"] for e in
             client.get("/api/admin/overview", headers=_h(chef)).json()["environments"]]
    assert "apercu-vide" in names


def test_an_environment_without_a_profile_reports_every_module():
    """Showing zero would read as "nothing available"."""
    _signup("chef@boite.fr")
    chef, _ = _login("chef@boite.fr")
    client.post("/api/admin/quick-user",
                json={"email": "x@y.fr", "memberships": {"libre": "editor"}},
                headers=_h(chef))
    e = [x for x in client.get("/api/admin/overview", headers=_h(chef)).json()["environments"]
         if x["name"] == "libre"][0]
    assert e["has_profile"] is False
    assert len(e["modules"]) >= 12 and e["modules_restricted"] is False


def test_the_overview_is_superadmin_only():
    _signup("chef@boite.fr")
    chef, _ = _login("chef@boite.fr")
    client.post("/api/admin/quick-user",
                json={"email": "op@x.fr", "memberships": {"rh": "admin"}}, headers=_h(chef))
    op, _ = _login("op@x.fr")
    assert client.get("/api/admin/overview", headers=_h(op)).status_code == 403
