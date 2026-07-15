"""OAuth 2.0 token management for the Google Health API.

Google access tokens expire in ~1 hour; a long-lived **refresh token** mints
fresh ones.  This module is the single place that owns that dance so the rest of
the code can just ask for "a valid access token":

    from fitlit import auth
    token = auth.get_access_token()      # always valid, transparently refreshed

Design notes
------------
* **Shared cache.**  The eight fetchers run as separate processes, so the minted
  access token + its expiry are cached on disk (``data/state/token.json``) and
  guarded with the same ``fcntl`` exclusive lock the rate limiter uses.  Whoever
  refreshes first writes the file; the others read it back instead of each
  hammering Google's token endpoint.  The file is ``chmod 600`` — it holds a
  live credential.
* **Leeway.**  We refresh ``OAUTH_REFRESH_LEEWAY_SECONDS`` (default 60s) *before*
  the real expiry so a token never dies mid-request.
* **Fallback.**  With no refresh token configured we fall back to a static
  ``GOOGLE_HEALTH_ACCESS_TOKEN`` (handy for quick local testing); it just can't
  self-renew.
* **`login` CLI.**  ``python -m fitlit.auth login`` runs the one-time consent
  exchange (docs/DEPLOYMENT.md §2b) and prints the refresh token to paste into
  ``.env`` — so onboarding is one command instead of curl-by-hand.

Standard library only (urllib for HTTP, fcntl for the lock).
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from fitlit import config

log = logging.getLogger("fitlit.auth")

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None


class AuthError(RuntimeError):
    """Raised when no token can be obtained (no refresh token, refresh failed…)."""


# --------------------------------------------------------------------------- #
# Token cache (data/state/token.json), shared across the fetcher processes.
# --------------------------------------------------------------------------- #
def _read_cache() -> dict:
    try:
        with open(config.TOKEN_STATE) as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def _write_cache(access_token: str, expires_at: float) -> None:
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = config.TOKEN_STATE.with_suffix(".json.tmp")
    payload = {"access_token": access_token, "expires_at": expires_at}
    # Write 0600 from the start — never widen perms on a credential file.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(payload, fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, config.TOKEN_STATE)  # atomic swap


def _is_fresh(cache: dict, *, now: float) -> bool:
    token = cache.get("access_token")
    expires_at = float(cache.get("expires_at", 0.0))
    return bool(token) and now < expires_at - config.OAUTH_REFRESH_LEEWAY_SECONDS


# --------------------------------------------------------------------------- #
# Talking to Google's token endpoint.
# --------------------------------------------------------------------------- #
def _post_token(data: dict) -> dict:
    """POST form-encoded data to the OAuth token endpoint; return parsed JSON."""
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        config.OAUTH_TOKEN_URI,
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=config.REQUEST_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise AuthError(f"token endpoint {exc.code} {exc.reason}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise AuthError(f"token endpoint unreachable: {exc}") from exc


def _refresh(*, now: float) -> str:
    """Exchange the refresh token for a new access token, cache it, return it."""
    if not (config.OAUTH_CLIENT_ID and config.OAUTH_CLIENT_SECRET and config.OAUTH_REFRESH_TOKEN):
        raise AuthError(
            "no refresh token configured — set GOOGLE_HEALTH_CLIENT_ID, "
            "GOOGLE_HEALTH_CLIENT_SECRET and GOOGLE_HEALTH_REFRESH_TOKEN "
            "(run `python -m fitlit.auth login` to obtain the refresh token)."
        )
    resp = _post_token({
        "client_id": config.OAUTH_CLIENT_ID,
        "client_secret": config.OAUTH_CLIENT_SECRET,
        "refresh_token": config.OAUTH_REFRESH_TOKEN,
        "grant_type": "refresh_token",
    })
    access_token = resp.get("access_token")
    if not access_token:
        raise AuthError(f"refresh response had no access_token: {resp}")
    # expires_in is seconds-from-now; default to 3600 if Google omits it.
    expires_at = now + float(resp.get("expires_in", 3600))
    _write_cache(access_token, expires_at)
    log.info("refreshed access token (valid ~%ds)", int(expires_at - now))
    return access_token


# --------------------------------------------------------------------------- #
# Public API.
# --------------------------------------------------------------------------- #
def is_configured() -> bool:
    """True if a token can be obtained — either full OAuth refresh credentials
    or a static access token. Used by the readiness probe."""
    has_refresh = bool(
        config.OAUTH_CLIENT_ID and config.OAUTH_CLIENT_SECRET and config.OAUTH_REFRESH_TOKEN
    )
    return has_refresh or bool(config.ACCESS_TOKEN)


def get_access_token(*, force_refresh: bool = False, now: float | None = None) -> str:
    """Return a currently-valid access token, refreshing transparently.

    Resolution order:
      1. unless ``force_refresh``, a fresh cached token (shared across processes);
      2. a refresh-token exchange (under an exclusive lock, double-checking the
         cache so concurrent fetchers don't all hit Google at once);
      3. the static ``GOOGLE_HEALTH_ACCESS_TOKEN`` fallback, if that's all we have.

    Raises :class:`AuthError` if none of these yield a token.
    """
    now = time.time() if now is None else now

    if not force_refresh:
        cache = _read_cache()
        if _is_fresh(cache, now=now):
            return cache["access_token"]

    # No usable refresh credentials → fall back to the static token if present.
    if not (config.OAUTH_CLIENT_ID and config.OAUTH_CLIENT_SECRET and config.OAUTH_REFRESH_TOKEN):
        if config.ACCESS_TOKEN:
            return config.ACCESS_TOKEN
        raise AuthError(
            "no access token available — set GOOGLE_HEALTH_ACCESS_TOKEN for a "
            "one-off, or configure OAuth refresh (see docs/DEPLOYMENT.md §2)."
        )

    # Refresh under an exclusive lock so eight fetcher processes don't stampede
    # the token endpoint; re-check the cache once we hold the lock.
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock = open(config.TOKEN_STATE.with_suffix(".json.lock"), "a+")
    try:
        if fcntl is not None:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if not force_refresh:
            cache = _read_cache()
            if _is_fresh(cache, now=time.time()):
                return cache["access_token"]
        return _refresh(now=time.time())
    finally:
        if fcntl is not None:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


# --------------------------------------------------------------------------- #
# `login` — one-time consent exchange to capture a refresh token.
# --------------------------------------------------------------------------- #
def _build_consent_url(state: str | None = None) -> str:
    params = {
        "client_id": config.OAUTH_CLIENT_ID,
        "redirect_uri": config.OAUTH_REDIRECT_URI,
        "response_type": "code",
        "access_type": "offline",   # ← makes Google return a refresh_token
        "prompt": "consent",        # ← forces it even on re-consent
        "scope": " ".join(config.SCOPES),
    }
    if state:
        params["state"] = state
    return f"{config.OAUTH_AUTH_URI}?{urllib.parse.urlencode(params)}"


def _exchange_code(code: str) -> dict:
    return _post_token({
        "client_id": config.OAUTH_CLIENT_ID,
        "client_secret": config.OAUTH_CLIENT_SECRET,
        "code": code.strip(),
        "grant_type": "authorization_code",
        "redirect_uri": config.OAUTH_REDIRECT_URI,
    })


def login() -> int:
    """Interactive one-time flow: print the consent URL, take the redirect code,
    exchange it, and report the refresh token to save in ``.env``."""
    if not (config.OAUTH_CLIENT_ID and config.OAUTH_CLIENT_SECRET):
        print("Set GOOGLE_HEALTH_CLIENT_ID and GOOGLE_HEALTH_CLIENT_SECRET in .env first.")
        return 1

    print("\n1. Open this URL in a browser and approve access:\n")
    print("   " + _build_consent_url() + "\n")
    print(f"2. Google redirects to {config.OAUTH_REDIRECT_URI}?code=...")
    print("   Copy the `code` value from that URL.\n")
    try:
        code = input("3. Paste the code here: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\naborted.")
        return 1
    if not code:
        print("no code entered.")
        return 1

    try:
        resp = _exchange_code(code)
    except AuthError as exc:
        print(f"\nexchange failed: {exc}")
        return 1

    refresh_token = resp.get("refresh_token")
    access_token = resp.get("access_token")
    if access_token and "expires_in" in resp:
        _write_cache(access_token, time.time() + float(resp["expires_in"]))

    if not refresh_token:
        print("\nNo refresh_token in the response. This happens when you've "
              "consented before — revoke FitLit's access at "
              "https://myaccount.google.com/permissions and retry, or ensure the "
              "consent URL had access_type=offline & prompt=consent.")
        return 1

    print("\n✅ Success. Add this line to your .env:\n")
    print(f"   GOOGLE_HEALTH_REFRESH_TOKEN={refresh_token}\n")
    print("The access token has also been cached; fetchers will refresh it "
          "automatically from now on.")
    return 0


def main(argv: list[str] | None = None) -> int:
    import sys

    args = sys.argv[1:] if argv is None else argv
    cmd = args[0] if args else ""
    if cmd == "login":
        return login()
    if cmd == "token":  # print a valid access token (debug helper)
        try:
            print(get_access_token())
            return 0
        except AuthError as exc:
            print(f"error: {exc}")
            return 1
    print("usage: python -m fitlit.auth {login|token}")
    return 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
