from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

def _load_dotenv_simple() -> None:
    env_file = Path(__file__).parent.parent / ".env"
    if not env_file.exists():
        return
    try:
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip()
                    # Remove surrounding quotes if present
                    if val and val[0] == val[-1] and val[0] in ('"', "'"):
                        val = val[1:-1]
                    if key and key not in os.environ:
                        os.environ[key] = val
    except OSError:
        pass

DEFAULT_AUTH_TIMEOUT = 180
DEFAULT_AUTH_PORT = 53682
SESSION_DIR_NAME = ".comm0ns_dashboard"
SESSION_FILE_NAME = "session.json"


class AuthError(RuntimeError):
    pass


class OAuthState:
    def __init__(self) -> None:
        self.event = threading.Event()
        self.payload: dict[str, Any] | None = None


def _to_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _session_file_path() -> Path:
    configured = os.getenv("TUI_AUTH_SESSION_FILE", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / SESSION_DIR_NAME / SESSION_FILE_NAME


def _ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _load_session(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _save_session(path: Path, session: dict[str, Any]) -> None:
    _ensure_parent_dir(path)
    path.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _remove_session(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


def _session_expired(session: dict[str, Any], skew_sec: int = 60) -> bool:
    expires_at = _to_int(session.get("expires_at"), 0)
    if expires_at <= 0:
        return True
    return (expires_at - skew_sec) <= int(time.time())


def _auth_apikey() -> str:
    # Prefer anon key for auth, but keep backward compatibility with SUPABASE_KEY-only setup.
    return (
        os.getenv("SUPABASE_AUTH_KEY", "").strip()
        or os.getenv("SUPABASE_ANON_KEY", "").strip()
        or os.getenv("SUPABASE_KEY", "").strip()
    )


def _supabase_auth_request(
    supabase_url: str,
    apikey: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    bearer_token: str | None = None,
    timeout: float = 20.0,
) -> dict[str, Any]:
    url = f"{supabase_url.rstrip('/')}{path}"
    data = None
    headers = {
        "apikey": apikey,
        "Accept": "application/json",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"

    req = Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace").strip()
            if not raw:
                return {}
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
            return {"data": parsed}
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        snippet = body[:300].strip()
        raise AuthError(f"Auth API error ({exc.code}): {snippet}") from exc
    except (URLError, json.JSONDecodeError) as exc:
        raise AuthError(f"Auth API request failed: {exc}") from exc


def _create_pkce_pair() -> tuple[str, str]:
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return code_verifier, code_challenge


def _make_oauth_handler(state: OAuthState) -> type[BaseHTTPRequestHandler]:
    class OAuthHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def build_completion_html(self, auth_data: dict[str, str]) -> str:
            # Use the actual listening port so callback works even when TUI_AUTH_PORT is changed.
            port = self.server.server_port
            payload_json = json.dumps(auth_data, ensure_ascii=False)
            return f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8" />
  <title>Comm0ns Login</title>
</head>
<body>
  <p id="status">認証処理中...</p>
  <script>
    const status = document.getElementById("status");
    const PORT = {port};
    const payload = {payload_json};

    // Keep compatibility with providers that return tokens in query/hash.
    const hashParams = new URLSearchParams(window.location.hash.slice(1));
    const queryParams = new URLSearchParams(window.location.search.slice(1));
    hashParams.forEach((v, k) => {{ payload[k] = v; }});
    queryParams.forEach((v, k) => {{ payload[k] = v; }});

    fetch("http://127.0.0.1:" + PORT + "/auth/complete", {{
      method: "POST",
      headers: {{"Content-Type": "application/json"}},
      body: JSON.stringify(payload),
    }})
      .then((res) => {{
        if (!res.ok) throw new Error("Server returned " + res.status);
        status.textContent = "認証完了！このタブを閉じてください。";
      }})
      .catch((err) => {{
        status.textContent = "CLIとの通信に失敗しました（ポート: " + PORT + "） " + err;
      }});
  </script>
</body>
</html>
"""

        def _write_html(self, status: int, html: str) -> None:
            body = html.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _write_json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self) -> None:
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path != "/auth/callback":
                self._write_html(404, "<h1>Not Found</h1>")
                return

            query = {k: v[0] for k, v in parse_qs(parsed.query).items() if v}
            has_auth_payload = any(
                k in query for k in ("code", "error", "access_token", "refresh_token")
            )
            if has_auth_payload and state.payload is None:
                state.payload = query
                state.event.set()
            self._write_html(200, self.build_completion_html(query))

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path != "/auth/complete":
                self._write_json(404, {"ok": False, "error": "not_found"})
                return

            length = _to_int(self.headers.get("Content-Length"), 0)
            raw = self.rfile.read(max(0, length)).decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                payload = {}

            normalized = {str(k): str(v) for k, v in payload.items()}
            if normalized:
                state.payload = normalized
            if state.payload is None:
                state.payload = {}
            state.event.set()
            self._write_json(200, {"ok": True})

    return OAuthHandler


def _normalize_session_payload(payload: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    access_token = str(payload.get("access_token") or "").strip()
    refresh_token = str(payload.get("refresh_token") or "").strip()
    if not refresh_token and previous:
        refresh_token = str(previous.get("refresh_token") or "").strip()
    if not access_token:
        raise AuthError("access_token is missing in auth response")

    expires_at = _to_int(payload.get("expires_at"), 0)
    if expires_at <= 0:
        expires_in = _to_int(payload.get("expires_in"), 3600)
        expires_at = int(time.time()) + max(60, expires_in)

    session: dict[str, Any] = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": expires_at,
        "token_type": payload.get("token_type", "bearer"),
        "saved_at": int(time.time()),
    }
    if "user" in payload and isinstance(payload["user"], dict):
        session["user"] = payload["user"]
    return session


def _refresh_session_if_needed(
    supabase_url: str,
    apikey: str,
    session: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not session:
        return None
    if not _session_expired(session):
        return session

    refresh_token = str(session.get("refresh_token") or "").strip()
    if not refresh_token:
        return None

    refreshed = _supabase_auth_request(
        supabase_url,
        apikey,
        "POST",
        "/auth/v1/token?grant_type=refresh_token",
        {"refresh_token": refresh_token},
    )
    return _normalize_session_payload(refreshed, previous=session)


def _fetch_auth_user(supabase_url: str, apikey: str, access_token: str) -> dict[str, Any] | None:
    try:
        return _supabase_auth_request(
            supabase_url,
            apikey,
            "GET",
            "/auth/v1/user",
            bearer_token=access_token,
        )
    except AuthError:
        return None


def _perform_browser_login(supabase_url: str, apikey: str, timeout_sec: int) -> dict[str, Any]:
    state = OAuthState()
    handler = _make_oauth_handler(state)
    port = _to_int(os.getenv("TUI_AUTH_PORT"), DEFAULT_AUTH_PORT)
    if not (1024 <= port <= 65535):
        raise AuthError(f"TUI_AUTH_PORT is invalid: {port}")

    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    except OSError as exc:
        raise AuthError(f"Failed to bind localhost:{port}. Please free the port or set TUI_AUTH_PORT.") from exc

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    code_verifier, code_challenge = _create_pkce_pair()
    redirect_to = f"http://127.0.0.1:{port}/auth/callback"
    params = {
        "provider": "discord",
        "redirect_to": redirect_to,
        "scopes": "identify email",
        "code_challenge": code_challenge,
        "code_challenge_method": "s256",
    }
    auth_url = f"{supabase_url.rstrip('/')}/auth/v1/authorize?{urlencode(params)}"

    print("Launching browser for Discord authentication...")
    opened = webbrowser.open(auth_url, new=2, autoraise=True)
    if not opened:
        print("Browser auto-open failed. Open this URL manually:")
        print(auth_url)

    try:
        ok = state.event.wait(timeout=max(30, timeout_sec))
        if not ok or state.payload is None:
            raise AuthError("Timed out while waiting for OAuth callback")
        payload = state.payload
        if payload.get("error"):
            reason = payload.get("error_description") or payload.get("error")
            raise AuthError(f"OAuth failed: {reason}")

        if payload.get("code"):
            token_payload = _supabase_auth_request(
                supabase_url,
                apikey,
                "POST",
                "/auth/v1/token?grant_type=pkce",
                {
                    "auth_code": payload["code"],
                    "code_verifier": code_verifier,
                },
            )
            return _normalize_session_payload(token_payload)

        if payload.get("access_token"):
            return _normalize_session_payload(payload)

        raise AuthError("OAuth callback did not include auth code or token")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


def ensure_tui_auth_session(force_login: bool, timeout_sec: int, supabase_url: str = "", supabase_key: str = "") -> dict[str, Any]:
    _load_dotenv_simple()
    url = supabase_url or os.getenv("SUPABASE_URL", "").strip()
    apikey = supabase_key or _auth_apikey()
    if not url:
        raise AuthError("SUPABASE_URL is required for TUI authentication")
    if not apikey:
        raise AuthError("SUPABASE_AUTH_KEY or SUPABASE_ANON_KEY (fallback SUPABASE_KEY) is required")

    session_file = _session_file_path()
    if force_login:
        _remove_session(session_file)

    current = _load_session(session_file)
    refreshed = _refresh_session_if_needed(url, apikey, current)
    if refreshed:
        user = _fetch_auth_user(url, apikey, refreshed["access_token"])
        if user:
            refreshed["user"] = user
        _save_session(session_file, refreshed)
        return refreshed

    new_session = _perform_browser_login(url, apikey, timeout_sec)
    user = _fetch_auth_user(url, apikey, new_session["access_token"])
    if user:
        new_session["user"] = user
    _save_session(session_file, new_session)
    return new_session
