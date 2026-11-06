"""Accounts: Google sign-in, password sign-in, refresh, sign out, change password, delete account.

  GET    /api/auth/google/start                                 -> 302 to Google (state cookie set)
  GET    /api/auth/google/callback  ?code&state                 -> 302 to / with the refresh cookie, or /?auth_error=<code>
  POST   /api/auth/register    {email, password}                -> session  } off (403 password_off) unless
  POST   /api/auth/login       {email, password}                -> session  } PASSWORD_AUTH=1 (tests)
  POST   /api/auth/refresh     (refresh cookie)                 -> session (the cookie is rotated)
  POST   /api/auth/logout      (refresh cookie)                 -> {ok}
  POST   /api/auth/logout-all  (Bearer)                         -> {ok}: every device signs out
  GET    /api/auth/me          (Bearer)                         -> {user, allowance}
  POST   /api/auth/password    (Bearer) {currentPassword, newPassword} -> session; off unless PASSWORD_AUTH=1
  DELETE /api/auth/me          (Bearer) {email} (or {password}) -> {ok}: the account and all its data

A session is {accessToken, expiresAt, user, allowance}; the refresh token travels only in an httpOnly,
SameSite=Strict cookie scoped to /api/auth, so page scripts never see it.

Sign-in is Google only (v1, Nov 5 2026), and Google has verified the email: that ends fake sign-ups with made-up
addresses, email squatting and (with register off) the "email_taken" enumeration of audit F34. A Google sign-in
with the email of an existing password account opens that account, so early testers keep their history. The
password routes stay in the code, switched off; tests turn them on to make accounts quickly.
"""

from __future__ import annotations

import logging
import time
from typing import NoReturn

from flask import Blueprint, Response, g, redirect, request
from limits import parse as parse_limit
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from .. import google_auth, quota
from ..db import RefreshToken, User, new_id, now_ms
from ..middleware import ApiError, client_ip, client_network, ctx, error_response, json_body, json_response, rate_limit, require_auth
from ..schemas import ChangePassword, DeleteAccount, Empty, Login, Register, validate
from ..security import create_access_token, hash_password, hash_token, new_refresh_token, verify_password

log = logging.getLogger("explainer")
bp = Blueprint("auth", __name__, url_prefix="/api/auth")

COOKIE = "es_refresh"
COOKIE_PATH = "/api/auth"
REUSE_GRACE_MS = 20_000  # two tabs refreshing with the same cookie at once is not theft

REGISTER_LIMIT = parse_limit("5/hour")  # per IP
GOOGLE_LIMIT = parse_limit("30/hour")  # Google sign-in starts and returns, per IP
LOGIN_LIMIT = parse_limit("20/minute")  # per IP
# Failed sign-ins. The block is per email *and* network, so a stranger can't lock the owner out (audit F05);
# the looser per-email ceiling still stops guessing spread over many networks.
LOGIN_FAIL_LIMIT = parse_limit("10/15minutes")  # per (email, network)
LOGIN_FAIL_EMAIL_LIMIT = parse_limit("100/hour")  # per email, any network
REFRESH_LIMIT = parse_limit("60/minute")  # per IP
ACCOUNT_LIMIT = parse_limit("10/hour")  # password changes and deletions, per user


def public_user(u: User) -> dict:
    return {"id": u.id, "email": u.email, "plan": u.plan, "createdAt": u.created_at}


def _add_refresh(s, user_id: str, family: str | None = None, token_id: str | None = None) -> str:
    """Add a new refresh token row in session s; returns the raw token for the cookie."""
    c = ctx()
    raw = new_refresh_token()
    now = now_ms()
    s.add(RefreshToken(id=token_id or new_id(), user_id=user_id, token_hash=hash_token(raw), family_id=family or new_id(), created_at=now,
                       expires_at=now + c.settings.refresh_days * 86_400_000,
                       user_agent=request.headers.get("User-Agent", "")[:200], ip=client_ip()[:64]))
    return raw


def _issue(user: User, family: str | None = None) -> Response:
    """A session response: a fresh access token, and a new refresh token in the cookie."""
    with ctx().db.session() as s:
        raw = _add_refresh(s, user.id, family)
    return _session_response(user, raw)


def _session_response(user: User, raw: str) -> Response:
    """The session JSON for user, with raw (an already stored refresh token) in the cookie."""
    c = ctx()
    token, exp = create_access_token(user.id, user.token_version, c.settings.jwt_secret, c.settings.access_minutes)
    res = json_response({"accessToken": token, "expiresAt": exp, "user": public_user(user),
                         "allowance": quota.allowance(c.settings, c.db, user.id)})
    return _set_refresh_cookie(res, raw)


def _secure() -> bool:
    return ctx().settings.production or request.is_secure


def _set_refresh_cookie(res: Response, raw: str) -> Response:
    res.set_cookie(COOKIE, raw, max_age=ctx().settings.refresh_days * 86400, path=COOKIE_PATH, httponly=True,
                   secure=_secure(), samesite="Strict")
    return res


def _clear_cookie(res: Response) -> Response:
    res.delete_cookie(COOKIE, path=COOKIE_PATH, httponly=True, secure=_secure(), samesite="Strict")
    return res


def _no_session(message: str = "Please sign in.") -> NoReturn:
    raise _SessionEnded(message)


class _SessionEnded(ApiError):
    def __init__(self, message: str):
        super().__init__(401, message, "no_session")


@bp.errorhandler(_SessionEnded)
def _ended(e: _SessionEnded) -> Response:
    return _clear_cookie(error_response(e.status, e.message, e.code))


def _network_signup_limit():
    """Audit F04: free accounts per network per day (password and Google sign-ups together). Checked before an
    account is made but counted only once it is, so failed attempts don't use up a shared network's sign-ups
    (audit N02). None = no cap."""
    n = ctx().settings.network_daily_signups
    return parse_limit(f"{n}/day") if n > 0 else None


def _check_network_signups(net: str) -> None:
    limiter, limit = ctx().limiter, _network_signup_limit()
    if limit is not None and not limiter.test(limit, "register-net", net):
        reset = limiter.get_window_stats(limit, "register-net", net).reset_time
        raise ApiError(429, "Too many new accounts from this network today. Please try again tomorrow.", "rate_limited",
                       headers={"Retry-After": str(max(1, int(reset - time.time())))})


def _count_network_signup(net: str) -> None:
    limit = _network_signup_limit()
    if limit is not None:
        ctx().limiter.hit(limit, "register-net", net)


@bp.post("/register")
def register() -> Response:
    c = ctx()
    if not c.settings.password_auth:
        _password_off()
    rate_limit(REGISTER_LIMIT, "register", client_ip(), "Too many new accounts from this network. Please try again later.")
    net = client_network()
    _check_network_signups(net)
    body = validate(Register, json_body())
    hashed = hash_password(body.password)  # slow on purpose; outside any database transaction
    try:
        with c.db.session() as s:
            if s.scalar(select(User.id).where(User.email == body.email)):
                raise ApiError(409, "An account with this email already exists. Sign in instead.", "email_taken", {"email": "Already registered."})
            user = User(email=body.email, password_hash=hashed, last_login_at=now_ms(), signup_net=net)
            s.add(user)
    except IntegrityError:  # the same email registered by a parallel request
        raise ApiError(409, "An account with this email already exists. Sign in instead.", "email_taken", {"email": "Already registered."}) from None
    _count_network_signup(net)
    log.info("new account %s", user.id)
    res = _issue(user)
    res.status_code = 201
    return res


def _password_off() -> NoReturn:
    raise ApiError(403, "Sign in with Google: use \"Continue with Google\".", "password_off")


def _redirect_uri() -> str:
    return ctx().settings.google_redirect_uri or request.host_url.rstrip("/") + "/api/auth/google/callback"


def _drop_state(res: Response) -> Response:
    res.delete_cookie(google_auth.STATE_COOKIE, path=google_auth.STATE_COOKIE_PATH, httponly=True, secure=_secure(), samesite="Lax")
    res.headers["Cache-Control"] = "no-store"
    return res


def _google_fail(code: str) -> Response:
    """Back to the app page, which shows a message for code (a page load, so never JSON)."""
    return _drop_state(redirect(f"/?auth_error={code}", 302))


def _google_ready() -> bool:
    s = ctx().settings
    return bool(s.google_client_id and s.google_client_secret)


@bp.get("/google/start")
def google_start() -> Response:
    c = ctx()
    if not _google_ready():
        log.warning("Google sign-in asked for, but GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are not set")
        return _google_fail("unavailable")
    if not c.limiter.hit(GOOGLE_LIMIT, "google", client_ip()):
        return _google_fail("rate_limited")
    url, cookie = google_auth.start(c.settings.google_client_id, _redirect_uri(), c.settings.jwt_secret)
    res = redirect(url, 302)
    res.set_cookie(google_auth.STATE_COOKIE, cookie, max_age=google_auth.STATE_SECONDS, path=google_auth.STATE_COOKIE_PATH,
                   httponly=True, secure=_secure(), samesite="Lax")
    res.headers["Cache-Control"] = "no-store"
    return res


@bp.get("/google/callback")
def google_callback() -> Response:
    c = ctx()
    if not _google_ready():
        return _google_fail("unavailable")
    if not c.limiter.hit(GOOGLE_LIMIT, "google", client_ip()):
        return _google_fail("rate_limited")
    if request.args.get("error"):  # e.g. access_denied: the reader pressed Cancel on Google's page
        return _google_fail("cancelled")
    try:
        nonce, verifier = google_auth.read_state(request.cookies.get(google_auth.STATE_COOKIE, ""),
                                                 request.args.get("state", ""), c.settings.jwt_secret)
        code = request.args.get("code", "")
        if not code:
            raise google_auth.GoogleError("google", "no code")
        id_token = google_auth.exchange_code(code, verifier, c.settings.google_client_id, c.settings.google_client_secret,
                                             _redirect_uri())
        who = google_auth.check_id_token(id_token, c.settings.google_client_id, nonce)
    except google_auth.GoogleError as e:
        log.warning("Google sign-in failed (%s): %s", e.code, e)
        return _google_fail(e.code)
    try:
        user = _google_user(who)
    except ApiError as e:
        if e.status == 429:
            return _google_fail("network_limit")
        raise
    if user is None:
        return _google_fail("inactive")
    with c.db.session() as s:
        raw = _add_refresh(s, user.id)
    # The app page's restore() turns the refresh cookie into a session.
    return _set_refresh_cookie(_drop_state(redirect("/", 302)), raw)


def _google_user(who: google_auth.Identity) -> User | None:
    """The account for a Google identity: by Google id, else by email (linking a password account: Google has
    verified the address), else a new account (within the per-network sign-up cap). None = can't sign in."""
    c = ctx()
    net = client_network()
    with c.db.session() as s:
        user = s.scalar(select(User).where(User.google_sub == who.sub))
        if user is None:
            user = s.scalar(select(User).where(User.email == who.email))
            if user is not None:
                if user.google_sub is not None:  # the address is linked to another Google account already
                    log.warning("Google sign-in: account %s is linked to another Google account", user.id)
                    return None
                user.google_sub = who.sub
                log.info("linked Google sign-in to account %s", user.id)
        if user is not None:
            if not user.is_active:
                return None
            if user.email_verified_at is None:
                user.email_verified_at = now_ms()
            user.last_login_at = now_ms()
            _backfill_net(s, user)
            s.execute(delete(RefreshToken).where(RefreshToken.user_id == user.id, RefreshToken.expires_at < now_ms()))
            s.flush()
            s.expunge(user)
            return user
    _check_network_signups(net)
    try:
        with c.db.session() as s:
            user = User(email=who.email, password_hash=None, google_sub=who.sub, email_verified_at=now_ms(),
                        last_login_at=now_ms(), signup_net=net)
            s.add(user)
            s.flush()
            s.expunge(user)
    except IntegrityError:  # the same Google account signing in twice at once
        with c.db.session() as s:
            user = s.scalar(select(User).where(User.google_sub == who.sub))
            if user is None:
                raise
            s.expunge(user)
            return user
    _count_network_signup(net)
    log.info("new account %s (Google)", user.id)
    return user


@bp.post("/login")
def login() -> Response:
    if not ctx().settings.password_auth:
        _password_off()
    rate_limit(LOGIN_LIMIT, "login", client_ip(), "Too many sign-in attempts. Please wait a minute.")
    body = validate(Login, json_body())
    c = ctx()
    net = client_network()
    if not c.limiter.test(LOGIN_FAIL_LIMIT, "login-fail", body.email, net):
        raise ApiError(429, "Too many failed sign-ins for this account. Try again in 15 minutes.", "rate_limited", headers={"Retry-After": "900"})
    if not c.limiter.test(LOGIN_FAIL_EMAIL_LIMIT, "login-fail-email", body.email):
        raise ApiError(429, "Too many failed sign-ins for this account. Try again in an hour.", "rate_limited", headers={"Retry-After": "3600"})
    with c.db.session() as s:
        user = s.scalar(select(User).where(User.email == body.email))
    # Same work and the same answer whether the email is unknown or the password wrong.
    if not verify_password(body.password, user.password_hash if user else None) or user is None or not user.is_active:
        c.limiter.hit(LOGIN_FAIL_LIMIT, "login-fail", body.email, net)
        c.limiter.hit(LOGIN_FAIL_EMAIL_LIMIT, "login-fail-email", body.email)
        raise ApiError(401, "Wrong email or password.", "bad_credentials")
    c.limiter.clear(LOGIN_FAIL_LIMIT, "login-fail", body.email, net)  # the owner's typos don't pile up
    with c.db.session() as s:
        s.execute(update(User).where(User.id == user.id).values(last_login_at=now_ms()))
        _backfill_net(s, user)
        s.execute(delete(RefreshToken).where(RefreshToken.user_id == user.id, RefreshToken.expires_at < now_ms()))
    return _issue(user)


@bp.post("/refresh")
def refresh() -> Response:
    rate_limit(REFRESH_LIMIT, "refresh", client_ip(), "Too many requests. Please wait a minute.")
    validate(Empty, json_body() if request.get_data() else {})  # no body = {}: the answer is about the session (F31)
    raw = request.cookies.get(COOKIE, "")
    if not raw:
        _no_session()
    c = ctx()
    now = now_ms()
    with c.db.session() as s:
        row = s.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw)))
        if row is None:
            _no_session()
        if row.revoked_at is not None:
            if row.replaced_by and now - row.revoked_at > REUSE_GRACE_MS:
                # A rotated token came back: someone else has a copy. End every session in this chain.
                s.execute(update(RefreshToken).where(RefreshToken.family_id == row.family_id, RefreshToken.revoked_at.is_(None)).values(revoked_at=now))
                log.warning("refresh token reuse for user %s; family revoked", row.user_id)
                s.commit()
                _no_session("Your session was ended for security. Please sign in again.")
            if not row.replaced_by:
                _no_session()
            # Within the grace window, only while the token that replaced it is still live (audit N01): signing
            # out, "sign out everywhere" and a password change revoke it, so an old copy can't bring the session back.
            succ = s.get(RefreshToken, row.replaced_by)
            if succ is None or succ.revoked_at is not None or succ.expires_at <= now:
                _no_session()
        if row.expires_at <= now:
            _no_session("Your session has expired. Please sign in again.")
        user = s.get(User, row.user_id)
        if user is None or not user.is_active:
            _no_session()
        _backfill_net(s, user)
        successor = new_id()
        if row.revoked_at is None:
            # Compare-and-set (audit F20): only one of two refreshes racing with the same cookie rotates it, and
            # replaced_by names the token that replaced it, so the chain can be followed.
            rotated = s.execute(update(RefreshToken).where(RefreshToken.id == row.id, RefreshToken.revoked_at.is_(None))
                                .values(revoked_at=now, replaced_by=successor)).rowcount == 1
            if not rotated:
                s.refresh(row)
                if not row.replaced_by:  # signed out at that moment, not rotated
                    _no_session()
        if row.replaced_by != successor:
            _grace_reuse(s, row)
        new_raw = _add_refresh(s, user.id, row.family_id, successor)
    return _session_response(user, new_raw)


def _backfill_net(s, user: User) -> None:
    """Accounts made before the per-network cap (migration 0003) have no signup_net, so they skipped that cap
    (audit F04). They get the network they sign in or refresh from, once."""
    if user.signup_net is None:
        user.signup_net = client_network()
        s.execute(update(User).where(User.id == user.id, User.signup_net.is_(None)).values(signup_net=user.signup_net))


def _grace_reuse(s, row: RefreshToken) -> None:
    """A rotated token used again within REUSE_GRACE_MS (two tabs refreshing together) is accepted once more,
    not again and again: a copied cookie can't mint any number of sessions (audit F21). The extra request
    gets a 401 but keeps its cookie, which the tab that won already replaced."""
    issued = s.scalar(select(func.count()).select_from(RefreshToken).where(
        RefreshToken.family_id == row.family_id, RefreshToken.id != row.id, RefreshToken.created_at >= row.revoked_at)) or 0
    if issued >= 2:  # the successor, plus the one grace reuse
        log.warning("refresh grace window reused again for user %s; refused", row.user_id)
        raise ApiError(401, "Please sign in.", "no_session")


@bp.post("/logout")
def logout() -> Response:
    rate_limit(REFRESH_LIMIT, "logout", client_ip(), "Too many requests. Please wait a minute.")
    raw = request.cookies.get(COOKIE, "")
    if raw:
        with ctx().db.session() as s:
            s.execute(update(RefreshToken).where(RefreshToken.token_hash == hash_token(raw), RefreshToken.revoked_at.is_(None)).values(revoked_at=now_ms()))
    return _clear_cookie(json_response({"ok": True}))


def _end_all_sessions(user_id: str) -> None:
    with ctx().db.session() as s:
        s.execute(update(User).where(User.id == user_id).values(token_version=User.token_version + 1))
        s.execute(update(RefreshToken).where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)).values(revoked_at=now_ms()))


@bp.post("/logout-all")
@require_auth
def logout_all() -> Response:
    _end_all_sessions(g.user.id)
    return _clear_cookie(json_response({"ok": True}))


@bp.get("/me")
@require_auth
def me() -> Response:
    c = ctx()
    return json_response({"user": public_user(g.user), "allowance": quota.allowance(c.settings, c.db, g.user.id)})


@bp.post("/password")
@require_auth
def change_password() -> Response:
    rate_limit(ACCOUNT_LIMIT, "account", g.user.id, "Too many account changes. Please try again later.")
    if not ctx().settings.password_auth:
        _password_off()
    body = validate(ChangePassword, json_body())
    if g.user.password_hash is None:
        raise ApiError(422, "This account signs in with Google, so it has no password to change.", "no_password")
    if not verify_password(body.currentPassword, g.user.password_hash):
        raise ApiError(403, "Your current password is wrong.", "bad_credentials", {"currentPassword": "Wrong password."})
    if body.currentPassword == body.newPassword:
        raise ApiError(422, "The new password must be different.", "invalid", {"newPassword": "Same as the current one."})
    hashed = hash_password(body.newPassword)
    # One transaction (audit F19): the new password, signing out every other device (and anyone with the old
    # password) and this device's new session land together, or not at all.
    with ctx().db.session() as s:
        user = s.get(User, g.user.id)
        if user is None:  # the account was deleted from another device meanwhile
            _no_session()
        user.password_hash = hashed
        user.token_version = user.token_version + 1
        s.execute(update(RefreshToken).where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None)).values(revoked_at=now_ms()))
        s.flush()
        raw = _add_refresh(s, user.id)
    return _session_response(user, raw)  # this device stays signed in


@bp.delete("/me")
@require_auth
def delete_account() -> Response:
    rate_limit(ACCOUNT_LIMIT, "account", g.user.id, "Too many account changes. Please try again later.")
    body = validate(DeleteAccount, json_body())
    # Confirmed by typing the account's email (everyone signs in with Google now); a password account's
    # password also works.
    if body.email is not None:
        if body.email != g.user.email:
            raise ApiError(403, "That isn't this account's email.", "bad_credentials", {"email": "Type this account's email."})
    elif body.password is None or not verify_password(body.password, g.user.password_hash):
        raise ApiError(403, "Type this account's email to confirm.", "bad_credentials", {"email": "Type this account's email."})
    with ctx().db.session() as s:
        user = s.get(User, g.user.id)
        if user:
            # History and sessions go with it (ON DELETE CASCADE). Usage rows stay, with user_id
            # set to NULL, so the spend still counts toward the global daily cap (audit F01).
            s.delete(user)
    log.info("deleted account %s", g.user.id)
    return _clear_cookie(json_response({"ok": True}))
