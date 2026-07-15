"""One-shot OAuth callback catcher for headless / remote (VM) onboarding.

Runs a tiny HTTP server on 127.0.0.1:8765 that waits for Google's
``/callback?code=...`` redirect, exchanges the code for tokens, and writes the
selected refresh token into ``.env`` — no copy-paste of the code needed.

On a remote VM the browser is on your laptop, so bridge the redirect with an SSH
local-forward, then approve in your laptop browser:

    # on your laptop (new terminal):
    ssh -N -L 8765:localhost:8765 azureuser@<vm-ip>

    # on the VM:
    uv run python scripts/oauth_capture.py
    #   → prints the consent URL; open it in the laptop browser, approve,
    #     the redirect tunnels back here and the code is captured automatically.
"""
from __future__ import annotations

import http.server
import argparse
import re
import secrets
import socketserver
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fitlit import auth, config, gmail_auth

HOST, PORT = "127.0.0.1", 8765
_result: dict = {}
_expected_state = ""


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        parsed = urllib.parse.urlparse(self.path)
        if not parsed.path.startswith("/callback"):
            self.send_response(404)
            self.end_headers()
            return
        params = urllib.parse.parse_qs(parsed.query)
        returned_state = (params.get("state") or [""])[0]
        if not _expected_state or not secrets.compare_digest(returned_state, _expected_state):
            _result["error"] = "state_mismatch"
            self.send_response(400)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body><h2>Authorization state mismatch.</h2></body></html>")
            return
        _result["code"] = (params.get("code") or [""])[0]
        _result["error"] = (params.get("error") or [""])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        msg = ("✅ FitLit captured the authorization. You can close this tab and "
               "return to the terminal.") if _result["code"] else \
              ("❌ No code received: " + _result["error"])
        self.wfile.write(f"<html><body><h2>{msg}</h2></body></html>".encode())

    def log_message(self, *args) -> None:  # silence per-request logging
        pass


def _update_env(key: str, refresh_token: str) -> None:
    """Insert or replace one refresh-token variable in .env."""
    env_path = Path(config.BASE_DIR) / ".env"
    line = f"{key}={refresh_token}"
    text = env_path.read_text() if env_path.exists() else ""
    pattern = rf"(?m)^\s*{re.escape(key)}="
    if re.search(pattern, text):
        text = re.sub(rf"(?m)^\s*{re.escape(key)}=.*$", line, text)
    else:
        text = text.rstrip("\n") + ("\n" if text else "") + line + "\n"
    env_path.write_text(text)
    env_path.chmod(0o600)


def main(argv: list[str] | None = None) -> int:
    global _expected_state
    parser = argparse.ArgumentParser(description="Capture a Google OAuth refresh token")
    parser.add_argument(
        "--gmail",
        action="store_true",
        help="request gmail.send only and save GMAIL_REFRESH_TOKEN",
    )
    args = parser.parse_args(argv)
    if not (config.OAUTH_CLIENT_ID and config.OAUTH_CLIENT_SECRET):
        print("Set GOOGLE_HEALTH_CLIENT_ID and GOOGLE_HEALTH_CLIENT_SECRET in .env first.")
        return 1
    _expected_state = secrets.token_urlsafe(32)
    consent_url = (
        gmail_auth.build_consent_url(_expected_state)
        if args.gmail else auth._build_consent_url(_expected_state)
    )
    env_key = "GMAIL_REFRESH_TOKEN" if args.gmail else "GOOGLE_HEALTH_REFRESH_TOKEN"

    print("\n1. On your LAPTOP, open this URL in a browser and approve:\n")
    print("   " + consent_url + "\n")
    print(f"2. Waiting for the redirect on http://localhost:{PORT}/callback ...")
    print("   (bridge it with:  ssh -N -L 8765:localhost:8765 azureuser@<vm-ip>)\n")

    with socketserver.TCPServer((HOST, PORT), _Handler) as httpd:
        httpd.timeout = 1
        deadline = None
        while "code" not in _result and "error" not in _result:
            httpd.handle_request()  # blocks up to httpd.timeout
        # tiny grace so the browser receives the response page
        time.sleep(0.2)

    if not _result.get("code"):
        print(f"\n❌ Authorization failed: {_result.get('error') or 'no code'}")
        return 1

    print("\n→ got the code, exchanging for tokens ...")
    try:
        resp = gmail_auth.exchange_code(_result["code"]) if args.gmail else auth._exchange_code(_result["code"])
    except (auth.AuthError, gmail_auth.GmailAuthError) as exc:
        print(f"❌ exchange failed: {exc}")
        return 1

    refresh_token = resp.get("refresh_token")
    if resp.get("access_token") and "expires_in" in resp:
        cache_writer = gmail_auth._write_cache if args.gmail else auth._write_cache
        cache_writer(resp["access_token"], time.time() + float(resp["expires_in"]))
    if not refresh_token:
        print("❌ No refresh_token returned (already consented before?). Revoke at "
              "https://myaccount.google.com/permissions and retry.")
        return 1

    _update_env(env_key, refresh_token)
    print(f"\n✅ Done. {env_key} written to .env and access token cached.")
    print("   Restart the relevant FitLit service so it reloads .env.")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
