#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Cloudflare Turnstile helpers: classify pages, keep CF cookies, auto-click the widget."""

from __future__ import annotations

import subprocess
import sys
import time
from typing import Any, Callable, Iterable

LogFn = Callable[[str], None]


class TurnstileStopped(Exception):
    """Raised when the caller requests cancel during Turnstile handling."""


HARD_BLOCK_MARKERS = (
    "故障排除",
    "cf-error-details",
    "cf-error",
    "sorry, you have been blocked",
    "you have been blocked",
    "error code 1020",
    "error code 1005",
    "error code 1015",
    "access denied",
)

CHALLENGE_MARKERS = (
    "确认您是真人",
    "确认你是真人",
    "进行人机验证",
    "人机验证",
    "verify you are human",
    "confirm you are human",
    "just a moment",
    "checking your browser",
    "needs to review the security of your connection",
    "cf-turnstile",
    "challenges.cloudflare.com",
)

CF_COOKIE_NAMES = {
    "cf_clearance",
    "__cf_bm",
    "_cfuvid",
    "cf_chl_2",
    "cf_chl_prog",
    "cf_chl_rc_i",
    "cf_chl_rc_ni",
}

LOCATE_TURNSTILE_JS = r"""
function rectOf(node) {
  if (!node || !node.getBoundingClientRect) return null;
  const r = node.getBoundingClientRect();
  return {
    x: r.left,
    y: r.top,
    width: r.width,
    height: r.height,
    visible: r.width > 10 && r.height > 10
  };
}
function pickIframe() {
  const selectors = [
    'iframe[src*="challenges.cloudflare.com"]',
    'iframe[src*="turnstile"]',
    'iframe[id^="cf-chl-widget"]',
    'div.cf-turnstile iframe',
    'iframe[title*="Widget containing"]',
    'iframe[title*="Cloudflare"]',
  ];
  for (const sel of selectors) {
    const node = document.querySelector(sel);
    if (node) return node;
  }
  const hosts = document.querySelectorAll('div, span, body, section, form');
  for (const host of hosts) {
    const root = host.shadowRoot;
    if (!root) continue;
    const node = root.querySelector('iframe[src*="challenges.cloudflare.com"], iframe[src*="turnstile"], iframe');
    if (node) return node;
  }
  return null;
}
function pickWidget() {
  const selectors = [
    '.cf-turnstile',
    '[data-sitekey]',
    'div[id^="cf-chl"]',
    'div[class*="turnstile" i]',
  ];
  for (const sel of selectors) {
    const node = document.querySelector(sel);
    if (node) return node;
  }
  return null;
}
function pickLabelRect() {
  const nodes = Array.from(document.querySelectorAll('div, span, label, p, button'));
  for (const node of nodes) {
    const t = String(node.innerText || node.textContent || '').replace(/\s+/g, ' ').trim();
    if (!t || t.length > 48) continue;
    if (!/(确认您是真人|确认你是真人|Verify you are human|成功！|Success!)/i.test(t)) continue;
    const r = rectOf(node);
    if (r && r.visible) return r;
  }
  return null;
}
const iframe = pickIframe();
const widget = pickWidget();
const input = document.querySelector('input[name="cf-turnstile-response"]');
const token = String((input && input.value) || '').trim();
let rect = rectOf(iframe) || rectOf(widget) || pickLabelRect();
const body = ((document.body && (document.body.innerText || document.body.textContent)) || '')
  .replace(/\s+/g, ' ').trim().slice(0, 500);
const html = (document.documentElement && document.documentElement.innerHTML || '').slice(0, 2500);
return {
  token,
  tokenLen: token.length,
  hasInput: !!input,
  hasIframe: !!iframe,
  hasWidget: !!widget,
  rect,
  url: location.href || '',
  title: document.title || '',
  body,
  html,
  screenX: Number(window.screenX || 0),
  screenY: Number(window.screenY || 0),
  outerWidth: Number(window.outerWidth || 0),
  outerHeight: Number(window.outerHeight || 0),
  innerWidth: Number(window.innerWidth || 0),
  innerHeight: Number(window.innerHeight || 0),
  dpr: Number(window.devicePixelRatio || 1)
};
"""

READ_TOKEN_JS = r"""
try {
  const byInput = String((document.querySelector('input[name="cf-turnstile-response"]') || {}).value || '').trim();
  if (byInput) return byInput;
  if (window.turnstile && typeof window.turnstile.getResponse === 'function') {
    return String(window.turnstile.getResponse() || '').trim();
  }
  return '';
} catch (e) { return ''; }
"""

PATCH_MOUSE_JS = r"""
window.dtp = 1;
function getRandomInt(min, max) { return Math.floor(Math.random() * (max - min + 1)) + min; }
let sx = getRandomInt(800, 1400);
let sy = getRandomInt(300, 800);
try {
  Object.defineProperty(MouseEvent.prototype, 'screenX', { get() { return sx; }, configurable: true });
  Object.defineProperty(MouseEvent.prototype, 'screenY', { get() { return sy; }, configurable: true });
} catch (e) {}
"""


def _noop_log(_: str) -> None:
    return None


def is_turnstile_token_ready(token: Any, min_len: int = 80) -> bool:
    text = str(token or "").strip()
    if text == "passed":
        return True
    return len(text) >= min_len


def is_cf_cookie_name(name: Any) -> bool:
    n = str(name or "").strip().lower()
    if not n:
        return False
    if n in CF_COOKIE_NAMES:
        return True
    return n.startswith("cf_") or n.startswith("__cf")


def _cookie_as_dict(cookie: Any) -> dict[str, Any] | None:
    if isinstance(cookie, dict):
        name = cookie.get("name") or cookie.get("Name")
        value = cookie.get("value") if "value" in cookie else cookie.get("Value")
        if not name or value is None:
            return None
        item = {
            "name": str(name),
            "value": str(value),
            "domain": str(cookie.get("domain") or cookie.get("Domain") or ".x.ai"),
            "path": str(cookie.get("path") or cookie.get("Path") or "/"),
        }
        for src, dst in (
            ("secure", "secure"),
            ("httpOnly", "httpOnly"),
            ("sameSite", "sameSite"),
            ("expiry", "expiry"),
            ("expires", "expiry"),
        ):
            if cookie.get(src) is not None:
                item[dst] = cookie[src]
        return item
    name = getattr(cookie, "name", None)
    value = getattr(cookie, "value", None)
    if not name or value is None:
        return None
    return {
        "name": str(name),
        "value": str(value),
        "domain": str(getattr(cookie, "domain", "") or ".x.ai"),
        "path": str(getattr(cookie, "path", "") or "/"),
    }


def filter_cf_cookies(cookies: Iterable[Any] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for cookie in cookies or []:
        item = _cookie_as_dict(cookie)
        if item and is_cf_cookie_name(item.get("name")):
            out.append(item)
    return out


def classify_cloudflare_text(blob: str) -> str:
    """Return hard-block, challenge, or none.

    Ray ID alone is not a hard block; it appears on Turnstile pages too.
    Challenge markers win when both are present and a widget/copy is visible.
    """
    text = str(blob or "").lower()
    if not text.strip():
        return "none"
    challenge_hit = next((m for m in CHALLENGE_MARKERS if m in text), "")
    hard_hit = next((m for m in HARD_BLOCK_MARKERS if m in text), "")
    if challenge_hit and not any(
        m in text
        for m in (
            "error code 1020",
            "error code 1005",
            "error code 1015",
            "you have been blocked",
            "sorry, you have been blocked",
        )
    ):
        return "challenge"
    if hard_hit:
        return "hard-block"
    if challenge_hit:
        return "challenge"
    return "none"


def turnstile_click_offsets(width: float, height: float) -> list[tuple[float, float]]:
    w = float(width or 300)
    h = float(height or 65)
    checkbox_x = max(18.0, min(32.0, w * 0.09))
    mid_y = h / 2.0 if h > 0 else 32.0
    points = [
        (checkbox_x, mid_y),
        (26.0, mid_y),
        (w / 2.0, mid_y),
    ]
    deduped: list[tuple[float, float]] = []
    seen: set[tuple[int, int]] = set()
    for x, y in points:
        key = (int(round(x)), int(round(y)))
        if key in seen:
            continue
        seen.add(key)
        deduped.append((x, y))
    return deduped


def viewport_to_screen(
    loc: dict[str, Any],
    view_x: float,
    view_y: float,
) -> tuple[float, float]:
    chrome_h = max(0.0, float(loc.get("outerHeight") or 0) - float(loc.get("innerHeight") or 0))
    chrome_w = max(0.0, float(loc.get("outerWidth") or 0) - float(loc.get("innerWidth") or 0))
    screen_x = float(loc.get("screenX") or 0) + (chrome_w / 2.0) + float(view_x)
    screen_y = float(loc.get("screenY") or 0) + chrome_h + float(view_y)
    return screen_x, screen_y


def _cancelled(cancel_callback: Callable[[], Any] | None) -> bool:
    if not cancel_callback:
        return False
    try:
        return bool(cancel_callback())
    except Exception:
        return False


def _run_js(page: Any, script: str, default: Any = None) -> Any:
    if page is None:
        return default
    try:
        return page.run_js(script)
    except Exception:
        return default


def read_turnstile_token(page: Any) -> str:
    token = _run_js(page, READ_TOKEN_JS, "")
    return str(token or "").strip()


def locate_turnstile(page: Any) -> dict[str, Any]:
    info = _run_js(page, LOCATE_TURNSTILE_JS, {})
    return info if isinstance(info, dict) else {}


def inspect_cloudflare_page(page: Any, log: LogFn | None = None) -> tuple[str, str]:
    loc = locate_turnstile(page)
    blob = " ".join(
        [
            str(loc.get("url") or ""),
            str(loc.get("title") or ""),
            str(loc.get("body") or ""),
            str(loc.get("html") or ""),
        ]
    )
    kind = classify_cloudflare_text(blob)
    if kind == "none" and (loc.get("hasIframe") or loc.get("hasInput")):
        kind = "challenge"
    detail = (
        f"kind={kind}; url={loc.get('url') or ''}; title={loc.get('title') or ''}; "
        f"iframe={bool(loc.get('hasIframe'))}; tokenLen={loc.get('tokenLen') or 0}"
    )
    if log and kind != "none":
        log(f"[Debug] Cloudflare 页面检测: {detail}")
    return kind, detail


def collect_browser_cookies(page: Any, browser: Any = None) -> list[Any]:
    targets = [page]
    if browser is not None:
        targets.append(browser)
    elif page is not None:
        targets.append(getattr(page, "browser", None))
    for target in targets:
        if target is None:
            continue
        for kwargs in (
            {"all_domains": True, "all_info": True},
            {"all_domains": True},
            {},
        ):
            try:
                cookies = target.cookies(**kwargs) if kwargs else target.cookies()
            except TypeError:
                try:
                    cookies = target.cookies()
                except Exception:
                    cookies = None
            except Exception:
                cookies = None
            if cookies:
                return list(cookies)
    return []


def collect_cf_cookies(page: Any, browser: Any = None) -> list[dict[str, Any]]:
    return filter_cf_cookies(collect_browser_cookies(page, browser))


def clear_browser_cookies(page: Any, browser: Any = None) -> None:
    targets = [page]
    if browser is not None:
        targets.append(browser)
    elif page is not None:
        targets.append(getattr(page, "browser", None))
    for target in targets:
        if target is None:
            continue
        for action in (
            lambda t: t.set.cookies.clear(),
            lambda t: t.set_cookies(False),
        ):
            try:
                action(target)
                break
            except Exception:
                continue


def inject_cookies(page: Any, cookies: Iterable[dict[str, Any]], log: LogFn | None = None) -> int:
    log = log or _noop_log
    items = [c for c in cookies or [] if c.get("name") and c.get("value") is not None]
    if page is None or not items:
        return 0
    injected = 0
    for item in items:
        payload = {
            "name": str(item["name"]),
            "value": str(item["value"]),
            "domain": str(item.get("domain") or ".x.ai"),
            "path": str(item.get("path") or "/"),
        }
        if item.get("secure") is not None:
            payload["secure"] = bool(item.get("secure"))
        if item.get("httpOnly") is not None:
            payload["httpOnly"] = bool(item.get("httpOnly"))
        expiry = item.get("expiry")
        if expiry is not None:
            try:
                payload["expires"] = float(expiry)
            except Exception:
                pass
        ok = False
        try:
            page.run_cdp("Network.setCookie", **payload)
            ok = True
        except Exception:
            try:
                setter = getattr(getattr(page, "set", None), "cookies", None)
                if setter:
                    setter(item)
                    ok = True
            except Exception:
                ok = False
        if ok:
            injected += 1
    if injected:
        log(f"[Debug] 已写回 {injected} 个 Cloudflare cookie")
    return injected


def _bring_to_front(page: Any) -> None:
    actions = (
        lambda: page.run_cdp("Page.bringToFront"),
        lambda: page.set.activate(),
        lambda: getattr(page, "browser").activate(),
    )
    for action in actions:
        try:
            action()
            return
        except Exception:
            continue


def _ele_rect(ele: Any) -> dict[str, float] | None:
    if ele is None:
        return None
    try:
        r = ele.rect
    except Exception:
        return None
    try:
        x = float(getattr(r, "x", None) if not isinstance(r, dict) else r.get("x", 0))
        y = float(getattr(r, "y", None) if not isinstance(r, dict) else r.get("y", 0))
        w = float(getattr(r, "width", None) if not isinstance(r, dict) else r.get("width", 0))
        h = float(getattr(r, "height", None) if not isinstance(r, dict) else r.get("height", 0))
    except Exception:
        try:
            # DrissionPage Location may expose corners / size helpers
            x = float(r.location[0])  # type: ignore[index]
            y = float(r.location[1])  # type: ignore[index]
            w = float(r.size[0])  # type: ignore[index]
            h = float(r.size[1])  # type: ignore[index]
        except Exception:
            return None
    if w <= 0 or h <= 0:
        return None
    return {"x": x, "y": y, "width": w, "height": h, "visible": True}


def _cdp_click_viewport(page: Any, x: float, y: float, log: LogFn) -> bool:
    vx, vy = float(x), float(y)
    if vx < 0 or vy < 0:
        return False
    try:
        page.run_js(PATCH_MOUSE_JS)
    except Exception:
        pass
    try:
        for event_type, buttons in (
            ("mouseMoved", 0),
            ("mousePressed", 1),
            ("mouseReleased", 0),
        ):
            page.run_cdp(
                "Input.dispatchMouseEvent",
                type=event_type,
                x=vx,
                y=vy,
                button="left",
                buttons=buttons,
                clickCount=1,
                pointerType="mouse",
            )
        log(f"[Debug] CDP 点击 viewport=({vx:.0f},{vy:.0f})")
        return True
    except Exception as exc:
        log(f"[Debug] CDP 点击失败: {exc}")
        return False


def _iter_shadow_iframes(page: Any) -> list[Any]:
    found: list[Any] = []
    seen: set[int] = set()

    def _add(iframe: Any) -> None:
        if iframe is None:
            return
        key = id(iframe)
        if key in seen:
            return
        seen.add(key)
        found.append(iframe)

    for sel in (
        'css:iframe[src*="challenges.cloudflare.com"]',
        'css:iframe[src*="turnstile"]',
        "css:.cf-turnstile",
        "css:[data-sitekey]",
        '@name=cf-turnstile-response',
    ):
        try:
            el = page.ele(sel, timeout=0.05)
        except Exception:
            el = None
        if el is None:
            continue
        if "iframe" in sel:
            _add(el)
        else:
            try:
                sr = el.shadow_root
            except Exception:
                sr = None
            if sr is not None:
                try:
                    _add(sr.ele("tag:iframe", timeout=0.05))
                except Exception:
                    pass
            # 向上找一层父节点 shadow，覆盖 input 与 widget 是兄弟节点的结构
            try:
                parent = el.parent()
                psr = parent.shadow_root if parent is not None else None
                if psr is not None:
                    _add(psr.ele("tag:iframe", timeout=0.05))
            except Exception:
                pass
    return found


def _click_iframe_checkbox(iframe: Any, log: LogFn) -> bool:
    if iframe is None:
        return False
    try:
        iframe.run_js(PATCH_MOUSE_JS)
    except Exception:
        pass
    try:
        body = iframe.ele("tag:body", timeout=0.05)
        body_sr = body.shadow_root if body is not None else None
        btn = body_sr.ele("tag:input", timeout=0.05) if body_sr is not None else None
        if btn is not None:
            btn.click()
            log("[Debug] 已点击 Turnstile shadow checkbox")
            return True
    except Exception as exc:
        log(f"[Debug] shadow checkbox 点击失败: {exc}")
    for ox, oy in turnstile_click_offsets(300, 65):
        try:
            iframe.click.at(ox, oy)
            log(f"[Debug] iframe.click.at offset=({ox:.0f},{oy:.0f})")
            return True
        except Exception:
            continue
    try:
        iframe.click()
        log("[Debug] 已点击 Turnstile iframe")
        return True
    except Exception as exc:
        log(f"[Debug] iframe.click 失败: {exc}")
        return False


def _click_widget_elements(page: Any, log: LogFn) -> bool:
    for sel in (
        "css:.cf-turnstile",
        "css:[data-sitekey]",
        '@name=cf-turnstile-response',
        'css:input[name="cf-turnstile-response"]',
    ):
        try:
            ele = page.ele(sel, timeout=0.05)
        except Exception:
            ele = None
        if ele is None:
            continue
        rect = _ele_rect(ele)
        if not rect:
            continue
        for ox, oy in turnstile_click_offsets(rect["width"], rect["height"]):
            try:
                ele.click.at(ox, oy)
                log(f"[Debug] 元素 click.at {sel} offset=({ox:.0f},{oy:.0f})")
                return True
            except Exception:
                pass
            if _cdp_click_viewport(page, rect["x"] + ox, rect["y"] + oy, log):
                return True
    return False


def os_mouse_click(x: float, y: float, log: LogFn | None = None) -> bool:
    log = log or _noop_log
    sx, sy = int(round(x)), int(round(y))
    if sx <= 0 or sy <= 0:
        return False
    if sys.platform == "darwin":
        try:
            import Quartz

            point = (float(sx), float(sy))
            for event_type in (
                Quartz.kCGEventMouseMoved,
                Quartz.kCGEventLeftMouseDown,
                Quartz.kCGEventLeftMouseUp,
            ):
                event = Quartz.CGEventCreateMouseEvent(
                    None, event_type, point, Quartz.kCGMouseButtonLeft
                )
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
            log(f"[Debug] 系统鼠标点击 ({sx},{sy})")
            return True
        except Exception as exc:
            log(f"[Debug] Quartz 点击失败: {exc}")
        try:
            script = f'tell application "System Events" to click at {{{sx}, {sy}}}'
            result = subprocess.run(
                ["osascript", "-e", script],
                check=False,
                capture_output=True,
                timeout=2,
                text=True,
            )
            if result.returncode == 0:
                log(f"[Debug] osascript 点击 ({sx},{sy})")
                return True
            err = (result.stderr or result.stdout or "").strip()
            log(f"[Debug] osascript 点击失败: {err or result.returncode}")
        except Exception as exc:
            log(f"[Debug] osascript 异常: {exc}")
        return False
    if sys.platform.startswith("linux"):
        try:
            subprocess.run(
                ["xdotool", "mousemove", str(sx), str(sy), "click", "1"],
                check=False,
                capture_output=True,
                timeout=2,
            )
            log(f"[Debug] xdotool 点击 ({sx},{sy})")
            return True
        except Exception:
            return False
    try:
        import ctypes

        ctypes.windll.user32.SetCursorPos(sx, sy)
        ctypes.windll.user32.mouse_event(2, 0, 0, 0, 0)
        ctypes.windll.user32.mouse_event(4, 0, 0, 0, 0)
        log(f"[Debug] Win32 鼠标点击 ({sx},{sy})")
        return True
    except Exception:
        return False


def _os_click_widget(page: Any, loc: dict[str, Any], log: LogFn) -> bool:
    rect = loc.get("rect") or {}
    if not rect:
        return False
    width = float(rect.get("width") or 0)
    height = float(rect.get("height") or 0)
    if width <= 0 or height <= 0:
        # label-only fallback: click slightly left of text center
        width = 280.0
        height = 40.0
        rect = {
            "x": float(rect.get("x") or 0) - 40.0,
            "y": float(rect.get("y") or 0),
            "width": width,
            "height": height,
        }
    _bring_to_front(page)
    ox, oy = turnstile_click_offsets(width, height)[0]
    view_x = float(rect.get("x") or 0) + ox
    view_y = float(rect.get("y") or 0) + oy
    screen_x, screen_y = viewport_to_screen(loc, view_x, view_y)
    return os_mouse_click(screen_x, screen_y, log=log)


def _click_from_rect(page: Any, loc: dict[str, Any], log: LogFn, os_click: bool) -> bool:
    rect = loc.get("rect") or {}
    if not rect:
        return False
    width = float(rect.get("width") or 300)
    height = float(rect.get("height") or 65)
    ox, oy = turnstile_click_offsets(width, height)[0]
    vx = float(rect.get("x") or 0) + ox
    vy = float(rect.get("y") or 0) + oy
    if _cdp_click_viewport(page, vx, vy, log):
        return True
    if os_click:
        return _os_click_widget(page, loc, log)
    return False


def _try_clicks(
    page: Any,
    loc: dict[str, Any],
    log: LogFn,
    os_click: bool,
    fast: bool = False,
) -> bool:
    rect = loc.get("rect") or {}
    log(
        "[Debug] Turnstile locate: "
        f"iframe={bool(loc.get('hasIframe'))} widget={bool(loc.get('hasWidget'))} "
        f"input={bool(loc.get('hasInput'))} rect={rect}"
    )

    # Fast path first: JS rect → CDP / OS. This is what actually works and is cheap.
    if _click_from_rect(page, loc, log, os_click=os_click):
        return True

    if fast:
        log("[Debug] 快速点击未命中，本轮跳过慢查找")
        return False

    for iframe in _iter_shadow_iframes(page):
        if _click_iframe_checkbox(iframe, log):
            return True
        iframe_rect = _ele_rect(iframe)
        if iframe_rect:
            ox, oy = turnstile_click_offsets(iframe_rect["width"], iframe_rect["height"])[0]
            if _cdp_click_viewport(page, iframe_rect["x"] + ox, iframe_rect["y"] + oy, log):
                return True

    if _click_widget_elements(page, log):
        return True

    log("[Debug] 本轮未找到可点击的 Turnstile 目标")
    return False


def solve_turnstile(
    page: Any,
    timeout: float = 45,
    log: LogFn | None = None,
    cancel_callback: Callable[[], Any] | None = None,
    auto_click: bool = True,
    os_click: bool = True,
    require: bool = False,
) -> str:
    """Wait for Turnstile to pass; click the checkbox when auto_click is on.

    Returns the token string, 'passed' for interstitial pages that cleared,
    or '' if nothing needed solving / timed out.
    """
    log = log or _noop_log
    if page is None:
        return ""
    deadline = time.time() + max(float(timeout or 0), 0)
    loc = locate_turnstile(page)
    token = str(loc.get("token") or "").strip() or read_turnstile_token(page)
    if is_turnstile_token_ready(token):
        return token

    def _kind_from(info: dict[str, Any]) -> tuple[str, bool]:
        blob = " ".join(
            [
                str(info.get("url") or ""),
                str(info.get("title") or ""),
                str(info.get("body") or ""),
                str(info.get("html") or ""),
            ]
        )
        current_kind = classify_cloudflare_text(blob)
        widget = bool(
            info.get("hasIframe")
            or info.get("hasWidget")
            or info.get("hasInput")
            or (info.get("rect") or {}).get("visible")
            or float((info.get("rect") or {}).get("width") or 0) > 10
        )
        if current_kind == "none" and widget:
            current_kind = "challenge"
        return current_kind, widget

    kind, has_widget = _kind_from(loc)
    if kind == "none" and not has_widget and not require:
        return ""

    last_click_at = -999.0  # 立刻点，不要先干等
    click_round = 0
    saw_widget = has_widget or kind == "challenge"
    announced = False
    if saw_widget:
        log("[*] 检测到 Cloudflare 人机验证，开始自动处理")
        announced = True
        _bring_to_front(page)
    while time.time() < deadline:
        if _cancelled(cancel_callback):
            raise TurnstileStopped()
        token = read_turnstile_token(page)
        if is_turnstile_token_ready(token):
            log(f"[*] Turnstile 已通过，token长度={len(token)}")
            return token
        loc = locate_turnstile(page)
        kind, has_widget = _kind_from(loc)
        if has_widget or kind == "challenge":
            saw_widget = True
            if not announced:
                log("[*] 检测到 Cloudflare 人机验证，开始自动处理")
                announced = True
                _bring_to_front(page)
        if saw_widget and kind == "none" and not has_widget:
            log("[*] Cloudflare 验证页已离开，视为通过")
            return "passed"
        if not has_widget and kind == "none":
            time.sleep(0.35)
            continue
        if auto_click and time.time() - last_click_at >= 0.7:
            # 前两轮只走快速路径（CDP/系统鼠标），避免 shadow 慢查找拖到一分钟
            clicked = _try_clicks(
                page,
                loc,
                log,
                os_click=os_click,
                fast=(click_round < 2),
            )
            last_click_at = time.time()
            click_round += 1
            if clicked:
                # 给 Turnstile 一点时间出 token，再决定要不要补点
                settle_deadline = time.time() + 1.2
                while time.time() < settle_deadline:
                    if _cancelled(cancel_callback):
                        raise TurnstileStopped()
                    token = read_turnstile_token(page)
                    if is_turnstile_token_ready(token):
                        log(f"[*] Turnstile 已通过，token长度={len(token)}")
                        return token
                    time.sleep(0.2)
                continue
        time.sleep(0.25)
    # 超时后读一次 token 返回
    token = read_turnstile_token(page)
    if is_turnstile_token_ready(token):
        log(f"[*] Turnstile 已通过，token长度={len(token)}")
        return token
    log("[!] Turnstile 自动处理超时，仍可手动点击后继续")
    return token
