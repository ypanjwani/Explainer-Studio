"""Share links: POST/GET/DELETE /api/share and the public /s/<id> page."""

from __future__ import annotations

import json
import re

from conftest import H, J, signup


def record(id="rec1", title="Dropout", **extra) -> dict:
    return {"id": id, "title": title, "createdAt": 1000, "updatedAt": 2000, "status": "done", "prompt": {"text": "x"},
            "request": {"mode": "concept", "text": "How does dropout work?"}, "steps": {}, "progress": {"visited": [], "answers": {}},
            "chat": [], "spec": {"title": title, "stages": [], "design": {}}, **extra}


def save(client, headers, rec: dict) -> None:
    assert client.put(f"/api/explainers/{rec['id']}", json=rec, headers=headers).status_code == 200


def share(client, headers, record_id="rec1", **extra):
    return client.post("/api/share", json={"recordId": record_id, **extra}, headers=headers)


def embedded_spec(page: str) -> dict:
    m = re.search(r"window\.__SPEC__ = (.*?);</script>", page)
    assert m, page
    return json.loads(m.group(1))


def test_share_and_view_signed_out(client):
    a = signup(client)
    save(client, a, record())
    r = share(client, a, mode="dark")
    assert r.status_code == 200, r.get_json()
    out = r.get_json()
    assert out["path"] == f"/s/{out['id']}"
    page = client.get(out["path"])  # no sign-in
    assert page.status_code == 200
    assert page.mimetype == "text/html"
    assert page.headers["Content-Security-Policy"].startswith("sandbox allow-scripts")
    assert "allow-same-origin" not in page.headers["Content-Security-Policy"]
    text = page.get_data(as_text=True)
    assert "<title>Dropout</title>" in text and '<script src="/viewer.js"></script>' in text
    spec = embedded_spec(text)
    assert spec["title"] == "Dropout" and spec["design"]["mode"] == "dark"  # no mode of its own: the one on screen
    assert client.get(f"/api/share/{out['id']}", headers=a).get_json()["id"] == out["id"]


def test_page_mode_is_kept(client):
    a = signup(client)
    rec = record()
    rec["spec"]["design"] = {"mode": "light"}
    save(client, a, rec)
    out = share(client, a, mode="dark").get_json()
    assert embedded_spec(client.get(out["path"]).get_data(as_text=True))["design"]["mode"] == "light"


def test_sharing_again_keeps_the_link_and_updates_it(client):
    a = signup(client)
    save(client, a, record())
    first = share(client, a).get_json()
    save(client, a, record(title="Dropout, revised", updatedAt=3000))
    again = share(client, a).get_json()
    assert again["id"] == first["id"]
    assert "<title>Dropout, revised</title>" in client.get(first["path"]).get_data(as_text=True)


def test_only_the_owner_can_manage_it(client):
    a, b = signup(client), signup(client, "other@example.com")
    save(client, a, record())
    out = share(client, a).get_json()
    assert client.get(f"/api/share/{out['id']}", headers=b).status_code == 404
    assert client.delete(f"/api/share/{out['id']}", headers=b).status_code == 404
    assert share(client, b).status_code == 404  # b has no rec1
    assert client.get(out["path"]).status_code == 200


def test_stop_sharing(client):
    a = signup(client)
    save(client, a, record())
    out = share(client, a).get_json()
    assert client.delete(f"/api/share/{out['id']}", headers=a).status_code == 200
    page = client.get(out["path"])
    assert page.status_code == 404 and "work anymore" in page.get_data(as_text=True)
    assert client.delete(f"/api/share/{out['id']}", headers=a).status_code == 404


def test_deleting_the_explainer_removes_its_link(client):
    a = signup(client)
    save(client, a, record())
    out = share(client, a).get_json()
    assert client.delete("/api/explainers/rec1", headers=a).status_code == 200
    assert client.get(out["path"]).status_code == 404


def test_bad_ids_and_unfinished_explainers(client):
    a = signup(client)
    assert client.get("/s/..%2F..").status_code == 404
    assert client.get("/s/nope").status_code == 404
    assert client.get("/api/share/bad!id", headers=a).status_code == 404
    assert share(client, a, record_id="").status_code == 400
    assert share(client, a, record_id="missing").status_code == 404
    save(client, a, record(status="generating", spec=None))
    assert share(client, a).status_code == 409


def test_needs_sign_in(client):
    assert client.post("/api/share", json={"recordId": "rec1"}, headers=J).status_code == 401
    assert client.get("/api/share/abcdefghijkl", headers=H).status_code == 401


def test_script_in_the_title_cant_break_out(client):
    a = signup(client)
    evil = "</script><script>alert(1)</script>"
    save(client, a, record(title=evil))
    text = client.get(share(client, a).get_json()["path"]).get_data(as_text=True)
    assert "<script>alert(1)" not in text
    assert embedded_spec(text)["title"] == evil
