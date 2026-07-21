"""The interactive OAuth login that unlocks unblurred mature content."""

import sys
from urllib.parse import parse_qs, urlencode, urlparse

from .api import DeviantArtClient
from .constants import AUTH_URL, REDIRECT_PORT, REDIRECT_URI


def login(client: DeviantArtClient):
    """Interactive OAuth login (Authorization Code grant).

    Opens the browser so the user authorizes the app, receives the code on
    a local HTTP server and saves the refresh token for future runs. With a
    user session, mature deviations are served unblurred (as long as the
    account has mature content enabled in its settings).
    """
    import base64
    import hashlib
    import secrets
    import webbrowser
    from http.server import BaseHTTPRequestHandler, HTTPServer

    state = secrets.token_urlsafe(16)
    # PKCE (required by DeviantArt): S256 challenge derived from a one-off verifier
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    result: dict[str, str] = {}

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self):
            params = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
            ok = params.get("state") == state and "code" in params
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<h2>Login complete, you can close this tab.</h2>" if ok
                else b"<h2>Login failed, check the terminal.</h2>"
            )
            result.update(params)

        def log_message(self, *args):
            pass

    auth_url = AUTH_URL + "?" + urlencode({
        "response_type": "code",
        "client_id": client.client_id,
        "redirect_uri": REDIRECT_URI,
        "scope": "browse",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    })
    print(
        "A browser window will open so you can authorize the application.\n"
        f"If it does not open, visit:\n  {auth_url}\n\n"
        f"NOTE: the app must list {REDIRECT_URI} in its 'OAuth2 Redirect URI\n"
        "Whitelist' (https://www.deviantart.com/developers/apps).\n"
    )
    server = HTTPServer(("127.0.0.1", REDIRECT_PORT), Callback)
    try:
        webbrowser.open(auth_url)
        while not result:
            server.handle_request()
    finally:
        server.server_close()

    if result.get("state") != state or "code" not in result:
        sys.exit(f"Authorization failed: {result.get('error_description') or result}")

    data = client._token_request(
        {
            "grant_type": "authorization_code",
            "code": result["code"],
            "redirect_uri": REDIRECT_URI,
            "code_verifier": code_verifier,
        },
        "Could not exchange the authorization code.",
    )
    client.save_user_token(data)
    print("Login successful; the session was saved for future runs.\n")
