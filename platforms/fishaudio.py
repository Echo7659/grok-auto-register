"""Fish Audio browser signup and local session auth writer."""

from __future__ import annotations

import json
import os
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

SIGNUP_URL = "https://fish.audio/zh-CN/auth/signup/"
APP_URL_HINT = "/app"
DEFAULT_SESSION_TTL_SEC = 7 * 24 * 3600

LogFn = Callable[[str], None] | None
CancelFn = Callable[[], bool] | None


class FishRegistrationError(Exception):
    """Raised when a Fish Audio signup step fails."""


class FishCancelled(FishRegistrationError):
    """Raised when the user requests stop during Fish signup."""


def _raise_if_cancelled(cancel_callback: CancelFn):
    if cancel_callback and cancel_callback():
        raise FishCancelled("用户停止注册")


def _sleep(seconds: float, cancel_callback: CancelFn = None):
    end = time.time() + max(0.0, float(seconds))
    while time.time() < end:
        _raise_if_cancelled(cancel_callback)
        time.sleep(min(0.2, end - time.time()))


def _emit(log_callback: LogFn, message: str):
    if log_callback:
        log_callback(message)


def generate_password() -> str:
    """Strong password accepted by Fish signup validation.

    Avoid '-' so accounts lines using '----' delimiters stay unambiguous.
    """
    return "N" + secrets.token_hex(4) + "!a7#" + secrets.token_hex(4)


def _sanitize_file_segment(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    out: list[str] = []
    for ch in value:
        if (
            ("a" <= ch <= "z")
            or ("A" <= ch <= "Z")
            or ("0" <= ch <= "9")
            or ch in {"@", ".", "_", "-"}
        ):
            out.append(ch)
        else:
            out.append("-")
    return "".join(out).strip("-")


def credential_file_name(email: str = "", sub: str = "") -> str:
    email_s = _sanitize_file_segment(email)
    if email_s:
        return f"fishaudio-{email_s}.json"
    sub_s = _sanitize_file_segment(sub)
    if sub_s:
        return f"fishaudio-{sub_s}.json"
    ts = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    return f"fishaudio-{ts}.json"


def build_auth_payload(
    *,
    email: str,
    token: str,
    user_id: str = "",
    active_team_id: str = "",
    active_workspace_id: str = "",
    session_ttl_sec: int = DEFAULT_SESSION_TTL_SEC,
    now: datetime | None = None,
) -> dict[str, Any]:
    token = (token or "").strip()
    if not token:
        raise ValueError("token is required")
    email = (email or "").strip()
    if not email:
        raise ValueError("email is required")

    project_id = (user_id or active_team_id or active_workspace_id or "").strip()
    if not project_id:
        raise ValueError("user_id / active_team_id is required")

    current = now or datetime.now(tz=timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    ttl = max(60, int(session_ttl_sec or DEFAULT_SESSION_TTL_SEC))
    expired_at = current + timedelta(seconds=ttl)

    return {
        "type": "fishaudio",
        "access_token": token,
        "refresh_token": token,
        "project_id": project_id,
        "device_id": project_id,
        "sub": project_id,
        "email": email,
        "auth_kind": "session",
        "expired": expired_at.isoformat().replace("+00:00", "Z"),
        "last_refresh": current.isoformat().replace("+00:00", "Z"),
    }


def write_auth_file(auth_dir: str, payload: dict[str, Any]) -> str:
    auth_dir = os.path.abspath(str(auth_dir or "").strip() or "fish_auths")
    os.makedirs(auth_dir, exist_ok=True)
    path = os.path.join(
        auth_dir,
        credential_file_name(email=str(payload.get("email") or ""), sub=str(payload.get("sub") or "")),
    )
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def _page_text(page) -> str:
    try:
        return str(page.run_js("return (document.body && document.body.innerText) || ''") or "")
    except Exception:
        return ""


def _page_url(page) -> str:
    try:
        return str(page.url or "")
    except Exception:
        try:
            return str(page.run_js("return location.href") or "")
        except Exception:
            return ""


def detect_step(page) -> str:
    """Return email|otp|password|app|unknown based on visible page state."""
    url = _page_url(page).lower()
    if "/app" in url and "/auth" not in url:
        return "app"
    try:
        state = page.run_js(
            """
return (() => {
  const text = ((document.body && document.body.innerText) || '').replace(/\\s+/g, ' ');
  const hasEmail = !!document.querySelector('input[name="email"], input[type="email"]');
  const hasOtp = !!document.querySelector('input[data-input-otp], input[autocomplete="one-time-code"]');
  const passwords = Array.from(document.querySelectorAll('input[type="password"]'))
    .filter((n) => {
      const style = window.getComputedStyle(n);
      return style.display !== 'none' && style.visibility !== 'hidden' && n.offsetParent !== null;
    });
  if (passwords.length >= 1 && /密码|password/i.test(text)) return 'password';
  if (hasOtp || /验证码|verification code|otp/i.test(text)) return 'otp';
  if (hasEmail || /创建帐户|创建账户|create.*account|email/i.test(text)) return 'email';
  if (location.pathname.includes('/app')) return 'app';
  return 'unknown';
})()
"""
        )
        return str(state or "unknown")
    except Exception:
        text = _page_text(page)
        if re.search(r"验证码|verification code", text, re.I):
            return "otp"
        if re.search(r"密码|password", text, re.I):
            return "password"
        if re.search(r"创建帐户|创建账户|email", text, re.I):
            return "email"
        return "unknown"


def open_signup_page(page, log_callback: LogFn = None, cancel_callback: CancelFn = None, timeout: float = 45):
    _raise_if_cancelled(cancel_callback)
    _emit(log_callback, f"[*] 打开 Fish Audio 注册页: {SIGNUP_URL}")
    page.get(SIGNUP_URL)
    deadline = time.time() + timeout
    while time.time() < deadline:
        _raise_if_cancelled(cancel_callback)
        # If already logged in, clear storage and retry once.
        url = _page_url(page)
        if "/app" in url and "/auth" not in url:
            _emit(log_callback, "[!] 检测到已登录会话，清理后重开注册页")
            try:
                page.run_js(
                    """
try { localStorage.clear(); } catch (e) {}
try { sessionStorage.clear(); } catch (e) {}
"""
                )
            except Exception:
                pass
            try:
                page.set.cookies.clear()
            except Exception:
                try:
                    page.run_js("document.cookie.split(';').forEach(c => { const n = c.split('=')[0].trim(); document.cookie = n + '=;expires=Thu, 01 Jan 1970 00:00:00 GMT;path=/'; });")
                except Exception:
                    pass
            page.get(SIGNUP_URL)
            _sleep(1.0, cancel_callback)
            continue
        step = detect_step(page)
        if step in ("email", "otp", "password"):
            _emit(log_callback, f"[*] 注册页就绪，当前步骤: {step}")
            return step
        _sleep(0.5, cancel_callback)
    raise FishRegistrationError(f"打开注册页超时: {_page_url(page)}")


def submit_email(page, email: str, log_callback: LogFn = None, cancel_callback: CancelFn = None, timeout: float = 60):
    email = (email or "").strip()
    if not email:
        raise FishRegistrationError("邮箱为空")
    deadline = time.time() + timeout
    submitted = False
    while time.time() < deadline:
        _raise_if_cancelled(cancel_callback)
        step = detect_step(page)
        if step == "otp":
            _emit(log_callback, "[*] 已进入验证码步骤")
            return
        if step == "password":
            _emit(log_callback, "[*] 已跳过验证码，直接进入密码步骤")
            return
        result = page.run_js(
            """
const email = String(arguments[0] || '').trim();
return (() => {
  function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }
  function setValue(node, value) {
    node.focus();
    try { node.click(); } catch (e) {}
    const proto = node instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
    const tracker = node._valueTracker;
    if (tracker) tracker.setValue('');
    if (setter) setter.call(node, value); else node.value = value;
    node.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, data: value, inputType: 'insertText' }));
    node.dispatchEvent(new InputEvent('input', { bubbles: true, data: value, inputType: 'insertText' }));
    node.dispatchEvent(new Event('change', { bubbles: true }));
    return (node.value || '').trim() === value;
  }
  const input = document.querySelector('input[name="email"], input[type="email"]');
  if (!input || !isVisible(input)) return { state: 'not-ready' };
  if (!setValue(input, email)) return { state: 'fill-failed' };
  const buttons = Array.from(document.querySelectorAll('button, [role="button"]'))
    .filter((n) => isVisible(n) && !n.disabled);
  const submit = buttons.find((n) => /继续|continue|下一步|next/i.test((n.innerText || n.textContent || '').trim()))
    || document.querySelector('button[type="submit"]');
  if (!submit) return { state: 'no-submit', filled: true };
  submit.click();
  return { state: 'submitted', filled: true };
})()
            """,
            email,
        )
        state = ""
        if isinstance(result, dict):
            state = str(result.get("state") or "")
        else:
            state = str(result or "")
        if state == "submitted":
            submitted = True
            _emit(log_callback, "[*] 已提交邮箱，等待验证码步骤 / reCAPTCHA")
        elif state == "fill-failed":
            _emit(log_callback, "[Debug] 邮箱填入失败，重试")
        _sleep(1.0, cancel_callback)
    if submitted:
        raise FishRegistrationError("已提交邮箱，但未进入验证码步骤（可能被 reCAPTCHA 或域名拦截）")
    raise FishRegistrationError("提交邮箱超时")


def submit_otp(page, code: str, log_callback: LogFn = None, cancel_callback: CancelFn = None, timeout: float = 60):
    code = re.sub(r"\D", "", str(code or ""))
    if len(code) != 6:
        raise FishRegistrationError(f"Fish OTP 需要 6 位数字，收到: {code!r}")
    deadline = time.time() + timeout
    submitted = False
    while time.time() < deadline:
        _raise_if_cancelled(cancel_callback)
        step = detect_step(page)
        if step == "password":
            _emit(log_callback, "[*] 验证码已通过，进入密码步骤")
            return
        if step == "app":
            _emit(log_callback, "[*] 验证码后已进入应用页")
            return
        result = page.run_js(
            """
const code = String(arguments[0] || '').trim();
return (() => {
  function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }
  function setValue(node, value) {
    node.focus();
    try { node.click(); } catch (e) {}
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
    const tracker = node._valueTracker;
    if (tracker) tracker.setValue('');
    if (setter) setter.call(node, value); else node.value = value;
    node.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, data: value, inputType: 'insertText' }));
    node.dispatchEvent(new InputEvent('input', { bubbles: true, data: value, inputType: 'insertText' }));
    node.dispatchEvent(new Event('change', { bubbles: true }));
    return (node.value || '').replace(/\\s+/g, '') === value;
  }
  const otp = document.querySelector('input[data-input-otp], input[autocomplete="one-time-code"]')
    || Array.from(document.querySelectorAll('input[type="text"], input:not([type])'))
         .find((n) => isVisible(n) && !n.disabled);
  if (!otp) return { state: 'not-ready' };
  if (!setValue(otp, code)) return { state: 'fill-failed', value: otp.value || '' };
  const buttons = Array.from(document.querySelectorAll('button, [role="button"]'))
    .filter((n) => isVisible(n) && !n.disabled);
  const submit = buttons.find((n) => /继续|continue|验证|verify|下一步|next/i.test((n.innerText || '').trim()))
    || document.querySelector('button[type="submit"]');
  if (!submit) return { state: 'no-submit', filled: true };
  submit.click();
  return { state: 'submitted' };
})()
            """,
            code,
        )
        state = str((result or {}).get("state") if isinstance(result, dict) else result or "")
        if state == "submitted":
            submitted = True
            _emit(log_callback, "[*] 已提交验证码")
        _sleep(1.0, cancel_callback)
    if submitted:
        raise FishRegistrationError("已提交验证码，但未进入密码步骤")
    raise FishRegistrationError("提交验证码超时")


def submit_password(page, password: str, log_callback: LogFn = None, cancel_callback: CancelFn = None, timeout: float = 90):
    password = str(password or "")
    if len(password) < 8:
        raise FishRegistrationError("密码过短")
    deadline = time.time() + timeout
    submitted = False
    while time.time() < deadline:
        _raise_if_cancelled(cancel_callback)
        step = detect_step(page)
        if step == "app":
            _emit(log_callback, "[*] 注册完成，已进入应用页")
            return
        # token may appear before navigation finishes
        try:
            token = page.run_js("return localStorage.getItem('token') || ''")
            if token:
                _emit(log_callback, "[*] 已检测到 session token")
                return
        except Exception:
            pass
        result = page.run_js(
            """
const password = String(arguments[0] || '');
return (() => {
  function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }
  function setValue(node, value) {
    node.focus();
    try { node.click(); } catch (e) {}
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
    const tracker = node._valueTracker;
    if (tracker) tracker.setValue('');
    if (setter) setter.call(node, value); else node.value = value;
    node.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, data: value, inputType: 'insertText' }));
    node.dispatchEvent(new InputEvent('input', { bubbles: true, data: value, inputType: 'insertText' }));
    node.dispatchEvent(new Event('change', { bubbles: true }));
    return (node.value || '') === value;
  }
  const inputs = Array.from(document.querySelectorAll('input[type="password"]')).filter((n) => isVisible(n) && !n.disabled);
  if (!inputs.length) return { state: 'not-ready' };
  const first = inputs[0];
  const second = inputs[1] || inputs[0];
  if (!setValue(first, password)) return { state: 'fill-failed' };
  if (!setValue(second, password)) return { state: 'fill-failed-confirm' };
  const buttons = Array.from(document.querySelectorAll('button, [role="button"]'))
    .filter((n) => isVisible(n) && !n.disabled);
  const submit = buttons.find((n) => /注册|创建|continue|继续|sign\\s*up|register|submit/i.test((n.innerText || '').trim()))
    || document.querySelector('button[type="submit"]');
  if (!submit) return { state: 'no-submit', filled: true };
  submit.click();
  return { state: 'submitted', passwordCount: inputs.length };
})()
            """,
            password,
        )
        state = str((result or {}).get("state") if isinstance(result, dict) else result or "")
        if state == "submitted":
            submitted = True
            _emit(log_callback, "[*] 已提交密码，等待注册完成 / reCAPTCHA")
        _sleep(1.2, cancel_callback)
    if submitted:
        raise FishRegistrationError("已提交密码，但未检测到登录成功")
    raise FishRegistrationError("提交密码超时")


def harvest_session(page, timeout: float = 45, log_callback: LogFn = None, cancel_callback: CancelFn = None) -> dict[str, str]:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        _raise_if_cancelled(cancel_callback)
        try:
            data = page.run_js(
                """
return (() => {
  const token = localStorage.getItem('token') || '';
  const team = localStorage.getItem('active_team_id')
    || (document.cookie.match(/(?:^|;\\s*)active_team_id=([^;]+)/) || [])[1]
    || '';
  const workspace = localStorage.getItem('active_workspace_id')
    || (document.cookie.match(/(?:^|;\\s*)active_workspace_id=([^;]+)/) || [])[1]
    || '';
  let userId = '';
  try {
    const cache = JSON.parse(localStorage.getItem('user-info-cache-v1') || 'null');
    userId = (cache && cache.user && cache.user._id) || '';
  } catch (e) {}
  return {
    token: String(token || '').trim(),
    active_team_id: decodeURIComponent(String(team || '').trim()),
    active_workspace_id: decodeURIComponent(String(workspace || '').trim()),
    user_id: String(userId || '').trim(),
    url: location.href,
  };
})()
"""
            )
        except Exception as exc:
            data = {"error": str(exc)}
        if isinstance(data, dict):
            last = {k: str(v or "") for k, v in data.items()}
            token = last.get("token") or ""
            user_id = last.get("user_id") or last.get("active_team_id") or last.get("active_workspace_id") or ""
            if token and user_id:
                # Fish often writes user_id first; team/workspace may lag or equal user_id.
                last["user_id"] = user_id
                last["active_team_id"] = last.get("active_team_id") or user_id
                last["active_workspace_id"] = last.get("active_workspace_id") or user_id
                _emit(log_callback, f"[*] 已收割 session: team={last.get('active_team_id','')[:12]}...")
                return last
        _sleep(0.8, cancel_callback)
    raise FishRegistrationError(f"收割登录态超时: {last}")


def extract_fish_otp(text: str, subject: str = "") -> str | None:
    """Extract a 6-digit Fish Audio OTP from mail subject/body."""
    blob = f"{subject or ''}\n{text or ''}"
    patterns = [
        r"(?:验证码|校[验驗]码|one[-\s]?time(?:\s+pass(?:word|code))?|otp|verification\s+code|security\s+code)[^\d]{0,20}(\d{6})",
        r"\b(\d{6})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, blob, re.IGNORECASE)
        if match:
            return match.group(1)
    return None
