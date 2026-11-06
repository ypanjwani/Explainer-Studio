# Plan: account verification and sign-up abuse (before SaaS v1)

Written Nov 5 2026. **Status: step 1 (Google sign-in) built Nov 5 2026**, with tests (`server/tests/test_google_auth.py`)
and migration 0007; a real sign-in in the browser is still to be tried. Choices confirmed by the user: link an existing
password account by email, keep the per-network sign-up cap for Google sign-ups. **Password sign-in removed right away
(Nov 5 2026, user's call)** instead of after 2 weeks: no password option in the UI; routes off unless `PASSWORD_AUTH=1`.

**After deploying:** add `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` in Render (the container migrates the DB on
start), then in Google Console fill Branding (home page, privacy link `https://explainer-studio-1.onrender.com/privacy.html`,
authorized domain `explainer-studio-1.onrender.com`) and Publish app. Tell early testers to use "Continue with
Google" with the same email (testers whose email isn't a Google account can't sign in any more).
Context: v1 is deployed on Render for validation (no payments). Accounts are email + password with **no email
verification** (CLAUDE.md says "by design"; this plan changes that decision).

## Decision (Nov 5 2026): v1 is Google sign-in only

Google verifies the email and handles password recovery, so for v1:

| Step | v1 |
|---|---|
| 1. Continue with Google | **Build now.** The only way to sign in or create an account. |
| 2. Email code + Forgot password | **Dropped for v1.** Not needed without passwords. Maybe later, if users ask for email sign-up. |
| 3. Turnstile | **Optional, later.** Google accounts already make fake sign-ups costly. Add only if abuse shows up. |
| 4. Disposable emails / Gmail aliases | **Dropped.** Google accounts can't be throwaway domains; one Google account = one address. |
| 5. Anthropic console spend limit | **Still do** (user, in the console). |

Changes to step 1 because of Google-only:
- **Password sign-up switched off** (`/api/auth/register` refused; no "Create account" tab).
- **Password sign-in kept for existing testers** for ~2 weeks (small "Sign in with password" link), then removed with
  "Change password".
- **Same email = same account:** a Google sign-in with an existing account's email opens that account (Google says it
  is verified). Sets `google_sub` and `email_verified_at` on it.
- **Delete account:** Google accounts have no password, so they confirm by typing their email.
- **Email enumeration (F34)** goes away with the register route.
- **State cookie must be `SameSite=Lax`** (the refresh cookie is Strict, which the browser does not send on the
  cross-site return from Google, so the state check would always fail).
- **No new dependency:** code exchange + ID token check with `httpx` (the ID token comes straight from Google's
  token endpoint over TLS; still check `iss`, `aud`, `exp`, `email_verified`).
- **A `/privacy` page** in the app: Google needs a privacy policy link before the app can be published.

### Google Cloud Console (done by the user, Nov 5 2026)
- Project **ClariTale**; Google Auth Platform set up as **External**; scopes `openid`, `email`, `profile` only; no logo
  (a logo forces verification).
- Web OAuth client created. Redirect URIs:
  - `http://localhost:5173/api/auth/google/callback` (dev: open the app at `localhost`, not `127.0.0.1`)
  - `https://explainer-studio-1.onrender.com/api/auth/google/callback` (Render)
- `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` are in the local `.env`. Not yet in Render (add after the code ships).
- **Still in Testing:** "Publish app" is greyed out until Branding is complete (likely the privacy policy link). Until
  then only listed test users can sign in (Audience → Test users). After `/privacy` is deployed: fill Branding (home
  page + privacy link + authorized domain `explainer-studio-1.onrender.com`), then Publish app. No Google review is
  needed for these basic scopes.

## What the code does today (checked Nov 5 2026)

- **Email check:** `server/explainer/schemas.py` `normalize_email()`: format check only (`check_deliverability=False`),
  trimmed + lower-cased. Gmail `+tag` and dot variants count as separate accounts. No disposable-domain block.
- **Sign-up limits** (`server/explainer/routes/auth.py` `register()`):
  - `REGISTER_LIMIT` 5/hour per IP.
  - `NETWORK_DAILY_SIGNUPS` (20/day) per network: IPv4 /24, IPv6 /48 (`middleware.network_of`).
- **Spend caps** (`server/explainer/quota.py`, `config.py`, `render.yaml`):
  - `FREE_DAILY_USD` $0.25 per user/day, `FREE_DAILY_GENERATIONS` 10.
  - `NETWORK_DAILY_USD` $2 per network/day (all accounts made from that network).
  - `GLOBAL_DAILY_USD` $10 for the whole app/day.
- **Routes:** register, login, refresh, logout, logout-all, me, password change, account delete. **No password reset.**
- Register answers 409 `email_taken`, so anyone can check if an email is registered (audit F34, known trade-off).

## The problems

1. **Fake/unlimited accounts:** anyone switching networks (VPN, mobile data) gets 20 new accounts and $2 of spend
   each time. ~5 networks use up the $10/day global cap, so **real users are blocked until 00:00 UTC** (a cheap
   daily denial of service). The bill itself stays capped.
2. **Email squatting:** someone can register `friend@gmail.com` first; the real owner gets "already registered".
3. **No "Forgot password":** nothing verified to send a reset to, so a forgotten password = lost account.
4. **Email enumeration** through the 409 on register.
5. **Validation data is noisy:** sign-up counts can't be trusted.

## Plan (in order)

Original plan below, kept for reference. **For v1 see "Decision" above:** only steps 1 and 5.

### 1. "Continue with Google" sign-in (medium)
- Google OAuth (OpenID Connect). Google has verified the email, so no email service is needed. Most learners/students have Gmail.
- Server: new routes, e.g. `GET /api/auth/google/start` (state + PKCE, state in a short-lived signed cookie) and
  `GET /api/auth/google/callback`. Check the ID token (issuer, audience, expiry, `email_verified: true`), then find or
  create the user and issue the same session as `_issue()` / `_session_response()`.
- DB: an Alembic migration adding e.g. `users.google_sub` (unique, nullable), `users.email_verified_at` (nullable),
  and making `password_hash` nullable for Google-only accounts.
- Existing email+password account with the same email: link only because Google says the email is verified
  (that verifies it). Decide whether to ask the user first.
- New env vars: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` (secret: Render dashboard, `sync: false` in
  `render.yaml`, add to `.env.example`). Callback URL must match the Render URL.
- Frontend: a "Continue with Google" button on the sign-in screen (`app/src/shell/account.ts` `authView`).
- Check CSP / `middleware.py` origin rules still allow the redirect flow (the callback is a top-level GET).

### 2. Email verification code for email + password sign-ups (medium)
- On register: create the account as **unverified** and email a 6-digit code (expires ~15 min; store only a hash;
  max ~5 wrong tries; resend limited e.g. 3/hour).
- **Unverified accounts can't generate or chat** (check in `quota.py` / `routes/ai.py`). They can sign in and see
  "Enter the code we sent".
- Email service: e.g. Resend (free tier) or Postmark / AWS SES. Needs a sending domain with SPF/DKIM.
  New env var e.g. `RESEND_API_KEY` (secret; Render dashboard + `.env.example`). Dev: log the code instead of sending.
- Same mail path then gives **"Forgot password"** (code or link, single use, expires, ends all sessions like
  `_end_all_sessions`).
- With verification, register can answer generically ("check your email") and fix the enumeration (audit F34).
- Unverified accounts older than e.g. 7 days: delete, so squatted emails free up.

### 3. Cloudflare Turnstile on sign-up (small)
- Free, mostly invisible CAPTCHA. Widget on the register form; server checks the token with Cloudflare's
  siteverify API before creating the account (fail closed in production).
- Env vars: `TURNSTILE_SITE_KEY` (public, the frontend needs it, e.g. served by an API or a `VITE_` var) and
  `TURNSTILE_SECRET_KEY` (secret).
- CSP: allow `challenges.cloudflare.com` (script + frame) on the app page (`middleware.py` sets the CSP).
- Optionally on login after a few failures.

### 4. Block disposable emails and alias tricks (small)
- Reject known throwaway domains (a maintained list, e.g. the `disposable-email-domains` package/list).
- For the account-count checks, treat Gmail `a.b+x@gmail.com` as `ab@gmail.com` (keep the address the user typed
  for sending mail). Probably a separate `email_canonical` column (unique) rather than changing `email`.

### 5. Anthropic console spend limit + alert (settings only, no code)
- A backstop if the app's own cost tracking is wrong. The user does this in the Anthropic console.
- Also decide if `GLOBAL_DAILY_USD` = $10/day (up to ~$300/month) is OK.

## When building

- Update `.claude/CLAUDE.md` (the "No email/password verification by design" line under Auth) and `.env.example`.
- Add pytest tests in `server/tests/` for each new route (verified/unverified, expired code, wrong code limits,
  Google token checks with a faked verifier).
- Don't add Claude attribution to commits (user preference).
- Show the user the plan step by step; they decide what goes in v1.
