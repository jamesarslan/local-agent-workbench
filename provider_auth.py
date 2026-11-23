"""OpenRouter browser authorization using a one-use loopback callback and PKCE."""

import base64
import hashlib
import secrets
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

import requests

from connections import store_key, vault_status

CALLBACK_TIMEOUT = 5


def connect_openrouter(connection=None):
    connection = connection or {"provider": "OpenRouter"}
    if not isinstance(connection, dict) or connection.get("provider") != "OpenRouter":
        raise ValueError("Choose an OpenRouter connection before signing in.")
    if not vault_status()["available"]:
        raise ValueError(
            "Sign-in needs a system credential vault to keep the new key, and none is "
            "available. Create an OpenRouter API key and set OPENROUTER_API_KEY before "
            "starting Agent Workbench."
        )
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    path = "/authorize/" + secrets.token_urlsafe(24)
    codes = []

    class Callback(BaseHTTPRequestHandler):
        timeout = CALLBACK_TIMEOUT

        def log_message(self, *args):
            pass

        def do_GET(self):
            target = urlsplit(self.path)
            if target.path != path:
                self.send_error(404)
                return
            params = parse_qs(target.query)
            codes.append(params.get("code", [""])[0])
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(
                b"Return to Agent Workbench to see your connection status."
            )

    with HTTPServer(("127.0.0.1", 0), Callback) as server:
        server.timeout = 1
        callback = f"http://localhost:{server.server_port}{path}"
        query = urlencode(
            dict(
                callback_url=callback,
                code_challenge=challenge,
                code_challenge_method="S256",
                key_label="Agent Workbench",
            )
        )
        if not webbrowser.open("https://openrouter.ai/auth?" + query):
            raise ValueError(
                "Could not open the default browser. Use the API key option."
            )
        deadline = time.monotonic() + 180
        while not codes and time.monotonic() < deadline:
            server.handle_request()
    if not codes or not codes[0]:
        raise ValueError("Sign-in expired or was declined. You can try again.")
    try:
        r = requests.post(
            "https://openrouter.ai/api/v1/auth/keys",
            json=dict(
                code=codes[0], code_verifier=verifier, code_challenge_method="S256"
            ),
            timeout=20,
            allow_redirects=False,
        )
        r.raise_for_status()
        answer = r.json()
    except (requests.RequestException, ValueError):
        raise ValueError(
            "OpenRouter did not complete the sign-in. Check your internet connection "
            "and try again, or paste an API key instead."
        ) from None
    token = answer.get("key") if isinstance(answer, dict) else None
    if not isinstance(token, str) or not token:
        raise ValueError(
            "OpenRouter did not return an API key. Try signing in again, or paste an "
            "API key instead."
        )
    return store_key(connection, token)
