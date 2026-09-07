"""Mail.tm and 1secMail temporary mailbox helpers."""

from __future__ import annotations

import re
import secrets
import string
import time
from typing import Any, Callable
from urllib.parse import urlsplit

LogFn = Callable[[str], None] | None
CancelFn = Callable[[], bool] | None
SleepFn = Callable[[float, CancelFn], None]
HttpGetFn = Callable[..., Any]
HttpPostFn = Callable[..., Any]
ExtractFn = Callable[[str, str], str | None]


DEFAULT_MAILTM_API_BASE = "https://api.mail.tm"
DEFAULT_ONESECMAIL_API_BASE = "https://www.1secmail.com/api/v1/"


def _normalize_base(url: str, default: str) -> str:
    raw = str(url or default or "").strip() or default
    return raw.rstrip("/")


def _json_list(payload: Any) -> list:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("hydra:member", "member", "items", "data", "domains", "messages"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def _random_local(length: int = 10) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


# ---- Mail.tm ---------------------------------------------------------------


def mailtm_headers(token: str | None = None, content_type: bool = False) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if content_type:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def mailtm_get_domains(http_get: HttpGetFn, api_base: str = DEFAULT_MAILTM_API_BASE) -> list[dict]:
    base = _normalize_base(api_base, DEFAULT_MAILTM_API_BASE)
    resp = http_get(f"{base}/domains", headers=mailtm_headers())
    resp.raise_for_status()
    domains = _json_list(resp.json())
    active = []
    for item in domains:
        if not isinstance(item, dict):
            continue
        domain = str(item.get("domain") or "").strip()
        if not domain:
            continue
        if item.get("isActive") is False or item.get("isPrivate") is True:
            continue
        active.append(item)
    return active or [d for d in domains if isinstance(d, dict) and d.get("domain")]


def mailtm_create_email_and_token(
    http_get: HttpGetFn,
    http_post: HttpPostFn,
    api_base: str = DEFAULT_MAILTM_API_BASE,
) -> tuple[str, str]:
    base = _normalize_base(api_base, DEFAULT_MAILTM_API_BASE)
    domains = mailtm_get_domains(http_get, api_base=base)
    if not domains:
        raise Exception("Mail.tm 无可用域名")
    domain = str(domains[0].get("domain") or "").strip()
    if not domain:
        raise Exception("Mail.tm 域名数据异常")
    address = f"{_random_local(10)}@{domain}"
    password = secrets.token_urlsafe(12)
    create = http_post(
        f"{base}/accounts",
        json={"address": address, "password": password},
        headers=mailtm_headers(content_type=True),
    )
    if create.status_code not in (200, 201):
        raise Exception(f"Mail.tm 创建邮箱失败 HTTP {create.status_code}: {str(create.text)[:200]}")
    token_resp = http_post(
        f"{base}/token",
        json={"address": address, "password": password},
        headers=mailtm_headers(content_type=True),
    )
    token_resp.raise_for_status()
    data = token_resp.json() if token_resp.text else {}
    token = ""
    if isinstance(data, dict):
        token = str(data.get("token") or "").strip()
    if not token:
        raise Exception("Mail.tm 获取 token 失败")
    return address, token


def mailtm_get_messages(http_get: HttpGetFn, token: str, api_base: str = DEFAULT_MAILTM_API_BASE) -> list[dict]:
    base = _normalize_base(api_base, DEFAULT_MAILTM_API_BASE)
    resp = http_get(f"{base}/messages", headers=mailtm_headers(token=token))
    resp.raise_for_status()
    return [m for m in _json_list(resp.json()) if isinstance(m, dict)]


def mailtm_get_message_detail(
    http_get: HttpGetFn,
    token: str,
    message_id: str,
    api_base: str = DEFAULT_MAILTM_API_BASE,
) -> dict:
    base = _normalize_base(api_base, DEFAULT_MAILTM_API_BASE)
    resp = http_get(f"{base}/messages/{message_id}", headers=mailtm_headers(token=token))
    resp.raise_for_status()
    data = resp.json() if resp.text else {}
    return data if isinstance(data, dict) else {}


def mailtm_get_oai_code(
    *,
    http_get: HttpGetFn,
    token: str,
    email: str,
    extract_code: ExtractFn,
    sleep_with_cancel: SleepFn,
    raise_if_cancelled: Callable[[CancelFn], None],
    api_base: str = DEFAULT_MAILTM_API_BASE,
    timeout: float = 180,
    poll_interval: float = 3,
    log_callback: LogFn = None,
    cancel_callback: CancelFn = None,
) -> str:
    deadline = time.time() + timeout
    seen_ids: set[str] = set()
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        try:
            messages = mailtm_get_messages(http_get, token, api_base=api_base)
        except Exception as exc:
            if log_callback:
                log_callback(f"[Debug] Mail.tm 拉取邮件列表失败: {exc}")
            sleep_with_cancel(poll_interval, cancel_callback)
            continue
        for msg in messages:
            msg_id = str(msg.get("id") or msg.get("@id") or "").split("/")[-1].strip()
            if not msg_id or msg_id in seen_ids:
                continue
            seen_ids.add(msg_id)
            recipients = []
            for item in msg.get("to") or []:
                if isinstance(item, dict):
                    recipients.append(str(item.get("address") or "").lower())
                else:
                    recipients.append(str(item).lower())
            if recipients and email.lower() not in recipients:
                # Some payloads omit recipients on list endpoint; still try detail.
                pass
            try:
                detail = mailtm_get_message_detail(http_get, token, msg_id, api_base=api_base)
            except Exception as exc:
                if log_callback:
                    log_callback(f"[Debug] Mail.tm 获取邮件详情失败: {exc}")
                continue
            parts = []
            text_body = detail.get("text") or ""
            if text_body:
                parts.append(str(text_body))
            html_list = detail.get("html") or []
            if isinstance(html_list, str):
                html_list = [html_list]
            for html in html_list:
                parts.append(re.sub(r"<[^>]+>", " ", str(html)))
            subject = str(detail.get("subject") or msg.get("subject") or "")
            combined = "\n".join(parts)
            if log_callback:
                log_callback(f"[Debug] Mail.tm 收到邮件: {subject}")
            code = extract_code(combined, subject)
            if code:
                if log_callback:
                    log_callback(f"[*] Mail.tm 从邮件中提取到验证码: {code}")
                return code
        sleep_with_cancel(poll_interval, cancel_callback)
    raise Exception(f"Mail.tm 在 {int(timeout)}s 内未收到验证码邮件")


# ---- 1secMail --------------------------------------------------------------


def onesecmail_split_address(email: str) -> tuple[str, str]:
    email = str(email or "").strip()
    if "@" not in email:
        raise Exception(f"1secMail 邮箱格式错误: {email}")
    login, domain = email.split("@", 1)
    login = login.strip()
    domain = domain.strip()
    if not login or not domain:
        raise Exception(f"1secMail 邮箱格式错误: {email}")
    return login, domain


def onesecmail_encode_token(email: str) -> str:
    login, domain = onesecmail_split_address(email)
    return f"1secmail:{login}@{domain}"


def onesecmail_decode_token(token: str, email: str = "") -> tuple[str, str]:
    raw = str(token or "").strip()
    if raw.startswith("1secmail:"):
        raw = raw[len("1secmail:") :]
    if "@" in raw:
        return onesecmail_split_address(raw)
    if email:
        return onesecmail_split_address(email)
    raise Exception("1secMail token 无效，缺少 login@domain")


def onesecmail_create_email_and_token(
    http_get: HttpGetFn,
    api_base: str = DEFAULT_ONESECMAIL_API_BASE,
) -> tuple[str, str]:
    base = _normalize_base(api_base, DEFAULT_ONESECMAIL_API_BASE)
    # Keep trailing path style used by upstream docs.
    if not base.endswith("/api/v1") and not base.endswith("/v1"):
        # allow full endpoint root
        pass
    url = base if base.endswith("/") else base + "/"
    # Prefer random mailbox endpoint.
    resp = http_get(url, params={"action": "genRandomMailbox", "count": 1})
    if resp.status_code == 403:
        raise Exception(
            "1secMail API 返回 403（官方接口当前常被拦截）。"
            "可在配置中更换 onesecmail_api_base，或改用 mailtm / duckmail / cloudflare。"
        )
    resp.raise_for_status()
    try:
        data = resp.json()
    except Exception as exc:
        raise Exception(f"1secMail 返回非 JSON: {str(resp.text)[:200]}") from exc
    address = ""
    if isinstance(data, list) and data:
        address = str(data[0] or "").strip()
    elif isinstance(data, str):
        address = data.strip()
    if not address or "@" not in address:
        raise Exception(f"1secMail 创建邮箱失败: {data!r}")
    return address, onesecmail_encode_token(address)


def onesecmail_get_messages(
    http_get: HttpGetFn,
    login: str,
    domain: str,
    api_base: str = DEFAULT_ONESECMAIL_API_BASE,
) -> list[dict]:
    base = _normalize_base(api_base, DEFAULT_ONESECMAIL_API_BASE)
    url = base if base.endswith("/") else base + "/"
    resp = http_get(
        url,
        params={"action": "getMessages", "login": login, "domain": domain},
    )
    if resp.status_code == 403:
        raise Exception("1secMail getMessages 返回 403")
    resp.raise_for_status()
    data = resp.json() if resp.text else []
    return [m for m in (data if isinstance(data, list) else []) if isinstance(m, dict)]


def onesecmail_read_message(
    http_get: HttpGetFn,
    login: str,
    domain: str,
    message_id: str | int,
    api_base: str = DEFAULT_ONESECMAIL_API_BASE,
) -> dict:
    base = _normalize_base(api_base, DEFAULT_ONESECMAIL_API_BASE)
    url = base if base.endswith("/") else base + "/"
    resp = http_get(
        url,
        params={
            "action": "readMessage",
            "login": login,
            "domain": domain,
            "id": message_id,
        },
    )
    resp.raise_for_status()
    data = resp.json() if resp.text else {}
    return data if isinstance(data, dict) else {}


def onesecmail_get_oai_code(
    *,
    http_get: HttpGetFn,
    token: str,
    email: str,
    extract_code: ExtractFn,
    sleep_with_cancel: SleepFn,
    raise_if_cancelled: Callable[[CancelFn], None],
    api_base: str = DEFAULT_ONESECMAIL_API_BASE,
    timeout: float = 180,
    poll_interval: float = 3,
    log_callback: LogFn = None,
    cancel_callback: CancelFn = None,
) -> str:
    login, domain = onesecmail_decode_token(token, email=email)
    deadline = time.time() + timeout
    seen_ids: set[str] = set()
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        try:
            messages = onesecmail_get_messages(http_get, login, domain, api_base=api_base)
        except Exception as exc:
            if log_callback:
                log_callback(f"[Debug] 1secMail 拉取邮件列表失败: {exc}")
            sleep_with_cancel(poll_interval, cancel_callback)
            continue
        for msg in messages:
            msg_id = str(msg.get("id") or "").strip()
            if not msg_id or msg_id in seen_ids:
                continue
            seen_ids.add(msg_id)
            try:
                detail = onesecmail_read_message(
                    http_get, login, domain, msg_id, api_base=api_base
                )
            except Exception as exc:
                if log_callback:
                    log_callback(f"[Debug] 1secMail 获取邮件详情失败: {exc}")
                continue
            parts = []
            for key in ("textBody", "body", "htmlBody"):
                value = detail.get(key)
                if isinstance(value, str) and value.strip():
                    if "html" in key.lower():
                        parts.append(re.sub(r"<[^>]+>", " ", value))
                    else:
                        parts.append(value)
            subject = str(detail.get("subject") or msg.get("subject") or "")
            combined = "\n".join(parts)
            if log_callback:
                log_callback(f"[Debug] 1secMail 收到邮件: {subject}")
            code = extract_code(combined, subject)
            if code:
                if log_callback:
                    log_callback(f"[*] 1secMail 从邮件中提取到验证码: {code}")
                return code
        sleep_with_cancel(poll_interval, cancel_callback)
    raise Exception(f"1secMail 在 {int(timeout)}s 内未收到验证码邮件")


def validate_provider_base(provider: str, values: dict) -> None:
    """Optional URL validation for GUI/config collect."""
    if provider == "mailtm":
        raw = str(values.get("mailtm_api_base") or DEFAULT_MAILTM_API_BASE).strip()
        parsed = urlsplit(raw)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("Mail.tm API Base 需要填写有效的 http:// 或 https:// 地址。")
    if provider == "onesecmail":
        raw = str(values.get("onesecmail_api_base") or DEFAULT_ONESECMAIL_API_BASE).strip()
        parsed = urlsplit(raw)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("1secMail API Base 需要填写有效的 http:// 或 https:// 地址。")
