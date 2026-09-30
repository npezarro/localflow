"""Sign in with Google (for account sync).

The standard flow for desktop apps: open the browser at Google's sign-in page, receive the
answer on a one-off http://127.0.0.1:<port> address, and exchange it (with PKCE) for tokens.
LocalFlow asks only for your email address and ``drive.appdata``: a hidden folder in your own
Google Drive that only LocalFlow can see. It can't read any of your other files.

The refresh token is kept in the OS credential store (see keystore.py), never in the data
folder. The OAuth client comes from the build (``_oauth_client.py``, generated in CI from
repository secrets), or for development from ``google_oauth.json`` in the data folder (the
"Desktop app" JSON downloaded from Google Cloud Console).
"""
import base64
import hashlib
import http.server
import json
import logging
import os
import secrets
import threading
import time
import urllib.parse
import urllib.request
import webbrowser

from . import keystore, paths

log = logging.getLogger(__name__)
SCOPES = "openid email https://www.googleapis.com/auth/drive.appdata"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REFRESH_KEY = "google_refresh_token"


class SignInError(RuntimeError):
    pass


def client():
    """(client_id, client_secret) or None when this build has no Google sign-in configured."""
    try:
        from . import _oauth_client  # generated at build time

        if _oauth_client.CLIENT_ID:
            return _oauth_client.CLIENT_ID, _oauth_client.CLIENT_SECRET
    except ImportError:
        pass
    path = os.path.join(paths.data_dir(), "google_oauth.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        inst = data.get("installed") or data
        return inst["client_id"], inst["client_secret"]
    except (OSError, ValueError, KeyError):
        return None


def available():
    return client() is not None


def _post(url, fields, timeout=30):
    body = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        try:
            detail = json.load(exc)
        except Exception:
            detail = {}
        raise SignInError(detail.get("error_description") or detail.get("error") or str(exc)) from None


def _email_from_id_token(id_token):
    try:
        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("email", "")
    except Exception:
        return ""


def pkce_pair():
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


_DONE_PAGE = (b"<html><body style='font-family:sans-serif;padding:40px'><h2>%s</h2>"
              b"<p>You can close this tab and go back to LocalFlow.</p></body></html>")


def sign_in(open_browser=webbrowser.open, timeout=300):
    """Run the browser sign-in. Returns the account's email address. Raises SignInError."""
    creds = client()
    if not creds:
        raise SignInError("this copy of LocalFlow has no Google sign-in configured")
    client_id, client_secret = creds
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(16)
    result = {}
    done = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if query.get("state", [""])[0] != state:
                self.send_response(400)
                self.end_headers()
                return  # a stray request (favicon, another tab): keep waiting
            result.update({k: v[0] for k, v in query.items()})
            ok = "code" in result
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(_DONE_PAGE % (b"Signed in." if ok else b"Sign-in was cancelled."))
            done.set()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    redirect = "http://127.0.0.1:%d/" % server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True, name="oauth-callback").start()
    try:
        url = AUTH_URL + "?" + urllib.parse.urlencode({
            "client_id": client_id, "redirect_uri": redirect, "response_type": "code", "scope": SCOPES,
            "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
            "access_type": "offline", "prompt": "consent select_account"})
        open_browser(url)
        if not done.wait(timeout):
            raise SignInError("sign-in timed out")
    finally:
        server.shutdown()
        server.server_close()
    if "code" not in result:
        raise SignInError(result.get("error", "sign-in was cancelled"))
    tokens = _post(TOKEN_URL, {"code": result["code"], "client_id": client_id, "client_secret": client_secret,
                               "redirect_uri": redirect, "grant_type": "authorization_code",
                               "code_verifier": verifier})
    if "refresh_token" not in tokens:
        raise SignInError("Google didn't return a refresh token")
    keystore.set(REFRESH_KEY, tokens["refresh_token"])
    Session.shared().set_access(tokens)
    return _email_from_id_token(tokens.get("id_token", ""))


def sign_out():
    token = keystore.get(REFRESH_KEY)
    keystore.set(REFRESH_KEY, "")
    Session.shared().set_access({})
    if token:
        try:  # also revoke it on Google's side; fine if offline
            _post("https://oauth2.googleapis.com/revoke", {"token": token}, timeout=10)
        except Exception:
            pass


def signed_in():
    return bool(keystore.get(REFRESH_KEY))


class Session:
    """Keeps a fresh access token."""
    _shared = None

    @classmethod
    def shared(cls):
        if cls._shared is None:
            cls._shared = cls()
        return cls._shared

    def __init__(self):
        self._token, self._expires = None, 0.0
        self._lock = threading.Lock()

    def set_access(self, tokens):
        self._token = tokens.get("access_token")
        self._expires = time.time() + float(tokens.get("expires_in", 0)) - 60

    def token(self):
        with self._lock:
            if self._token and time.time() < self._expires:
                return self._token
            refresh = keystore.get(REFRESH_KEY)
            creds = client()
            if not refresh or not creds:
                raise SignInError("not signed in")
            try:
                tokens = _post(TOKEN_URL, {"refresh_token": refresh, "client_id": creds[0],
                                           "client_secret": creds[1], "grant_type": "refresh_token"})
            except SignInError as exc:
                if "invalid_grant" in str(exc) or "expired" in str(exc).lower() or "revoked" in str(exc).lower():
                    keystore.set(REFRESH_KEY, "")
                    raise SignInError("your Google sign-in expired; sign in again") from None
                raise
            self.set_access(tokens)
            return self._token
