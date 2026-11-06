""""Continue with Google" (OpenID Connect, authorization code flow with PKCE).

1. /api/auth/google/start makes a random state, nonce and PKCE verifier, keeps them in a short-lived signed
   cookie (SameSite=Lax: the browser must send it on the return from Google, which Strict would block), and
   sends the browser to Google.
2. /api/auth/google/callback checks the state against the cookie, swaps the code for tokens at Google's token
   endpoint, and checks the ID token (issuer, audience, expiry, nonce, verified email).

The ID token comes straight from Google's token endpoint over TLS, so its claims are checked but its signature
isn't (Google's OpenID Connect docs allow this). No Google library: httpx and PyJWT are already dependencies.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
import jwt

from .schemas import normalize_email

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
ISSUERS = {"https://accounts.google.com", "accounts.google.com"}

STATE_COOKIE = "es_gstate"
STATE_COOKIE_PATH = "/api/auth/google"
STATE_SECONDS = 600  # time allowed on Google's pages
_STATE_AUDIENCE = "explainer-studio-google-state"
LEEWAY_SECONDS = 60


class GoogleError(Exception):
    """A failed sign-in; code is what the app page shows a message for (?auth_error=<code>)."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code


@dataclass(frozen=True)
class Identity:
    sub: str
    email: str


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def start(client_id: str, redirect_uri: str, secret: str) -> tuple[str, str]:
    """(Google's sign-in URL, the signed value for the state cookie)."""
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    challenge = _b64(hashlib.sha256(verifier.encode()).digest())
    url = AUTHORIZE_URL + "?" + urlencode({
        "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code",
        "scope": "openid email profile", "state": state, "nonce": nonce,
        "code_challenge": challenge, "code_challenge_method": "S256", "prompt": "select_account",
    })
    now = int(time.time())
    cookie = jwt.encode({"s": state, "n": nonce, "v": verifier, "iat": now, "exp": now + STATE_SECONDS,
                         "aud": _STATE_AUDIENCE}, secret, algorithm="HS256")
    return url, cookie


def read_state(cookie: str, state: str, secret: str) -> tuple[str, str]:
    """(nonce, PKCE verifier) if the cookie is ours, unexpired and matches the state Google sent back."""
    if not cookie or not state:
        raise GoogleError("state", "missing state")
    try:
        data = jwt.decode(cookie, secret, algorithms=["HS256"], audience=_STATE_AUDIENCE)
    except jwt.PyJWTError as e:
        raise GoogleError("state", f"bad state cookie: {e}") from None
    if not secrets.compare_digest(str(data.get("s", "")), state):
        raise GoogleError("state", "state mismatch")
    return str(data["n"]), str(data["v"])


def exchange_code(code: str, verifier: str, client_id: str, client_secret: str, redirect_uri: str) -> str:
    """The ID token for an authorization code. Tests replace this function."""
    try:
        res = httpx.post(TOKEN_URL, data={
            "code": code, "client_id": client_id, "client_secret": client_secret, "redirect_uri": redirect_uri,
            "grant_type": "authorization_code", "code_verifier": verifier,
        }, timeout=10)
    except httpx.HTTPError as e:
        raise GoogleError("unavailable", f"token endpoint unreachable: {e}") from None
    if res.status_code != 200:
        raise GoogleError("google", f"token endpoint answered {res.status_code}: {res.text[:200]}")
    token = (res.json() or {}).get("id_token")
    if not isinstance(token, str):
        raise GoogleError("google", "no id_token in the token response")
    return token


def check_id_token(id_token: str, client_id: str, nonce: str) -> Identity:
    try:
        claims = jwt.decode(id_token, options={"verify_signature": False})
    except jwt.PyJWTError as e:
        raise GoogleError("google", f"unreadable id_token: {e}") from None
    now = time.time()
    aud = claims.get("aud")
    if claims.get("iss") not in ISSUERS:
        raise GoogleError("google", "wrong issuer")
    if aud != client_id and not (isinstance(aud, list) and client_id in aud):
        raise GoogleError("google", "wrong audience")
    if not isinstance(claims.get("exp"), (int, float)) or claims["exp"] < now - LEEWAY_SECONDS:
        raise GoogleError("google", "expired id_token")
    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise GoogleError("google", "nonce mismatch")
    if claims.get("email_verified") is not True:
        raise GoogleError("not_verified", "email not verified by Google")
    sub, email = claims.get("sub"), claims.get("email")
    if not isinstance(sub, str) or not sub or len(sub) > 64 or not isinstance(email, str):
        raise GoogleError("google", "no sub or email")
    try:
        return Identity(sub=sub, email=normalize_email(email))
    except ValueError:
        raise GoogleError("google", "unusable email") from None
