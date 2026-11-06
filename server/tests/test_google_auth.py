"""'Continue with Google': the start redirect and state cookie, the callback's checks (state, ID token claims),
finding / linking / creating accounts, the network sign-up cap, and Google-only accounts (no password)."""

from __future__ import annotations

import time
from urllib.parse import parse_qs, urlparse

import jwt
import pytest

from conftest import H, J
from explainer import google_auth
from explainer.app import create_app

CLIENT_ID = "test-client.apps.googleusercontent.com"
PW = "correct horse battery"


def id_token(**changes) -> str:
    claims = {"iss": "https://accounts.google.com", "aud": CLIENT_ID, "exp": int(time.time()) + 600,
              "sub": "google-sub-1", "email": "Learner@Gmail.com", "email_verified": True, **changes}
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, "google-signs-these-" + "k" * 32, algorithm="HS256")


@pytest.fixture
def google(monkeypatch):
    """Fakes Google's token endpoint. Set .claims (changes to the default ID token) before signing in;
    the nonce is copied from the start redirect unless .claims sets one."""
    class Fake:
        claims: dict = {}
        nonce = ""
        calls: list = []

    def exchange(code, verifier, client_id, client_secret, redirect_uri):
        Fake.calls.append({"code": code, "verifier": verifier, "redirect_uri": redirect_uri})
        return id_token(**{"nonce": Fake.nonce, **Fake.claims})

    monkeypatch.setattr(google_auth, "exchange_code", exchange)
    Fake.claims, Fake.calls = {}, []
    return Fake


def start(client, google=None) -> dict:
    r = client.get("/api/auth/google/start")
    assert r.status_code == 302, r.get_data(as_text=True)
    q = parse_qs(urlparse(r.headers["Location"]).query)
    if google is not None:
        google.nonce = q["nonce"][0]
    return {k: v[0] for k, v in q.items()}


def sign_in(client, google, **claims):
    google.claims = claims
    q = start(client, google)
    return client.get(f"/api/auth/google/callback?code=abc&state={q['state']}")


def session(client) -> dict:
    """The session the app page gets on load from the refresh cookie the callback set."""
    r = client.post("/api/auth/refresh", json={}, headers=H)
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def bearer(s: dict) -> dict:
    return {**J, "Authorization": f"Bearer {s['accessToken']}"}


def test_start_redirects_to_google_with_pkce(client):
    r = client.get("/api/auth/google/start")
    assert r.status_code == 302
    url = urlparse(r.headers["Location"])
    q = parse_qs(url.query)
    assert url.netloc == "accounts.google.com"
    assert q["client_id"] == [CLIENT_ID] and q["response_type"] == ["code"]
    assert q["scope"] == ["openid email profile"] and q["code_challenge_method"] == ["S256"]
    assert q["redirect_uri"] == ["http://localhost/api/auth/google/callback"]
    assert q["state"][0] and q["nonce"][0] and q["code_challenge"][0]
    cookie = next(c for c in r.headers.getlist("Set-Cookie") if c.startswith("es_gstate="))
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie and "Path=/api/auth/google" in cookie


def test_redirect_uri_setting(app, monkeypatch):
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "https://app.example.com/api/auth/google/callback")
    q = start(create_app().test_client())
    assert q["redirect_uri"] == "https://app.example.com/api/auth/google/callback"


def test_new_account(client, google):
    r = sign_in(client, google)
    assert r.status_code == 302 and r.headers["Location"] == "/"
    cookies = r.headers.getlist("Set-Cookie")
    refresh = next(c for c in cookies if c.startswith("es_refresh="))
    assert "SameSite=Strict" in refresh and "HttpOnly" in refresh
    assert any(c.startswith("es_gstate=;") for c in cookies)  # the state cookie is used up
    s = session(client)
    assert s["user"]["email"] == "learner@gmail.com"
    # The PKCE verifier sent to Google is the one whose hash went in the start redirect.
    assert google.calls[0]["code"] == "abc" and google.calls[0]["verifier"]


def test_second_sign_in_finds_the_same_account(client, google):
    sign_in(client, google)
    first = session(client)["user"]["id"]
    client.post("/api/auth/logout", json={}, headers=H)
    # Found by Google's id, even after the address changed on Google's side.
    sign_in(client, google, email="new-address@gmail.com")
    assert session(client)["user"]["id"] == first


def test_links_an_existing_password_account(client, google):
    r = client.post("/api/auth/register", json={"email": "learner@gmail.com", "password": PW}, headers=H)
    old = r.get_json()["user"]["id"]
    client.post("/api/auth/logout", json={}, headers=H)
    sign_in(client, google)
    s = session(client)
    assert s["user"]["id"] == old


def test_email_linked_to_another_google_account_is_refused(client, google):
    sign_in(client, google)
    client.post("/api/auth/logout", json={}, headers=H)
    r = sign_in(client, google, sub="google-sub-2")
    assert r.headers["Location"] == "/?auth_error=inactive"


@pytest.mark.parametrize("claims, code", [
    ({"aud": "someone-else"}, "google"),
    ({"iss": "https://evil.example.com"}, "google"),
    ({"exp": int(time.time()) - 3600}, "google"),
    ({"nonce": "not-the-nonce"}, "google"),
    ({"email_verified": False}, "not_verified"),
    ({"email_verified": None}, "not_verified"),
    ({"sub": None}, "google"),
    ({"email": "not an email"}, "google"),
])
def test_bad_id_tokens(client, google, claims, code):
    r = sign_in(client, google, **claims)
    assert r.status_code == 302 and r.headers["Location"] == f"/?auth_error={code}"
    assert not any(c.startswith("es_refresh=") for c in r.headers.getlist("Set-Cookie"))
    assert client.post("/api/auth/refresh", json={}, headers=H).status_code == 401


def test_state_checks(client, google):
    q = start(client, google)
    assert client.get("/api/auth/google/callback?code=abc&state=forged").headers["Location"] == "/?auth_error=state"
    assert client.get("/api/auth/google/callback?code=abc").headers["Location"] == "/?auth_error=state"
    # No state cookie (a different browser, or the cookie expired): refused even with the right state.
    client.delete_cookie("es_gstate", path="/api/auth/google")
    assert client.get(f"/api/auth/google/callback?code=abc&state={q['state']}").headers["Location"] == "/?auth_error=state"
    assert google.calls == []  # Google's token endpoint was never asked


def test_cancel_on_google(client, google):
    start(client, google)
    r = client.get("/api/auth/google/callback?error=access_denied")
    assert r.headers["Location"] == "/?auth_error=cancelled"


def test_google_off_without_keys(app, monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET")
    r = create_app().test_client().get("/api/auth/google/start")
    assert r.status_code == 302 and r.headers["Location"] == "/?auth_error=unavailable"


def test_network_sign_up_cap(app, monkeypatch, google):
    monkeypatch.setenv("NETWORK_DAILY_SIGNUPS", "1")
    client = create_app().test_client()
    sign_in(client, google)
    assert sign_in(client, google, sub="google-sub-2", email="second@gmail.com").headers["Location"] == "/?auth_error=network_limit"
    # An existing account still signs in.
    assert sign_in(client, google).headers["Location"] == "/"


def test_disabled_account(client, app, google):
    from explainer.db import User
    sign_in(client, google)
    with app.extensions["explainer"].db.session() as s:
        s.query(User).update({"is_active": False})
    assert sign_in(client, google).headers["Location"] == "/?auth_error=inactive"


def test_password_routes_are_off_by_default(app, monkeypatch):
    from explainer.db import User
    from explainer.security import hash_password
    monkeypatch.delenv("PASSWORD_AUTH")
    off = create_app()
    with off.extensions["explainer"].db.session() as s:  # an early tester's password account
        s.add(User(email="a@example.com", password_hash=hash_password(PW)))
    client = off.test_client()
    for res in (client.post("/api/auth/register", json={"email": "b@example.com", "password": PW}, headers=H),
                client.post("/api/auth/login", json={"email": "a@example.com", "password": PW}, headers=H)):
        assert res.status_code == 403 and res.get_json()["code"] == "password_off"


def test_google_account_has_no_password(client, google):
    sign_in(client, google)
    a = bearer(session(client))
    r = client.post("/api/auth/password", json={"currentPassword": "anything", "newPassword": "a new password"}, headers=a)
    assert r.status_code == 422 and r.get_json()["code"] == "no_password"
    assert client.post("/api/auth/login", json={"email": "learner@gmail.com", "password": "anything"}, headers=H).status_code == 401


def test_delete_google_account_by_typing_the_email(client, google):
    sign_in(client, google)
    a = bearer(session(client))
    assert client.delete("/api/auth/me", json={}, headers=a).status_code == 403
    assert client.delete("/api/auth/me", json={"email": "other@gmail.com"}, headers=a).status_code == 403
    assert client.delete("/api/auth/me", json={"password": "anything"}, headers=a).status_code == 403
    assert client.delete("/api/auth/me", json={"email": " Learner@Gmail.com "}, headers=a).status_code == 200
    assert client.get("/api/auth/me", headers=a).status_code == 401


def test_password_account_deletes_by_typing_the_email(client):
    r = client.post("/api/auth/register", json={"email": "p@example.com", "password": PW}, headers=H)
    a = {**J, "Authorization": f"Bearer {r.get_json()['accessToken']}"}
    assert client.delete("/api/auth/me", json={"password": "wrong"}, headers=a).status_code == 403
    assert client.delete("/api/auth/me", json={"email": "p@example.com"}, headers=a).status_code == 200
