"""ElevenLabs browser signup and local auth writer.

Auth model (from frontend reverse):
1. POST https://api.us.elevenlabs.io/v1/user/pre-sign-up
   with email + account_metadata + recaptcha_token (hCaptcha) + optional Stripe Radar.
2. Firebase Auth createUserWithEmailAndPassword (project xi-labs).
   Blocking Function validates the pre-registered hCaptcha and usually requires email verify.
3. App API calls use Authorization: Bearer <Firebase ID token>
   or xi-api-key from POST /v1/user/create-api-key.

This module mirrors platforms/fishaudio.py: browser form helpers + local JSON auth export.
hCaptcha solving and full email-verify loop still need integration in the register orchestrator.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import unquote

SIGNUP_URL = "https://elevenlabs.io/app/sign-up"
SIGNIN_URL = "https://elevenlabs.io/app/sign-in"
API_BASE = "https://api.us.elevenlabs.io"
APP_URL_HINT = "/app/"

# Public web Firebase config from frontend env chunk (prod).
FIREBASE_API_KEY = "AIzaSyBSsRE_1Os04-bxpd5JTLIniy3UK4OqKys"
FIREBASE_AUTH_DOMAIN = "elevenlabs.io"
FIREBASE_PROJECT_ID = "xi-labs"
FIREBASE_APP_ID = "1:265222077342:web:3acce90d1596672570348f"

# Invisible hCaptcha sitekeys from getEnv().
HCAPTCHA_SIGNUP_SITEKEY = "3aad1500-7e79-4051-aac5-6852324dab76"
HCAPTCHA_SIGNIN_SITEKEY = "92e8aac8-4372-4358-9eae-f3e842981bd0"

DEFAULT_SESSION_TTL_SEC = 3600  # Firebase ID tokens are short-lived; refresh separately.
DEFAULT_AUTH_DIR = "eleven_auths"

LogFn = Callable[[str], None] | None
CancelFn = Callable[[], bool] | None


class ElevenRegistrationError(Exception):
    """Raised when an ElevenLabs signup step fails."""


class ElevenCancelled(ElevenRegistrationError):
    """Raised when the user requests stop during ElevenLabs signup."""


def _raise_if_cancelled(cancel_callback: CancelFn):
    if cancel_callback and cancel_callback():
        raise ElevenCancelled("用户停止注册")


def _sleep(seconds: float, cancel_callback: CancelFn = None):
    end = time.time() + max(0.0, float(seconds))
    while time.time() < end:
        _raise_if_cancelled(cancel_callback)
        time.sleep(min(0.2, end - time.time()))


def _emit(log_callback: LogFn, message: str):
    if log_callback:
        log_callback(message)


def generate_password() -> str:
    """Password accepted by ElevenLabs signup UI rules.

    Requires >=8 letters, at least one number, at least one special character.
    Avoid '-' so account lines using '----' delimiters stay unambiguous.
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
        return f"elevenlabs-{email_s}.json"
    sub_s = _sanitize_file_segment(sub)
    if sub_s:
        return f"elevenlabs-{sub_s}.json"
    ts = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    return f"elevenlabs-{ts}.json"


def build_auth_payload(
    *,
    email: str,
    id_token: str = "",
    refresh_token: str = "",
    user_id: str = "",
    workspace_id: str = "",
    auth_account_id: str = "",
    session_ttl_sec: int = DEFAULT_SESSION_TTL_SEC,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build local auth JSON for ElevenLabs **web** session.

    Stores Firebase ID token as access_token for:
      Authorization: Bearer <access_token>

    This is intentionally not the developer console xi-api-key / sk_ key.
    """
    email = (email or "").strip()
    if not email:
        raise ValueError("email is required")

    id_token = (id_token or "").strip()
    refresh_token = (refresh_token or "").strip()
    if not id_token:
        raise ValueError("id_token is required")

    project_id = (workspace_id or user_id or auth_account_id or "").strip()
    if not project_id:
        raise ValueError("workspace_id / user_id / auth_account_id is required")

    current = now or datetime.now(tz=timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    ttl = max(60, int(session_ttl_sec or DEFAULT_SESSION_TTL_SEC))
    expired_at = current + timedelta(seconds=ttl)

    return {
        "type": "elevenlabs",
        "access_token": id_token,
        "refresh_token": refresh_token or id_token,
        "id_token": id_token,
        "project_id": project_id,
        "device_id": project_id,
        "sub": (auth_account_id or user_id or project_id).strip(),
        "user_id": (user_id or "").strip(),
        "workspace_id": (workspace_id or "").strip(),
        "auth_account_id": (auth_account_id or "").strip(),
        "email": email,
        "auth_kind": "web_firebase_id_token",
        "api_base": API_BASE,
        "firebase_api_key": FIREBASE_API_KEY,
        "expired": expired_at.isoformat().replace("+00:00", "Z"),
        "last_refresh": current.isoformat().replace("+00:00", "Z"),
    }

def write_auth_file(auth_dir: str, payload: dict[str, Any]) -> str:
    auth_dir = os.path.abspath(str(auth_dir or "").strip() or DEFAULT_AUTH_DIR)
    os.makedirs(auth_dir, exist_ok=True)
    path = os.path.join(
        auth_dir,
        credential_file_name(
            email=str(payload.get("email") or ""),
            sub=str(payload.get("sub") or ""),
        ),
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
    """Return signup|verification|signin|app|onboarding|unknown based on page state."""
    url = _page_url(page).lower()
    if "/app/onboarding" in url:
        return "onboarding"
    if "/app/sign-in" in url:
        return "signin"
    if "/app/sign-up" in url:
        # May already be on the "verification link sent" sub-state.
        pass
    elif "/app/" in url and "/sign-" not in url and "/auth" not in url and "/action" not in url:
        return "app"
    try:
        state = page.run_js(
            """
return (() => {
  const text = ((document.body && document.body.innerText) || '').replace(/\\s+/g, ' ');
  const path = (location.pathname || '').toLowerCase();
  if (path.includes('/app/onboarding')) return 'onboarding';
  if (path.includes('/app/sign-in')) return 'signin';
  if (/we've sent a verification link|verification link to|check your (email|spam)|resend/i.test(text)) {
    return 'verification';
  }
  if (/verify your email|email verification|almost there! please sign in/i.test(text)) {
    return 'verification';
  }
  if (path.includes('/app/sign-up')) return 'signup';
  if (path.includes('/app/') && !path.includes('/sign-') && !path.includes('/action')) return 'app';
  const hasEmail = !!document.querySelector('input[name="email"], input[type="email"]');
  const hasPassword = !!document.querySelector('input[name="password"], input[type="password"]');
  if (hasEmail && hasPassword && /create an account|sign up/i.test(text)) return 'signup';
  if (hasEmail && hasPassword && /welcome back|sign in/i.test(text)) return 'signin';
  return 'unknown';
})()
"""
        )
        return str(state or "unknown")
    except Exception:
        text = _page_text(page)
        if re.search(r"verification link|verify your email|email verification", text, re.I):
            return "verification"
        if re.search(r"welcome back|sign in", text, re.I):
            return "signin"
        if re.search(r"create an account|sign up", text, re.I):
            return "signup"
        return "unknown"

def open_signup_page(page, log_callback: LogFn = None, cancel_callback: CancelFn = None, timeout: float = 45):
    _raise_if_cancelled(cancel_callback)
    _emit(log_callback, f"[*] 打开 ElevenLabs 注册页: {SIGNUP_URL}")
    page.get(SIGNUP_URL)
    deadline = time.time() + timeout
    while time.time() < deadline:
        _raise_if_cancelled(cancel_callback)
        url = _page_url(page)
        # Logged-in sessions redirect /app/sign-up → /app/home.
        if "/app/" in url and "/sign-" not in url:
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
                    page.run_js(
                        """
document.cookie.split(';').forEach((c) => {
  const n = c.split('=')[0].trim();
  document.cookie = n + '=;expires=Thu, 01 Jan 1970 00:00:00 GMT;path=/;domain=.elevenlabs.io';
  document.cookie = n + '=;expires=Thu, 01 Jan 1970 00:00:00 GMT;path=/';
});
"""
                    )
                except Exception:
                    pass
            page.get(SIGNUP_URL)
            _sleep(1.0, cancel_callback)
            continue
        step = detect_step(page)
        if step in ("signup", "verification"):
            _emit(log_callback, f"[*] 注册页就绪，当前步骤: {step}")
            return step
        _sleep(0.5, cancel_callback)
    raise ElevenRegistrationError(f"打开注册页超时: {_page_url(page)}")


def submit_signup_form(
    page,
    email: str,
    password: str,
    log_callback: LogFn = None,
    cancel_callback: CancelFn = None,
    timeout: float = 90,
):
    """Fill email/password, accept terms, click Sign up.

    Downstream network flow (handled by page JS):
    - invisible hCaptcha → token
    - optional Stripe Radar session
    - POST /v1/user/pre-sign-up
    - Firebase accounts:signUp
    """
    email = (email or "").strip()
    password = str(password or "")
    if not email:
        raise ElevenRegistrationError("邮箱为空")
    if len(password) < 8:
        raise ElevenRegistrationError("密码过短")

    deadline = time.time() + timeout
    submitted = False
    while time.time() < deadline:
        _raise_if_cancelled(cancel_callback)
        step = detect_step(page)
        if step in ("verification", "app", "onboarding", "signin"):
            _emit(log_callback, f"[*] 注册表单已通过，当前步骤: {step}")
            return step

        result = page.run_js(
            """
const email = String(arguments[0] || '').trim();
const password = String(arguments[1] || '');
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
    return (node.value || '') === value;
  }
  const emailInput = document.querySelector('input[name="email"], input[type="email"], input[data-testid="sign-up-email-input"]');
  const passwordInput = document.querySelector('input[name="password"], input[type="password"]');
  if (!emailInput || !passwordInput || !isVisible(emailInput) || !isVisible(passwordInput)) {
    return { state: 'not-ready' };
  }
  if (!setValue(emailInput, email)) return { state: 'fill-email-failed' };
  if (!setValue(passwordInput, password)) return { state: 'fill-password-failed' };

  const terms = document.querySelector('input[name="terms"]');
  if (terms && !terms.checked) {
    try {
      const clickable = terms.closest('label,button,div') || terms;
      clickable.click();
    } catch (e) {}
    if (!terms.checked) {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'checked')?.set;
      if (setter) setter.call(terms, true);
      terms.dispatchEvent(new Event('click', { bubbles: true }));
      terms.dispatchEvent(new Event('input', { bubbles: true }));
      terms.dispatchEvent(new Event('change', { bubbles: true }));
    }
  }
  const termsOk = !terms || !!terms.checked;

  const buttons = Array.from(document.querySelectorAll('button, [role="button"]'))
    .filter((n) => isVisible(n) && !n.disabled);
  const submit = buttons.find((n) => /^\\s*sign\\s*up\\s*$/i.test((n.innerText || n.textContent || '').trim()))
    || document.querySelector('button[type="submit"]');
  if (!submit) return { state: 'no-submit', filled: true, termsOk };
  if (!termsOk) return { state: 'terms-required', filled: true };
  submit.click();
  return { state: 'submitted', filled: true, termsOk: true };
})()
            """,
            email,
            password,
        )
        state = ""
        if isinstance(result, dict):
            state = str(result.get("state") or "")
        else:
            state = str(result or "")

        if state == "submitted":
            submitted = True
            _emit(log_callback, "[*] 已提交注册表单，等待 hCaptcha / pre-sign-up / Firebase")
        elif state == "terms-required":
            _emit(log_callback, "[Debug] 条款未勾选，重试")
        elif state in ("fill-email-failed", "fill-password-failed"):
            _emit(log_callback, f"[Debug] 表单填写失败: {state}")
        _sleep(1.2, cancel_callback)

    if submitted:
        raise ElevenRegistrationError(
            "已提交注册表单，但未进入验证邮箱/应用页（常见阻塞：invisible hCaptcha 或 Firebase Blocking Function）"
        )
    raise ElevenRegistrationError("提交注册表单超时")


def harvest_session(page, timeout: float = 60, log_callback: LogFn = None, cancel_callback: CancelFn = None) -> dict[str, str]:
    """Harvest Firebase ID token + xi_website_user cookie fields from the page."""
    deadline = time.time() + timeout
    last: dict[str, str] = {}
    while time.time() < deadline:
        _raise_if_cancelled(cancel_callback)
        try:
            data = page.run_js(
                f"""
return (async () => {{
  const out = {{
    id_token: '',
    refresh_token: '',
    user_id: '',
    workspace_id: '',
    auth_account_id: '',
    email: '',
    url: location.href,
  }};

  // Cookie snapshot written by the app after auth.
  try {{
    const m = document.cookie.match(/(?:^|;\\s*)xi_website_user=([^;]+)/);
    if (m) {{
      const raw = decodeURIComponent(m[1]);
      const obj = JSON.parse(raw);
      out.user_id = String(obj.user_id || '');
      out.workspace_id = String(obj.workspace_id || '');
      out.auth_account_id = String(obj.auth_account_id || '');
      out.email = String(obj.email || '');
    }}
  }} catch (e) {{}}

  // Prefer live Firebase Auth user if exposed on window.
  try {{
    const auth = window.firebase && window.firebase.auth && window.firebase.auth();
    if (auth && auth.currentUser) {{
      out.id_token = await auth.currentUser.getIdToken(false);
      out.auth_account_id = out.auth_account_id || String(auth.currentUser.uid || '');
      out.email = out.email || String(auth.currentUser.email || '');
    }}
  }} catch (e) {{}}

  // IndexedDB firebaseLocalStorage persistence (modular SDK).
  if (!out.id_token) {{
    try {{
      const dbName = 'firebaseLocalStorageDb';
      const idb = await new Promise((resolve, reject) => {{
        const req = indexedDB.open(dbName);
        req.onerror = () => reject(req.error);
        req.onsuccess = () => resolve(req.result);
      }});
      if (idb && idb.objectStoreNames.contains('firebaseLocalStorage')) {{
        const tx = idb.transaction('firebaseLocalStorage', 'readonly');
        const store = tx.objectStore('firebaseLocalStorage');
        const rows = await new Promise((resolve, reject) => {{
          const req = store.getAll();
          req.onerror = () => reject(req.error);
          req.onsuccess = () => resolve(req.result || []);
        }});
        for (const row of rows) {{
          const value = row && row.value;
          if (!value || typeof value !== 'object') continue;
          const sts = value.stsTokenManager || {{}};
          if (sts.accessToken) {{
            out.id_token = String(sts.accessToken || '');
            out.refresh_token = String(sts.refreshToken || '');
            out.auth_account_id = out.auth_account_id || String(value.uid || '');
            out.email = out.email || String(value.email || '');
            break;
          }}
        }}
      }}
      try {{ idb.close(); }} catch (e) {{}}
    }} catch (e) {{}}
  }}

  // localStorage fallback keys.
  if (!out.id_token) {{
    try {{
      for (const key of Object.keys(localStorage)) {{
        if (!key.includes('firebase:authUser')) continue;
        const raw = localStorage.getItem(key);
        if (!raw) continue;
        const value = JSON.parse(raw);
        const sts = value.stsTokenManager || {{}};
        if (sts.accessToken) {{
          out.id_token = String(sts.accessToken || '');
          out.refresh_token = String(sts.refreshToken || '');
          out.auth_account_id = out.auth_account_id || String(value.uid || '');
          out.email = out.email || String(value.email || '');
          break;
        }}
      }}
    }} catch (e) {{}}
  }}

  return out;
}})()
"""
            )
        except Exception as exc:
            data = {"error": str(exc)}

        if isinstance(data, dict):
            last = {k: str(v or "") for k, v in data.items()}
            token = last.get("id_token") or ""
            identity = (
                last.get("workspace_id")
                or last.get("user_id")
                or last.get("auth_account_id")
                or ""
            )
            if token and identity:
                _emit(
                    log_callback,
                    f"[*] 已收割 ElevenLabs session: user={last.get('user_id','')[:12]}...",
                )
                return last
        _sleep(0.8, cancel_callback)
    raise ElevenRegistrationError(f"收割登录态超时: {last}")


def sign_in_form(
    page,
    email: str,
    password: str,
    log_callback: LogFn = None,
    cancel_callback: CancelFn = None,
    timeout: float = 120,
):
    """Fill sign-in form after email verification and wait for app/onboarding."""
    email = (email or "").strip()
    password = str(password or "")
    if not email or not password:
        raise ElevenRegistrationError("登录邮箱或密码为空")

    # Dismiss "Email Verification / Continue" dialog if present.
    try:
        page.run_js(
            """
return (() => {
  const buttons = Array.from(document.querySelectorAll('button'));
  const cont = buttons.find((n) => /^\\s*continue\\s*$/i.test((n.innerText || '').trim()));
  if (cont) { cont.click(); return true; }
  return false;
})()
"""
        )
    except Exception:
        pass

    url = _page_url(page).lower()
    if "/app/sign-in" not in url:
        _emit(log_callback, f"[*] 打开登录页: {SIGNIN_URL}")
        page.get(SIGNIN_URL)
        _sleep(1.0, cancel_callback)

    deadline = time.time() + timeout
    submitted = False
    while time.time() < deadline:
        _raise_if_cancelled(cancel_callback)
        step = detect_step(page)
        if step in ("app", "onboarding"):
            _emit(log_callback, f"[*] 登录成功，当前步骤: {step}")
            return step

        result = page.run_js(
            """
const email = String(arguments[0] || '').trim();
const password = String(arguments[1] || '');
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
    node.dispatchEvent(new InputEvent('input', { bubbles: true, data: value, inputType: 'insertText' }));
    node.dispatchEvent(new Event('change', { bubbles: true }));
    return (node.value || '') === value;
  }
  const emailInput = document.querySelector('input[name="email"], input[type="email"]');
  const passwordInput = document.querySelector('input[name="password"], input[type="password"]');
  if (!emailInput || !passwordInput || !isVisible(emailInput) || !isVisible(passwordInput)) {
    return { state: 'not-ready' };
  }
  if (!setValue(emailInput, email)) return { state: 'fill-email-failed' };
  if (!setValue(passwordInput, password)) return { state: 'fill-password-failed' };
  const buttons = Array.from(document.querySelectorAll('button'))
    .filter((n) => isVisible(n) && !n.disabled);
  const submit = buttons.find((n) => /^\\s*sign\\s*in\\s*$/i.test((n.innerText || '').trim()))
    || document.querySelector('button[type="submit"]');
  if (!submit) return { state: 'no-submit', filled: true };
  submit.click();
  return { state: 'submitted' };
})()
            """,
            email,
            password,
        )
        state = str((result or {}).get("state") if isinstance(result, dict) else result or "")
        if state == "submitted":
            submitted = True
            _emit(log_callback, "[*] 已提交登录表单，等待 hCaptcha / 进入应用")
        _sleep(1.5, cancel_callback)

    if submitted:
        raise ElevenRegistrationError(
            "已提交登录，但未进入应用页（可能被登录 hCaptcha 或二次验证拦截）"
        )
    raise ElevenRegistrationError("登录超时")


def extract_verification_link(text: str, subject: str = "") -> str | None:
    """Extract ElevenLabs / Firebase email verification URL from mail body."""
    blob = f"{subject or ''}\n{text or ''}"
    patterns = [
        r"https?://elevenlabs\.io/app/action\?[^\s\"'<>]*mode=verifyEmail[^\s\"'<>]*",
        r"https?://elevenlabs\.io/[^\s\"'<>]*oobCode=[^\s\"'<>]*",
        r"https?://elevenlabs\.io/[^\s\"'<>]*verify[^\s\"'<>]*",
        r"https?://elevenlabs\.io/[^\s\"'<>]*confirm[^\s\"'<>]*",
        r"https?://[^\s\"'<>]*firebaseapp\.com/[^\s\"'<>]*",
        r"https?://[^\s\"'<>]*__/auth/action[^\s\"'<>]*",
    ]
    for pattern in patterns:
        match = re.search(pattern, blob, re.IGNORECASE)
        if match:
            return unquote(match.group(0).rstrip(").,;\"'").replace("&amp;", "&"))
    return None


def extract_eleven_otp(text: str, subject: str = "") -> str | None:
    """Best-effort 6-digit code extractor if a mail uses OTP instead of a link."""
    blob = f"{subject or ''}\n{text or ''}"
    patterns = [
        r"(?:verification code|security code|one[-\s]?time|otp|验证码)[^\d]{0,24}(\d{6})",
        r"\b(\d{6})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, blob, re.IGNORECASE)
        if match:
            return match.group(1)
    return None
