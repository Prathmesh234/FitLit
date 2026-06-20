"""One-shot OAuth callback catcher for headless / remote (VM) onboarding.

Runs a tiny HTTP server on 127.0.0.1:8765 that waits for Google's
``/callback?code=...`` redirect, exchanges the code for tokens, and writes
``GOOGLE_HEALTH_REFRESH_TOKEN`` into ``.env`` — no copy-paste of the code needed.

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
import re
import socketserver
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fitlit import auth, config

HOST, PORT = "127.0.0.1", 8765
_result: dict = {}


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        parsed = urllib.parse.urlparse(self.path)
        if not parsed.path.startswith("/callback"):
            self.send_response(404)
            self.end_headers()
            return
        params = urllib.parse.parse_qs(parsed.query)
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


def _update_env(refresh_token: str) -> None:
    """Insert/replace GOOGLE_HEALTH_REFRESH_TOKEN in .env."""
    env_path = Path(config.BASE_DIR) / ".env"
    line = f"GOOGLE_HEALTH_REFRESH_TOKEN={refresh_token}"
    text = env_path.read_text() if env_path.exists() else ""
    if re.search(r"(?m)^\s*GOOGLE_HEALTH_REFRESH_TOKEN=", text):
        text = re.sub(r"(?m)^\s*GOOGLE_HEALTH_REFRESH_TOKEN=.*$", line, text)
    else:
        text = text.rstrip("\n") + ("\n" if text else "") + line + "\n"
    env_path.write_text(text)


def main() -> int:
    if not (config.OAUTH_CLIENT_ID and config.OAUTH_CLIENT_SECRET):
        print("Set GOOGLE_HEALTH_CLIENT_ID and GOOGLE_HEALTH_CLIENT_SECRET in .env first.")
        return 1

    print("\n1. On your LAPTOP, open this URL in a browser and approve:\n")
    print("   " + auth._build_consent_url() + "\n")
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
        resp = auth._exchange_code(_result["code"])
    except auth.AuthError as exc:
        print(f"❌ exchange failed: {exc}")
        return 1

    refresh_token = resp.get("refresh_token")
    if resp.get("access_token") and "expires_in" in resp:
        auth._write_cache(resp["access_token"], time.time() + float(resp["expires_in"]))
    if not refresh_token:
        print("❌ No refresh_token returned (already consented before?). Revoke at "
              "https://myaccount.google.com/permissions and retry.")
        return 1

    _update_env(refresh_token)
    print("\n✅ Done. GOOGLE_HEALTH_REFRESH_TOKEN written to .env and access token cached.")
    print("   The fetchers will now mint + refresh tokens automatically.")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
