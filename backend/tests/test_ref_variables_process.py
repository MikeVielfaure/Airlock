"""
`ProcessRequest.ref_variables` — a computed column may reference a
référentiel variable by name; the value is resolved server-side at process
time, never taken from the client, and a secret is refused even if asked for
by name (the picker already excludes it — this is the fail-closed backstop).
"""
import io

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

CSV = "NOM\nAlice\nBob\n"
FIELDS = {"NOM": {"name": ["NOM"], "type": "string"}}
COLS = ["NOM"]


def _upload():
    r = client.post("/api/files", files={"file": ("d.csv", io.BytesIO(CSV.encode()), "text/csv")},
                    data={"file_type": "CSV", "encoding": "AUTO", "delimiter": ";"})
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def test_a_referentiel_variable_is_resolved_server_side():
    client.post("/api/variables", json={"name": "societe_v1", "value": "italie", "kind": "value"})
    sid = _upload()
    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": COLS, "fields": FIELDS,
        "computed": [{"name": "PAYS", "expression": "[societe_v1]"}],
        "ref_variables": ["societe_v1"]})
    assert r.status_code == 200, r.text
    body = r.json()
    pays_col = body["columns"].index("PAYS")
    assert body["data"][0][pays_col] == "italie"


def test_a_dotted_reference_reaches_a_json_variable_field():
    client.post("/api/variables", json={"name": "conn_v1",
                                        "value": '{"base_url": "https://api.exemple.fr"}',
                                        "kind": "api"})
    sid = _upload()
    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": COLS, "fields": FIELDS,
        "computed": [{"name": "URL", "expression": "[conn_v1.base_url]"}],
        "ref_variables": ["conn_v1"]})
    assert r.status_code == 200, r.text
    body = r.json()
    url_col = body["columns"].index("URL")
    assert body["data"][0][url_col] == "https://api.exemple.fr"


def test_a_secret_referentiel_variable_is_never_resolved_even_if_named():
    client.post("/api/variables", json={"name": "secret_v1", "value": "hunter2",
                                        "kind": "value", "secret": True})
    sid = _upload()
    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": COLS, "fields": FIELDS,
        "computed": [{"name": "X", "expression": "[secret_v1]"}],
        "ref_variables": ["secret_v1"]})
    assert r.status_code == 200, r.text
    body = r.json()
    x_col = body["columns"].index("X")
    assert body["data"][0][x_col] == ""


def test_a_hand_typed_variable_overrides_a_referentiel_one_with_the_same_name():
    client.post("/api/variables", json={"name": "clash_v1", "value": "depuis_referentiel",
                                        "kind": "value"})
    sid = _upload()
    r = client.post(f"/api/files/{sid}/process", json={
        "visible_cols": COLS, "fields": FIELDS,
        "computed": [{"name": "X", "expression": "[clash_v1]"}],
        "ref_variables": ["clash_v1"],
        "variables": {"clash_v1": "manuel"}})
    assert r.status_code == 200, r.text
    body = r.json()
    x_col = body["columns"].index("X")
    assert body["data"][0][x_col] == "manuel"
