"""Share links: a public, read-only copy of one explainer that anyone with the link can open (no sign-in).

  POST   /api/share        {recordId, mode?: "light" | "dark"} -> {id, path, createdAt, updatedAt}
                           Copies the explainer's spec from the reader's saved history (never a spec sent by the
                           browser). Sharing the same explainer again updates the copy and keeps the id/link.
  GET    /api/share/<id>   -> the same fields (the owner only)
  DELETE /api/share/<id>   -> {ok}: the link stops working (the owner only)
  GET    /s/<id>           the shared page: the spec plus the standalone viewer (/viewer.js, the one "Export HTML" uses)

Deleting the explainer (history.remove) or the account deletes its share too.

Security: /s/<id> is sent with a CSP `sandbox` (no allow-same-origin), so the page runs in an opaque origin. Even a
hand-crafted spec can't read the app's refresh cookie or call /api as the person viewing it. Widgets already run in
their own sandboxed iframes, and the viewer copes with blocked localStorage.
"""

from __future__ import annotations

import html
import json
import re
import secrets

from flask import Blueprint, Response, g
from limits import parse as parse_limit
from sqlalchemy import func, select

from ..db import Explainer, Share, now_ms
from ..js import json_stringify
from ..middleware import ApiError, client_ip, ctx, json_body, json_response, rate_limit, require_auth
from .history import RECORD_MAX_BYTES

bp = Blueprint("share", __name__, url_prefix="/api/share")
page_bp = Blueprint("share_page", __name__)

WRITE_LIMIT = parse_limit("30/minute")
READ_LIMIT = parse_limit("60/minute")
VIEW_LIMIT = parse_limit("120/minute")  # per IP: the page is public
USER_MAX_SHARES = 200
_ID = re.compile(r"^[A-Za-z0-9_-]{8,16}$")

# Keep the same as FONTS_URL / KATEX_CSS in app/src/render/export.ts.
FONTS_URL = ("https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;600"
             "&family=Fraunces:opsz,wght@9..144,500;9..144,700&family=Space+Grotesk:wght@400;500;600;700"
             "&family=IBM+Plex+Mono:wght@400;600&family=Nunito:wght@400;600;700;800&family=Fira+Code:wght@400;600&display=swap")
KATEX_CSS = "https://cdn.jsdelivr.net/npm/katex@0.19.0/dist/katex.min.css"

PAGE_CSP = ("sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox; "
            "frame-ancestors 'none'; object-src 'none'; base-uri 'none'; form-action 'none'")


def _check_id(share_id: str) -> None:
    if not _ID.match(share_id):
        raise ApiError(404, "Not found.")


def _out(row: Share) -> dict:
    return {"id": row.id, "path": f"/s/{row.id}", "createdAt": row.created_at, "updatedAt": row.updated_at}


def _shared_spec(data: dict, mode: object) -> dict:
    """The record's spec, with the light/dark mode on screen when the page has none of its own (as Export does)."""
    spec = data.get("spec")
    if not isinstance(spec, dict) or not isinstance(spec.get("title"), str) or not isinstance(spec.get("stages"), list):
        raise ApiError(409, "This explainer isn't finished yet, so it can't be shared.", "not_ready")
    design = spec.get("design") if isinstance(spec.get("design"), dict) else {}
    if design.get("mode") not in ("light", "dark") and mode in ("light", "dark"):
        spec = {**spec, "design": {**design, "mode": mode}}
    return spec


@bp.post("")
@require_auth
def create() -> Response:
    rate_limit(WRITE_LIMIT, "share-write", g.user.id, "Too many requests. Please wait a minute.")
    body = json_body()
    record_id = body.get("recordId") if isinstance(body, dict) else None
    if not isinstance(record_id, str) or not record_id or len(record_id) > 64:
        raise ApiError(400, "Say which explainer to share.", "invalid")
    now = now_ms()
    with ctx().db.session() as s:
        rec = s.get(Explainer, (g.user.id, record_id))
        if rec is None:
            raise ApiError(404, "That explainer isn't saved yet. Try again in a moment.")
        spec = _shared_spec(rec.data, body.get("mode"))
        size = len(json_stringify(spec).encode("utf-8"))
        if size > RECORD_MAX_BYTES:
            raise ApiError(413, "This explainer is too large to share.", "too_large")
        row = s.execute(select(Share).where(Share.user_id == g.user.id, Share.record_id == record_id)).scalar_one_or_none()
        if row is None:
            count = s.execute(select(func.count()).select_from(Share).where(Share.user_id == g.user.id)).scalar_one()
            if count >= USER_MAX_SHARES:
                raise ApiError(409, f"You can share at most {USER_MAX_SHARES} explainers. Stop sharing one first.", "too_many")
            row = Share(id=secrets.token_urlsafe(9), user_id=g.user.id, record_id=record_id, created_at=now)
            s.add(row)
        row.title = str(spec["title"])[:200]
        row.spec, row.size, row.updated_at = spec, size, now
        s.flush()
        return json_response(_out(row))


@bp.get("/<share_id>")
@require_auth
def status(share_id: str) -> Response:
    _check_id(share_id)
    rate_limit(READ_LIMIT, "share-read", g.user.id, "Too many requests. Please wait a minute.")
    with ctx().db.session() as s:
        row = s.get(Share, share_id)
        if row is None or row.user_id != g.user.id:
            raise ApiError(404, "Not found.")
        return json_response(_out(row))


@bp.delete("/<share_id>")
@require_auth
def remove(share_id: str) -> Response:
    _check_id(share_id)
    rate_limit(WRITE_LIMIT, "share-write", g.user.id, "Too many requests. Please wait a minute.")
    with ctx().db.session() as s:
        row = s.get(Share, share_id)
        if row is None or row.user_id != g.user.id:
            raise ApiError(404, "Not found.")
        s.delete(row)
    return json_response({"ok": True})


# ---------- the public page ----------


def _page(body: str, status: int = 200, title: str = "ClariTale") -> Response:
    doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{html.escape(title)}</title>
{body}
</html>
"""
    return Response(doc, status=status, mimetype="text/html",
                    headers={"Content-Security-Policy": PAGE_CSP, "Cache-Control": "no-cache"})


def _message(text: str, status: int) -> Response:
    return _page(f"""<style>body{{font:16px/1.5 system-ui,sans-serif;margin:0;display:grid;place-items:center;min-height:100vh;
background:#faf7f2;color:#2b2420}}@media (prefers-color-scheme:dark){{body{{background:#1c1917;color:#eee6dd}}}}
main{{max-width:28rem;padding:1rem;text-align:center}}</style>
</head>
<body><main><h1>ClariTale</h1><p>{html.escape(text)}</p></main></body>""", status)


@page_bp.get("/s/<share_id>")
def view(share_id: str) -> Response:
    try:
        rate_limit(VIEW_LIMIT, "share-view", client_ip(), "Too many requests.")
    except ApiError:
        return _message("Too many requests. Please wait a minute and reload.", 429)
    with ctx().db.session() as s:
        share = s.get(Share, share_id) if _ID.match(share_id) else None
        spec = share.spec if share else None
    if spec is None:
        return _message("This link doesn't work anymore. The person who shared it may have stopped sharing it.", 404)
    # ensure_ascii keeps U+2028/2029 out; "<" escaped so the JSON can't close the <script> tag.
    data = json.dumps(spec, ensure_ascii=True, separators=(",", ":")).replace("<", "\\u003c")
    return _page(f"""<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="{html.escape(FONTS_URL)}">
<link rel="stylesheet" href="{html.escape(KATEX_CSS)}">
</head>
<body>
<div id="root" class="scroll-host"></div>
<script>window.__SPEC__ = {data};</script>
<script src="/viewer.js"></script>
</body>""", title=str(spec.get("title") or "ClariTale"))
