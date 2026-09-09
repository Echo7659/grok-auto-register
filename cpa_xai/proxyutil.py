"""Resolve outbound proxy for CPA mint HTTP + browser.

Priority (highest first):
  1. explicit argument
  2. thread-local runtime pin (set_runtime_proxy)
  3. environment https_proxy / HTTPS_PROXY / http_proxy / HTTP_PROXY

Thread-local pin avoids cross-talk when multiple mint workers run with
different proxies in the same process.
"""

from __future__ import annotations

import os
import threading

import proxy_bridge

_thread = threading.local()


def set_runtime_proxy(proxy: str | None) -> None:
    """Pin proxy for the *current thread*. Empty clears pin."""
    p = proxy_bridge.normalize_proxy(proxy) or (proxy or "").strip() or None
    _thread.proxy = p


def get_runtime_proxy() -> str | None:
    return getattr(_thread, "proxy", None)


def resolve_proxy(explicit: str | None = None) -> str:
    for cand in (
        (explicit or "").strip(),
        (get_runtime_proxy() or "").strip(),
        (os.environ.get("https_proxy") or "").strip(),
        (os.environ.get("HTTPS_PROXY") or "").strip(),
        (os.environ.get("http_proxy") or "").strip(),
        (os.environ.get("HTTP_PROXY") or "").strip(),
    ):
        if cand:
            return proxy_bridge.normalize_proxy(cand) or cand
    return ""


def proxy_for_chromium(proxy: str) -> str:
    """Chromium-safe proxy. Authenticated upstreams go through a local bridge."""
    return proxy_bridge.proxy_for_chromium(proxy)


def proxy_log_label(proxy: str) -> str:
    """Redact userinfo for logs."""
    return proxy_bridge.proxy_log_label(proxy)
