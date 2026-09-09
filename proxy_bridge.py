"""Authenticated proxy helpers, fixed/pool selection, and Chromium bridge.

Chromium ``--proxy-server`` cannot embed ``user:pass``. When the configured
proxy has credentials, start a local forwarder and point the browser at it.
HTTP clients (requests / curl_cffi) keep using the full authenticated URL.

Proxy modes:
  - fixed: single ``proxy`` value
  - pool: random line from ``proxy_pool_file`` (one proxy per line)
"""

from __future__ import annotations

import base64
import os
import random
import re
import secrets
import select
import socket
import threading
from urllib.parse import quote, unquote, urlparse

_SESSION_IN_USER_RE = re.compile(r"-session-[A-Za-z0-9_-]+", re.IGNORECASE)

_lock = threading.Lock()
_bridges: dict[str, "_AuthProxyBridge"] = {}
_pool_lock = threading.Lock()
_pool_cache: dict[str, object] = {"path": "", "mtime": None, "items": []}
_thread = threading.local()


def normalize_proxy(raw: str | None) -> str:
    """Accept common proxy formats and return ``scheme://[user:pass@]host:port``."""
    text = (raw or "").strip()
    if not text:
        return ""

    # host:port:user:pass  (password may contain ':')
    if "://" not in text and text.count(":") >= 3:
        host, port, user, password = text.split(":", 3)
        host = host.strip()
        port = port.strip()
        user = user.strip()
        password = password.strip()
        if host and port.isdigit() and user:
            return (
                f"http://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}"
            )

    if "://" not in text:
        text = f"http://{text}"

    parsed = urlparse(text)
    if not parsed.hostname:
        return text

    scheme = (parsed.scheme or "http").lower()
    port = parsed.port or (443 if scheme == "https" else 80)
    if parsed.username is None:
        return f"{scheme}://{parsed.hostname}:{port}"

    user = unquote(parsed.username)
    password = unquote(parsed.password or "")
    return f"{scheme}://{quote(user, safe='')}:{quote(password, safe='')}@{parsed.hostname}:{port}"


def proxy_log_label(raw: str | None) -> str:
    proxy = normalize_proxy(raw)
    if not proxy:
        return ""
    try:
        u = urlparse(proxy)
        auth = "user:***@" if u.username else ""
        port = f":{u.port}" if u.port else ""
        return f"{u.scheme or 'http'}://{auth}{u.hostname or '?'}{port}"
    except Exception:
        return "(proxy)"


def proxy_has_auth(raw: str | None) -> bool:
    proxy = normalize_proxy(raw)
    if not proxy:
        return False
    return urlparse(proxy).username is not None


def proxy_for_http(raw: str | None) -> str:
    """Full proxy URL for requests / curl_cffi."""
    return normalize_proxy(raw)


def proxy_for_chromium(raw: str | None) -> str:
    """Return a Chromium-safe proxy URL (no embedded credentials)."""
    proxy = normalize_proxy(raw)
    if not proxy:
        return ""
    parsed = urlparse(proxy)
    if parsed.username is None:
        scheme = (parsed.scheme or "http").lower()
        if scheme.startswith("socks"):
            # DrissionPage / Chromium flags here do not support SOCKS well.
            return proxy
        host = parsed.hostname or ""
        port = parsed.port or (443 if scheme == "https" else 80)
        return f"{scheme}://{host}:{port}"
    return ensure_local_auth_bridge(proxy)


def normalize_proxy_mode(raw: str | None) -> str:
    text = str(raw or "fixed").strip().lower()
    if text in ("pool", "proxy_pool", "proxypool", "代理池"):
        return "pool"
    return "fixed"


def parse_proxy_pool_text(text: str) -> list[str]:
    """Parse pool file contents; blank lines and ``#`` comments are ignored."""
    items: list[str] = []
    seen: set[str] = set()
    for line in (text or "").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        # allow "proxy = ..." style leftovers
        if "=" in raw and "://" not in raw and raw.count(":") < 3:
            continue
        normalized = normalize_proxy(raw)
        if not normalized or normalized in seen:
            continue
        # require a hostname
        if not urlparse(normalized).hostname:
            continue
        seen.add(normalized)
        items.append(normalized)
    return items


def load_proxy_pool(path: str | None, *, force: bool = False) -> list[str]:
    """Load and cache proxies from a text file. Re-reads when mtime changes."""
    raw_path = str(path or "").strip()
    if not raw_path:
        return []
    abs_path = os.path.abspath(os.path.expanduser(raw_path))
    try:
        mtime = os.path.getmtime(abs_path)
    except OSError:
        with _pool_lock:
            if _pool_cache.get("path") == abs_path:
                _pool_cache.update({"path": abs_path, "mtime": None, "items": []})
        return []

    with _pool_lock:
        if (
            not force
            and _pool_cache.get("path") == abs_path
            and _pool_cache.get("mtime") == mtime
        ):
            return list(_pool_cache.get("items") or [])

    try:
        with open(abs_path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        with _pool_lock:
            _pool_cache.update({"path": abs_path, "mtime": mtime, "items": []})
        return []

    items = parse_proxy_pool_text(text)
    with _pool_lock:
        _pool_cache.update({"path": abs_path, "mtime": mtime, "items": list(items)})
    return items


def pick_pool_proxy(path: str | None, *, exclude: str | None = None) -> str:
    items = load_proxy_pool(path)
    if not items:
        return ""
    excluded = normalize_proxy(exclude) if exclude else ""
    candidates = [item for item in items if item != excluded] if excluded else list(items)
    if not candidates:
        candidates = list(items)
    return random.choice(candidates)


def rotate_residential_session(raw: str | None) -> str:
    """Force a new residential exit by rewriting ``-session-<id>`` in the username.

    Works with Decodo / Smartproxy-style usernames such as
    ``user-xxx-country-us-city-los_angeles``. Proxies without a username are
    returned unchanged.
    """
    proxy = normalize_proxy(raw)
    if not proxy:
        return ""
    parsed = urlparse(proxy)
    user = unquote(parsed.username or "")
    if not user:
        return proxy
    password = unquote(parsed.password or "")
    session_id = secrets.token_hex(4)
    if _SESSION_IN_USER_RE.search(user):
        new_user = _SESSION_IN_USER_RE.sub(f"-session-{session_id}", user, count=1)
    else:
        new_user = f"{user}-session-{session_id}"
    scheme = (parsed.scheme or "http").lower()
    host = parsed.hostname or ""
    port = parsed.port or (443 if scheme == "https" else 80)
    auth = f"{quote(new_user, safe='')}:{quote(password, safe='')}@"
    return f"{scheme}://{auth}{host}:{port}"

def set_thread_proxy(proxy: str | None) -> None:
    """Pin proxy for the current registration worker thread."""
    _thread.proxy = "" if proxy is None else str(proxy)


def clear_thread_proxy() -> None:
    if hasattr(_thread, "proxy"):
        delattr(_thread, "proxy")
    if hasattr(_thread, "force_rotate"):
        delattr(_thread, "force_rotate")


def get_thread_proxy() -> str | None:
    """Return pinned proxy, or None if this thread has not pinned one."""
    if hasattr(_thread, "proxy"):
        return str(getattr(_thread, "proxy") or "")
    return None


def mark_force_rotate(enabled: bool = True) -> None:
    """Ask the next ``assign=True`` resolve to pick a fresh exit IP."""
    _thread.force_rotate = bool(enabled)


def consume_force_rotate() -> bool:
    flagged = bool(getattr(_thread, "force_rotate", False))
    if hasattr(_thread, "force_rotate"):
        delattr(_thread, "force_rotate")
    return flagged


def resolve_proxy_from_config(cfg: dict | None, *, assign: bool = False) -> str:
    """Resolve proxy for fixed/pool mode.

    When ``assign`` is True in pool mode, pick a random proxy and pin it to
    the current thread. When False, reuse the thread pin if present.

    If ``mark_force_rotate()`` was set, pool mode prefers a different line and
    fixed residential proxies rewrite ``-session-<id>`` for a new exit IP.
    """
    cfg = cfg or {}
    mode = normalize_proxy_mode(cfg.get("proxy_mode"))
    force_rotate = consume_force_rotate() if assign else False
    if mode == "pool":
        pinned = get_thread_proxy()
        if assign or pinned is None:
            exclude = pinned if force_rotate else None
            chosen = pick_pool_proxy(cfg.get("proxy_pool_file"), exclude=exclude)
            if force_rotate and chosen and chosen == pinned:
                # Single-line pool: still rotate residential session if possible.
                chosen = rotate_residential_session(chosen) or chosen
            set_thread_proxy(chosen)
            return chosen
        return pinned
    fixed = normalize_proxy(cfg.get("proxy"))
    pinned = get_thread_proxy()
    if assign:
        if force_rotate and fixed:
            fixed = rotate_residential_session(fixed) or fixed
            set_thread_proxy(fixed)
            return fixed
        # Keep a previously rotated session on this thread across browser restarts.
        if pinned:
            return pinned
        set_thread_proxy(fixed)
        return fixed
    return pinned if pinned is not None else fixed


def active_proxy(cfg: dict | None = None) -> str:
    """Proxy for the current thread / config (does not re-roll pool)."""
    pinned = get_thread_proxy()
    if pinned is not None:
        return pinned
    return resolve_proxy_from_config(cfg, assign=False)


def ensure_local_auth_bridge(raw: str | None) -> str:
    """Start (or reuse) a local forwarder that injects Proxy-Authorization."""
    proxy = normalize_proxy(raw)
    if not proxy:
        return ""
    parsed = urlparse(proxy)
    if parsed.username is None:
        return proxy_for_chromium(proxy)

    with _lock:
        bridge = _bridges.get(proxy)
        if bridge is None or not bridge.alive:
            bridge = _AuthProxyBridge(
                upstream_host=parsed.hostname or "",
                upstream_port=int(parsed.port or 80),
                username=unquote(parsed.username or ""),
                password=unquote(parsed.password or ""),
            )
            bridge.start()
            _bridges[proxy] = bridge
        return bridge.local_url


class _AuthProxyBridge:
    """Minimal local HTTP proxy that forwards to an authenticated upstream."""

    def __init__(self, upstream_host: str, upstream_port: int, username: str, password: str):
        self.upstream_host = upstream_host
        self.upstream_port = upstream_port
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        self._auth_header = f"Proxy-Authorization: Basic {token}\r\n"
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._alive = False
        self.local_port = 0

    @property
    def alive(self) -> bool:
        return self._alive and self._sock is not None

    @property
    def local_url(self) -> str:
        return f"http://127.0.0.1:{self.local_port}"

    def start(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        sock.listen(64)
        self.local_port = sock.getsockname()[1]
        self._sock = sock
        self._alive = True
        self._thread = threading.Thread(
            target=self._serve,
            name=f"proxy-bridge-{self.local_port}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._alive = False
        sock = self._sock
        self._sock = None
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass

    def _serve(self) -> None:
        assert self._sock is not None
        while self._alive:
            try:
                self._sock.settimeout(1.0)
                client, _addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(
                target=self._handle_client,
                args=(client,),
                daemon=True,
            ).start()

    def _handle_client(self, client: socket.socket) -> None:
        upstream: socket.socket | None = None
        try:
            client.settimeout(30)
            req = _recv_headers(client)
            if not req:
                return
            first, _, rest = req.partition(b"\r\n")
            line = first.decode("latin-1", errors="ignore")
            parts = line.split(" ")
            if len(parts) < 2:
                return
            method = parts[0].upper()
            target = parts[1]

            upstream = socket.create_connection(
                (self.upstream_host, self.upstream_port),
                timeout=30,
            )
            upstream.settimeout(30)

            if method == "CONNECT":
                # Relay CONNECT to upstream with auth, then tunnel bytes.
                connect_req = (
                    f"CONNECT {target} HTTP/1.1\r\n"
                    f"Host: {target}\r\n"
                    f"{self._auth_header}"
                    f"Proxy-Connection: keep-alive\r\n"
                    f"\r\n"
                ).encode("latin-1")
                upstream.sendall(connect_req)
                resp = _recv_headers(upstream)
                if not resp or b" 200 " not in resp.split(b"\r\n", 1)[0]:
                    client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
                    return
                client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                _tunnel(client, upstream)
                return

            # Plain HTTP absolute-form request: inject auth and forward.
            head, sep, body = rest.partition(b"\r\n\r\n")
            filtered = []
            for hline in head.split(b"\r\n"):
                if not hline or hline.lower().startswith(b"proxy-authorization:"):
                    continue
                filtered.append(hline)
            forwarded = (
                first
                + b"\r\n"
                + (b"\r\n".join(filtered) + b"\r\n" if filtered else b"")
                + self._auth_header.encode("latin-1")
                + b"\r\n"
                + body
            )
            upstream.sendall(forwarded)
            _tunnel(client, upstream)
        except Exception:
            try:
                client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
            except Exception:
                pass
        finally:
            try:
                client.close()
            except Exception:
                pass
            if upstream is not None:
                try:
                    upstream.close()
                except Exception:
                    pass


def _recv_headers(sock: socket.socket, limit: int = 65536) -> bytes:
    data = b""
    while b"\r\n\r\n" not in data and len(data) < limit:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    return data


def _tunnel(a: socket.socket, b: socket.socket, idle_timeout: float = 120.0) -> None:
    sockets = [a, b]
    try:
        a.settimeout(None)
        b.settimeout(None)
    except Exception:
        pass
    while True:
        try:
            readable, _, errored = select.select(sockets, [], sockets, idle_timeout)
        except Exception:
            break
        if errored or not readable:
            break
        for src in readable:
            dst = b if src is a else a
            try:
                data = src.recv(65536)
            except Exception:
                return
            if not data:
                return
            try:
                dst.sendall(data)
            except Exception:
                return
