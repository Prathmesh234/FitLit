"""Least-privilege OAuth for the FitLit Gmail notification service."""
from __future__ import annotations

import json
import os
import time
import urllib.parse

from fitlit import auth, config

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None


class GmailAuthError(RuntimeError):
    """Raised when the Gmail send-only token cannot be obtained."""


def _read_cache() -> dict:
    try:
        with open(config.GMAIL_TOKEN_STATE) as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def _write_cache(access_token: str, expires_at: float) -> None:
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = config.GMAIL_TOKEN_STATE.with_suffix(".json.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump({"access_token": access_token, "expires_at": expires_at}, fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, config.GMAIL_TOKEN_STATE)


def _is_fresh(cache: dict, now: float) -> bool:
    return bool(cache.get("access_token")) and now < float(cache.get("expires_at", 0)) - 60


def is_configured() -> bool:
    return bool(
        config.OAUTH_CLIENT_ID
        and config.OAUTH_CLIENT_SECRET
        and config.GMAIL_REFRESH_TOKEN
        and config.GMAIL_TO
    )


def get_access_token(*, force_refresh: bool = False) -> str:
    now = time.time()
    if not force_refresh:
        cache = _read_cache()
        if _is_fresh(cache, now):
            return cache["access_token"]
    if not (config.OAUTH_CLIENT_ID and config.OAUTH_CLIENT_SECRET and config.GMAIL_REFRESH_TOKEN):
        raise GmailAuthError(
            "Gmail OAuth is not configured; set GMAIL_REFRESH_TOKEN after running "
            "`uv run python scripts/oauth_capture.py --gmail`."
        )

    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock = open(config.GMAIL_TOKEN_STATE.with_suffix(".json.lock"), "a+")
    try:
        if fcntl is not None:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if not force_refresh:
            cache = _read_cache()
            if _is_fresh(cache, time.time()):
                return cache["access_token"]
        try:
            response = auth._post_token({
                "client_id": config.OAUTH_CLIENT_ID,
                "client_secret": config.OAUTH_CLIENT_SECRET,
                "refresh_token": config.GMAIL_REFRESH_TOKEN,
                "grant_type": "refresh_token",
            })
        except auth.AuthError as exc:
            raise GmailAuthError(str(exc)) from exc
        token = response.get("access_token")
        if not token:
            raise GmailAuthError("Gmail token response contained no access_token")
        _write_cache(token, time.time() + float(response.get("expires_in", 3600)))
        return token
    finally:
        if fcntl is not None:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


def build_consent_url(state: str | None = None) -> str:
    """Request only gmail.send while reusing the existing OAuth client."""
    params = {
        "client_id": config.OAUTH_CLIENT_ID,
        "redirect_uri": config.OAUTH_REDIRECT_URI,
        "response_type": "code",
        "access_type": "offline",
        "prompt": "consent",
        "scope": config.GMAIL_SEND_SCOPE,
    }
    if state:
        params["state"] = state
    return f"{config.OAUTH_AUTH_URI}?{urllib.parse.urlencode(params)}"


def exchange_code(code: str) -> dict:
    try:
        return auth._post_token({
            "client_id": config.OAUTH_CLIENT_ID,
            "client_secret": config.OAUTH_CLIENT_SECRET,
            "code": code.strip(),
            "grant_type": "authorization_code",
            "redirect_uri": config.OAUTH_REDIRECT_URI,
        })
    except auth.AuthError as exc:
        raise GmailAuthError(str(exc)) from exc
