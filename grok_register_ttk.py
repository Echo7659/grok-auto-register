#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Grok 注册机 - TTK GUI 版本
整合 DrissionPage_example.py, openai_register.py, batch_open_nsfw.py
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import threading
import datetime
import time
import os
import sys
import gc
import queue
import secrets
import struct
import random
import re
import string
import json

os.environ.setdefault("TK_SILENCE_DEPRECATION", "1")

from DrissionPage import Chromium, ChromiumOptions
from DrissionPage.errors import PageDisconnectedError
from curl_cffi import requests

import cf_turnstile


CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
MEMORY_CLEANUP_INTERVAL = 5

UI_BG = "#242424"
UI_PANEL_BG = "#2b2b2b"
UI_FG = "#f2f2f2"
UI_MUTED_FG = "#b8b8b8"
UI_ENTRY_BG = "#333333"
UI_BUTTON_BG = "#3a3a3a"
UI_ACTIVE_BG = "#4a6078"

DEFAULT_CONFIG = {
    "duckmail_api_key": "",
    "cloudflare_api_base": "",
    "cloudflare_api_key": "",
    "cloudflare_auth_mode": "none",
    "cloudflare_path_domains": "/api/domains",
    "cloudflare_path_accounts": "/api/new_address",
    "cloudflare_path_token": "/api/token",
    "cloudflare_path_messages": "/api/mails",
    "proxy": "http://127.0.0.1:7890",
    "enable_nsfw": True,
    "register_count": 1,
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
    "grok2api_auto_add_local": True,
    "grok2api_local_token_file": "",
    "grok2api_pool_name": "ssoBasic",
    "grok2api_auto_add_remote": False,
    "grok2api_remote_base": "",
    "grok2api_remote_app_key": "",
    "cpa_export_enabled": True,
    "cpa_auth_dir": "cpa_auths",
    "cpa_proxy": "",
    "cpa_headless": False,
    "cpa_probe_after_write": True,
    "cpa_mint_timeout_sec": 240,
    "cpa_base_url": "https://cli-chat-proxy.grok.com/v1",
    "cpa_force_standalone": False,
    "cpa_mint_cookie_inject": True,
    "cpa_mint_browser_reuse": True,
    "cpa_mint_browser_recycle_every": 15,
    "cpa_hotload_dir": "",
    "cpa_copy_to_hotload": False,
    "cpa_management_auto_upload": False,
    "cpa_management_base": "",
    "cpa_management_key": "",
    "cpa_management_timeout_sec": 15,
    "cpa_management_use_system_proxy": False,
    "cpa_management_upload_required": False,
    "cpa_server_host": "",
    "cpa_server_user": "root",
    "cpa_server_password": "",
    "cpa_server_auth_dir": "",
    "token_only_file": "",
    "concurrent_count": 1,
    "browser_restart_every": 10,
    "cpa_probe_after_write": False,
    "cpa_mint_async": True,
    "browser_use_custom_ua": False,
    "log_level": "info",
    "speed_log_interval_sec": 60,
    "cf_auto_click": True,
    "keep_cf_cookies": True,
    "cf_os_click": True,
    "cf_turnstile_timeout_sec": 45,
}

config = DEFAULT_CONFIG.copy()
_cf_domain_index = 0
_cf_domain_lock = threading.Lock()
_io_lock = threading.Lock()
_stats_lock = threading.Lock()
_cpa_threads_lock = threading.Lock()

_LOG_LEVEL_RANK = {
    "quiet": 10,
    "info": 20,
    "debug": 30,
}


class RegistrationCancelled(Exception):
    pass


class AccountRetryNeeded(Exception):
    pass


def get_log_level():
    raw = str(config.get("log_level", "info") or "info").strip().lower()
    return raw if raw in _LOG_LEVEL_RANK else "info"


def message_log_rank(message):
    """根据消息内容推断日志级别。"""
    text = str(message or "")
    if "[Debug]" in text:
        return _LOG_LEVEL_RANK["debug"]
    # quiet 仅保留关键进度/结果/警告
    if text.startswith("--- "):
        return _LOG_LEVEL_RANK["info"]
    quiet_prefixes = ("[+]", "[-]", "[!]")
    if text.lstrip().startswith(quiet_prefixes) or any(
        f" {p}" in text[:12] for p in quiet_prefixes
    ):
        return _LOG_LEVEL_RANK["quiet"]
    if "[*] 速度统计" in text or text.lstrip().startswith("[*] 速度统计"):
        return _LOG_LEVEL_RANK["quiet"]
    if any(
        key in text
        for key in (
            "[*] 1.",
            "[*] 2.",
            "[*] 3.",
            "[*] 4.",
            "[*] 5.",
            "[*] 6.",
            "[*] 终端模式",
            "[*] 配置已保存",
            "[*] 任务结束",
            "[*] 注册成功",
            "[+] 注册成功",
            "Worker-",
            "浏览器已启动",
            "开始执行",
            "成功账号将实时保存",
            "按 Ctrl+C",
            "Cloudflare 拦截",
        )
    ):
        return _LOG_LEVEL_RANK["quiet"]
    return _LOG_LEVEL_RANK["info"]


def should_emit_log(message, level=None):
    configured = _LOG_LEVEL_RANK[get_log_level()]
    if level is not None:
        msg_rank = _LOG_LEVEL_RANK.get(str(level).lower(), _LOG_LEVEL_RANK["info"])
    else:
        msg_rank = message_log_rank(message)
    return msg_rank <= configured


def emit_log(log_callback, message, *, level=None):
    if not log_callback:
        return
    if not should_emit_log(message, level=level):
        return
    log_callback(message)


class RateMeter:
    """按固定间隔汇总创建速度（全局一条，避免每 worker 各打一条）。"""

    def __init__(self, interval_sec=60):
        # 允许测试用更短间隔；生产默认 60s
        self.interval_sec = max(float(interval_sec or 60), 1.0)
        self.t0 = time.time()
        self.last_tick = self.t0
        self.last_success = 0
        self._lock = threading.Lock()

    def format_line(self, success, fail=0, force=False):
        now = time.time()
        with self._lock:
            elapsed = now - self.last_tick
            if not force and elapsed < self.interval_sec:
                return None
            success = int(success or 0)
            fail = int(fail or 0)
            delta = max(success - self.last_success, 0)
            # 正常按实际窗口折算；极短窗口（force 收尾/刚启动）用 interval 估，避免天文数字
            if elapsed >= 1.0:
                window = elapsed
            else:
                window = self.interval_sec
            rate = delta * 60.0 / window
            total_sec = max(now - self.t0, 0.0)
            total_min = total_sec / 60.0
            # 运行不足 1s 时平均速度与窗口速率对齐，避免 540/min 这类瞬时噪声
            if total_sec >= 1.0:
                avg = success * 60.0 / total_sec
            else:
                avg = rate
            self.last_tick = now
            self.last_success = success
            return (
                f"[*] 速度统计: 成功 {rate:.0f}/min | 本分钟成功 {delta} "
                f"| 累计成功 {success} | 累计失败 {fail} | 运行 {total_min:.1f}min | 平均 {avg:.1f}/min"
            )

    def maybe_log(self, log_callback, success, fail=0, force=False):
        line = self.format_line(success, fail=fail, force=force)
        if line:
            emit_log(log_callback, line, level="quiet")


def start_speed_logger(get_counts, log_callback, stop_event, interval_sec=60):
    """后台每 interval 打印一次全局速度；stop 后打印最终摘要。"""

    meter = RateMeter(interval_sec=interval_sec)

    def _loop():
        while True:
            if stop_event.wait(timeout=meter.interval_sec):
                break
            try:
                success, fail = get_counts()
            except Exception:
                success, fail = 0, 0
            meter.maybe_log(log_callback, success, fail, force=True)
        try:
            success, fail = get_counts()
        except Exception:
            success, fail = 0, 0
        meter.maybe_log(log_callback, success, fail, force=True)

    thread = threading.Thread(target=_loop, name="speed-logger", daemon=True)
    thread.start()
    return thread, meter


def load_config():
    global config
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            config = {**DEFAULT_CONFIG, **loaded}
        except Exception:
            config = DEFAULT_CONFIG.copy()
    return config


def save_config():
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
        os.chmod(CONFIG_FILE, 0o600)
    except Exception as e:
        print(f"保存配置失败: {e}")


def ensure_stable_python_runtime():
    if sys.version_info < (3, 14) or os.environ.get("DPE_REEXEC_DONE") == "1":
        return

    local_app_data = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        os.path.join(local_app_data, "Programs", "Python", "Python312", "python.exe"),
        os.path.join(local_app_data, "Programs", "Python", "Python313", "python.exe"),
    ]

    current_python = os.path.normcase(os.path.abspath(sys.executable))
    for candidate in candidates:
        if not os.path.isfile(candidate):
            continue
        if os.path.normcase(os.path.abspath(candidate)) == current_python:
            return

        print(
            f"[*] 检测到 Python {sys.version.split()[0]}，自动切换到更稳定的解释器: {candidate}"
        )
        env = os.environ.copy()
        env["DPE_REEXEC_DONE"] = "1"
        os.execve(candidate, [candidate, os.path.abspath(__file__), *sys.argv[1:]], env)


def warn_runtime_compatibility():
    if sys.version_info >= (3, 14):
        print(
            "[提示] 当前 Python 为 3.14+；若出现 Mail.tm TLS 异常，建议改用 Python 3.12 或 3.13。"
        )


ensure_stable_python_runtime()
warn_runtime_compatibility()

load_config()

EXTENSION_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "turnstilePatch")
)


DUCKMAIL_API_BASE = "https://api.duckmail.sbs"


def get_proxies():
    proxy = config.get("proxy", "")
    if proxy:
        return {"http": proxy, "https": proxy}
    return {}


def get_duckmail_api_key():
    return config.get("duckmail_api_key", "")


def get_cloudflare_api_base():
    return str(config.get("cloudflare_api_base", "") or "").rstrip("/")


def get_cloudflare_api_key():
    return config.get("cloudflare_api_key", "")


def get_cloudflare_auth_mode():
    return str(config.get("cloudflare_auth_mode", "none") or "none").lower()


def get_cloudflare_path(key, default_path):
    raw = str(config.get(key, default_path) or default_path).strip()
    if not raw.startswith("/"):
        raw = "/" + raw
    return raw


def cloudflare_build_headers(content_type=False):
    headers = {"Content-Type": "application/json"} if content_type else {}
    key = get_cloudflare_api_key()
    mode = get_cloudflare_auth_mode()
    if key:
        if mode == "x-api-key":
            headers["X-API-Key"] = key
        elif mode == "x-admin-auth":
            headers["x-admin-auth"] = key
        elif mode != "none":
            headers["Authorization"] = f"Bearer {key}"
    return headers


def cloudflare_apply_auth_params(params=None):
    merged = dict(params or {})
    key = get_cloudflare_api_key()
    mode = get_cloudflare_auth_mode()
    if key and mode == "query-key":
        merged["key"] = key
    return merged


def cloudflare_next_default_domain():
    """按配置轮换选择 Cloudflare 临时邮箱域名。"""
    global _cf_domain_index
    domains = [x.strip() for x in str(config.get("defaultDomains", "") or "").split(",") if x.strip()]
    if not domains:
        return ""
    with _cf_domain_lock:
        domain = domains[_cf_domain_index % len(domains)]
        _cf_domain_index += 1
        return domain


def cloudflare_is_admin_create_path(path):
    """判断当前创建邮箱路径是否为 cloudflare_temp_email 管理员创建接口。"""
    return str(path or "").rstrip("/").lower() == "/admin/new_address"


def _pick_list_payload(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if isinstance(data.get("results"), list):
            return data.get("results")
        if isinstance(data.get("hydra:member"), list):
            return data.get("hydra:member")
        if isinstance(data.get("data"), list):
            return data.get("data")
        if isinstance(data.get("messages"), list):
            return data.get("messages")
        if isinstance(data.get("data"), dict):
            nested = data.get("data")
            if isinstance(nested.get("messages"), list):
                return nested.get("messages")
    return []


def cloudflare_create_temp_address(api_base):
    """适配 cloudflare_temp_email 新建地址接口并兼容 admin 创建模式。"""
    path = get_cloudflare_path("cloudflare_path_accounts", "/api/new_address")
    url = f"{api_base}{path}"
    domain = cloudflare_next_default_domain()
    is_admin_create = cloudflare_is_admin_create_path(path)
    if is_admin_create:
        payload = {"name": generate_username(10), "enablePrefix": True}
        if domain:
            payload["domain"] = domain
        headers = cloudflare_build_headers(content_type=True)
    else:
        payload = {}
        if domain:
            payload["domain"] = domain
        headers = {"Content-Type": "application/json"}
    resp = http_post(url, json=payload, headers=headers)
    resp.raise_for_status()
    try:
        data = resp.json()
    except Exception:
        raise Exception(f"Cloudflare {path} 返回非JSON: {resp.text[:300]}")
    address = data.get("address")
    jwt = data.get("jwt")
    if not address or not jwt:
        raise Exception(f"Cloudflare {path} 缺少 address/jwt: {data}")
    return address, jwt


def get_user_agent():
    return config.get(
        "user_agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
    )


def resolve_grok2api_local_token_file():
    configured = str(config.get("grok2api_local_token_file", "") or "").strip()
    if configured:
        return configured
    return os.path.join(os.path.dirname(__file__), "token.json")


def _normalize_sso_token(raw_token):
    token = str(raw_token or "").strip()
    if token.startswith("sso="):
        token = token[4:]
    return token


def add_token_to_grok2api_local_pool(raw_token, email="", log_callback=None):
    token = _normalize_sso_token(raw_token)
    if not token:
        return False
    token_file = resolve_grok2api_local_token_file()
    pool_name = str(config.get("grok2api_pool_name", "ssoBasic") or "ssoBasic").strip()
    if not pool_name:
        pool_name = "ssoBasic"
    parent_dir = os.path.dirname(token_file)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)
    with _io_lock:
        data = {}
        if os.path.exists(token_file):
            try:
                with open(token_file, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
            except Exception:
                data = {}
        if not isinstance(data, dict):
            data = {}
        pool = data.get(pool_name)
        if not isinstance(pool, list):
            pool = []
        existing = set()
        for item in pool:
            if isinstance(item, str):
                existing.add(_normalize_sso_token(item))
            elif isinstance(item, dict):
                existing.add(_normalize_sso_token(item.get("token", "")))
        if token in existing:
            if log_callback:
                log_callback(f"[*] grok2api 本地池已存在 token: {pool_name}")
            return True
        entry = {"token": token, "tags": ["auto-register"], "note": email}
        pool.append(entry)
        data[pool_name] = pool
        with open(token_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    if log_callback:
        log_callback(f"[+] 已写入 grok2api 本地池: {pool_name} ({token_file})")
    return True


def get_grok2api_remote_api_bases(base):
    """生成 grok2api 管理 API 候选根路径。

    参数:
      - base str: 用户配置的 grok2api 远端地址

    返回:
      - list[str]: 依次尝试的管理 API 根路径
    """
    normalized = str(base or "").strip().rstrip("/")
    if not normalized:
        return []
    lower = normalized.lower()
    candidates = [normalized]
    if lower.endswith("/admin/api"):
        return candidates
    if lower.endswith("/admin"):
        candidates.append(f"{normalized}/api")
    else:
        candidates.append(f"{normalized}/admin/api")
    seen = set()
    unique = []
    for item in candidates:
        if item not in seen:
            unique.append(item)
            seen.add(item)
    return unique


def add_token_to_grok2api_remote_pool(raw_token, email="", log_callback=None):
    token = _normalize_sso_token(raw_token)
    if not token:
        return False
    base = str(config.get("grok2api_remote_base", "") or "").strip().rstrip("/")
    app_key = str(config.get("grok2api_remote_app_key", "") or "").strip()
    pool_name = str(config.get("grok2api_pool_name", "ssoBasic") or "ssoBasic").strip() or "ssoBasic"
    if not base or not app_key:
        if log_callback:
            log_callback("[Debug] grok2api 远端未配置 base/app_key，跳过")
        return False
    headers = {"Content-Type": "application/json"}
    query = {"app_key": app_key}
    pool_map = {"ssoBasic": "basic", "ssoSuper": "super"}
    remote_pool = pool_map.get(pool_name, "basic")
    api_bases = get_grok2api_remote_api_bases(base)
    add_errors = []
    # 优先使用 add 接口，避免全量覆盖远端池
    add_payload = {"tokens": [token], "pool": remote_pool, "tags": ["auto-register"]}
    for api_base in api_bases:
        endpoint = f"{api_base}/tokens/add"
        try:
            resp_add = http_post(
                endpoint,
                headers=headers,
                params=query,
                json=add_payload,
                timeout=30,
                proxies={},
            )
            resp_add.raise_for_status()
            if log_callback:
                log_callback(f"[+] 已写入 grok2api 远端池: {pool_name} ({endpoint})")
            return True
        except Exception as add_exc:
            add_errors.append(f"{endpoint}: {add_exc}")
    if log_callback:
        log_callback(f"[Debug] /tokens/add 写入失败，尝试 /tokens 全量模式: {'; '.join(add_errors)}")

    # 兜底：旧版全量保存接口
    current = {}
    fallback_base = api_bases[0] if api_bases else base
    for api_base in api_bases or [base]:
        try:
            resp = http_get(f"{api_base}/tokens", headers=headers, params=query, timeout=20, proxies={})
            if resp.status_code == 200:
                payload = resp.json()
                current = payload.get("tokens", {}) if isinstance(payload, dict) else {}
                fallback_base = api_base
                break
        except Exception:
            continue
    if not isinstance(current, dict):
        current = {}
    pool = current.get(pool_name)
    if not isinstance(pool, list):
        pool = []
    existing = set()
    for item in pool:
        if isinstance(item, str):
            existing.add(_normalize_sso_token(item))
        elif isinstance(item, dict):
            existing.add(_normalize_sso_token(item.get("token", "")))
    if token not in existing:
        pool.append({"token": token, "tags": ["auto-register"], "note": email})
    current[pool_name] = pool
    save_errors = []
    save_bases = []
    for item in [fallback_base, *(api_bases or [base])]:
        if item and item not in save_bases:
            save_bases.append(item)
    for api_base in save_bases:
        try:
            resp2 = http_post(f"{api_base}/tokens", headers=headers, params=query, json=current, timeout=30, proxies={})
            resp2.raise_for_status()
            if log_callback:
                log_callback(f"[+] 已写入 grok2api 远端池: {pool_name} ({api_base}/tokens)")
            return True
        except Exception as save_exc:
            save_errors.append(f"{api_base}/tokens: {save_exc}")
    raise RuntimeError(f"grok2api 远端 /tokens 全量模式写入失败: {'; '.join(save_errors)}")


def add_token_to_grok2api_pools(raw_token, email="", log_callback=None):
    if config.get("grok2api_auto_add_local", True):
        try:
            add_token_to_grok2api_local_pool(raw_token, email=email, log_callback=log_callback)
        except Exception as exc:
            if log_callback:
                log_callback(f"[Debug] 写入 grok2api 本地池失败: {exc}")
    if config.get("grok2api_auto_add_remote", False):
        try:
            add_token_to_grok2api_remote_pool(raw_token, email=email, log_callback=log_callback)
        except Exception as exc:
            if log_callback:
                log_callback(f"[Debug] 写入 grok2api 远端池失败: {exc}")


def add_token_to_token_only_file(raw_token, log_callback=None):
    token = _normalize_sso_token(raw_token)
    if not token:
        return False
    token_only_file = str(config.get("token_only_file", "") or "").strip()
    if not token_only_file:
        token_only_file = os.path.join(os.path.dirname(__file__), "tokens.txt")
    try:
        with _io_lock:
            with open(token_only_file, "a", encoding="utf-8") as f:
                f.write(f"{token}\n")
        if log_callback:
            log_callback(f"[+] 已写入 token 文件: {token_only_file}")
        return True
    except Exception as exc:
        if log_callback:
            log_callback(f"[Debug] 写入 token 文件失败: {exc}")
        return False


def upload_to_cpa_server(local_path, log_callback=None):
    host = str(config.get("cpa_server_host", "") or "").strip()
    user = str(config.get("cpa_server_user", "root") or "root").strip()
    password = str(config.get("cpa_server_password", "") or "").strip()
    remote_dir = str(config.get("cpa_server_auth_dir", "") or "").strip()
    if not host or not remote_dir:
        return False
    try:
        import paramiko
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(host, username=user, password=password, timeout=15)
        sftp = ssh.open_sftp()
        filename = os.path.basename(local_path)
        remote_path = remote_dir.rstrip("/") + "/" + filename
        sftp.put(local_path, remote_path)
        try:
            sftp.chmod(remote_path, 0o600)
        except Exception:
            pass
        sftp.close()
        ssh.close()
        if log_callback:
            log_callback(f"[cpa] 已上传到服务器: {host}:{remote_path}")
        return True
    except Exception as exc:
        if log_callback:
            log_callback(f"[cpa] 上传到服务器失败: {exc}")
        return False


def export_cpa_xai_for_account(email, password, sso=None, log_callback=None, page=None):
    if not config.get("cpa_export_enabled", True):
        if log_callback:
            log_callback("[cpa] CPA 导出已禁用，跳过")
        return {"ok": False, "skipped": True, "reason": "disabled"}
    try:
        from cpa_export import export_cpa_xai_for_account as _export
        return _export(
            email, password,
            sso=sso,
            page=page,
            config=config,
            log_callback=log_callback,
        )
    except Exception as exc:
        if log_callback:
            log_callback(f"[cpa] CPA xAI 导出失败: {exc}")
        return {"ok": False, "error": str(exc)}


def create_browser_options():
    """创建尽量贴近真实浏览器的启动参数。

    TUN 系统代理时请保持 config.proxy 为空，让 Chromium 走系统网络栈。
    不要默认 new_env / 强制 UA / 过多 flag，容易触发 Cloudflare「故障排除」。
    """
    options = ChromiumOptions()
    options.set_timeouts(base=1)
    # 并发时为每个 worker 分配独立资料目录，避免 cookie/会话互相污染
    profile_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".browser_profiles")
    try:
        os.makedirs(profile_root, exist_ok=True)
        wid = _get_worker_id()
        profile_dir = os.path.join(
            profile_root,
            f"w{wid}_{os.getpid()}_{threading.get_ident()}_{int(time.time() * 1000) % 1000000}",
        )
        options.set_user_data_path(profile_dir)
    except Exception:
        pass
    # set_user_data_path 可能清掉 auto_port，必须放在后面重新启用
    options.auto_port()
    for flag in (
        "--no-first-run",
        "--no-default-browser-check",
    ):
        options.set_argument(flag)
    # 仅显式配置 proxy 时写入；TUN 模式保持空
    proxy = str(config.get("proxy", "") or "").strip()
    if proxy:
        try:
            options.set_proxy(proxy)
        except Exception:
            options.set_argument(f"--proxy-server={proxy}")
    # 默认使用浏览器真实 UA；仅当用户显式打开时才覆盖
    if config.get("browser_use_custom_ua", False):
        ua = get_user_agent()
        if ua:
            try:
                options.set_user_agent(ua)
            except Exception:
                options.set_argument(f"--user-agent={ua}")
    if os.path.exists(EXTENSION_PATH):
        options.add_extension(EXTENSION_PATH)
    return options


def _cf_timeout_sec(default=45):
    try:
        return max(8.0, float(config.get("cf_turnstile_timeout_sec", default) or default))
    except Exception:
        return float(default)


def snapshot_cf_cookies(log_callback=None):
    if not config.get("keep_cf_cookies", True):
        return list(getattr(_tls, "cf_cookies", None) or [])
    cookies = cf_turnstile.collect_cf_cookies(_get_page(), _get_browser())
    if cookies:
        _tls.cf_cookies = cookies
        if log_callback:
            names = ", ".join(sorted({c.get("name", "") for c in cookies if c.get("name")}))
            log_callback(f"[Debug] 已缓存 Cloudflare cookie: {names or len(cookies)}")
        return cookies
    return list(getattr(_tls, "cf_cookies", None) or [])


def restore_cf_cookies(log_callback=None):
    if not config.get("keep_cf_cookies", True):
        return 0
    cookies = list(getattr(_tls, "cf_cookies", None) or [])
    if not cookies:
        return 0
    return cf_turnstile.inject_cookies(_get_page(), cookies, log=log_callback)


def _should_use_os_click(log_callback=None):
    """系统鼠标全局唯一；多开时禁用，避免点到别的浏览器窗口。"""
    if not bool(config.get("cf_os_click", True)):
        return False
    try:
        concurrent = max(1, int(config.get("concurrent_count", 1) or 1))
    except Exception:
        concurrent = 1
    if concurrent > 1:
        # 每个 worker 只提示一次，避免刷屏
        if not getattr(_tls, "_os_click_disabled_logged", False):
            _tls._os_click_disabled_logged = True
            if log_callback:
                log_callback(
                    f"[*] concurrent_count={concurrent}，已禁用系统鼠标点击，改用各浏览器独立 CDP 点击"
                )
        return False
    return True


def try_solve_turnstile(log_callback=None, cancel_callback=None, timeout=None, require=False):
    raise_if_cancelled(cancel_callback)
    page = _get_page()
    if page is None:
        return ""
    try:
        token = cf_turnstile.solve_turnstile(
            page,
            timeout=_cf_timeout_sec() if timeout is None else timeout,
            log=log_callback,
            cancel_callback=cancel_callback,
            auto_click=bool(config.get("cf_auto_click", True)),
            os_click=_should_use_os_click(log_callback=log_callback),
            require=require,
        )
    except cf_turnstile.TurnstileStopped:
        raise RegistrationCancelled()
    if token:
        snapshot_cf_cookies(log_callback=log_callback)
    return token


def _build_request_kwargs(**kwargs):
    request_kwargs = dict(kwargs)
    proxies = request_kwargs.pop("proxies", None)
    if proxies is None:
        proxies = get_proxies()
    if proxies:
        request_kwargs["proxies"] = proxies
    request_kwargs.setdefault("timeout", 15)
    return request_kwargs


def http_get(url, **kwargs):
    try:
        return requests.get(url, **_build_request_kwargs(**kwargs))
    except Exception as exc:
        err = str(exc)
        # 代理不可用时自动回退为直连，避免整个流程直接失败
        if "127.0.0.1 port 7890" in err or "Could not connect to server" in err:
            retry_kwargs = dict(kwargs)
            retry_kwargs["proxies"] = {}
            return requests.get(url, **_build_request_kwargs(**retry_kwargs))
        raise


def http_post(url, **kwargs):
    try:
        return requests.post(url, **_build_request_kwargs(**kwargs))
    except Exception as exc:
        err = str(exc)
        if "127.0.0.1 port 7890" in err or "Could not connect to server" in err:
            retry_kwargs = dict(kwargs)
            retry_kwargs["proxies"] = {}
            return requests.post(url, **_build_request_kwargs(**retry_kwargs))
        raise


def raise_if_cancelled(cancel_callback=None):
    if cancel_callback and cancel_callback():
        raise RegistrationCancelled("用户停止注册")


def sleep_with_cancel(seconds, cancel_callback=None):
    deadline = time.time() + max(seconds, 0)
    while True:
        raise_if_cancelled(cancel_callback)
        remaining = deadline - time.time()
        if remaining <= 0:
            return
        time.sleep(min(0.2, remaining))


def get_domains(api_key=None):
    headers = {}
    key = api_key or get_duckmail_api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    resp = http_get(f"{DUCKMAIL_API_BASE}/domains", headers=headers)
    resp.raise_for_status()
    return resp.json().get("hydra:member", [])


def create_account(address, password, api_key=None, expires_in=0):
    headers = {"Content-Type": "application/json"}
    key = api_key or get_duckmail_api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    data = {"address": address, "password": password, "expiresIn": expires_in}
    resp = http_post(f"{DUCKMAIL_API_BASE}/accounts", json=data, headers=headers)
    resp.raise_for_status()
    return resp.json()


def get_token(address, password):
    data = {"address": address, "password": password}
    resp = http_post(f"{DUCKMAIL_API_BASE}/token", json=data)
    resp.raise_for_status()
    return resp.json().get("token")


def get_messages(token):
    headers = {"Authorization": f"Bearer {token}"}
    resp = http_get(f"{DUCKMAIL_API_BASE}/messages", headers=headers)
    resp.raise_for_status()
    return resp.json().get("hydra:member", [])


def get_message_detail(token, message_id):
    headers = {"Authorization": f"Bearer {token}"}
    resp = http_get(f"{DUCKMAIL_API_BASE}/messages/{message_id}", headers=headers)
    resp.raise_for_status()
    return resp.json()


def cloudflare_get_domains(api_base, api_key=None):
    headers = cloudflare_build_headers(content_type=False)
    if api_key and "Authorization" in headers:
        headers["Authorization"] = f"Bearer {api_key}"
    if api_key and "X-API-Key" in headers:
        headers["X-API-Key"] = api_key
    path = get_cloudflare_path("cloudflare_path_domains", "/domains")
    params = cloudflare_apply_auth_params()
    resp = http_get(f"{api_base}{path}", headers=headers, params=params)
    resp.raise_for_status()
    return _pick_list_payload(resp.json())


def cloudflare_create_account(api_base, address, password, api_key=None, expires_in=0):
    headers = cloudflare_build_headers(content_type=True)
    if api_key and "Authorization" in headers:
        headers["Authorization"] = f"Bearer {api_key}"
    if api_key and "X-API-Key" in headers:
        headers["X-API-Key"] = api_key
    payload = {"address": address, "password": password, "expiresIn": expires_in}
    path = get_cloudflare_path("cloudflare_path_accounts", "/accounts")
    params = cloudflare_apply_auth_params()
    resp = http_post(f"{api_base}{path}", json=payload, headers=headers, params=params)
    resp.raise_for_status()
    return resp.json()


def cloudflare_get_token(api_base, address, password, api_key=None):
    headers = cloudflare_build_headers(content_type=True)
    if api_key and "Authorization" in headers:
        headers["Authorization"] = f"Bearer {api_key}"
    if api_key and "X-API-Key" in headers:
        headers["X-API-Key"] = api_key
    path = get_cloudflare_path("cloudflare_path_token", "/token")
    resp = http_post(
        f"{api_base}{path}",
        json={"address": address, "password": password},
        headers=headers,
        params=cloudflare_apply_auth_params(),
    )
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict):
        if data.get("token"):
            return data.get("token")
        if isinstance(data.get("data"), dict) and data["data"].get("token"):
            return data["data"].get("token")
    return None


def cloudflare_get_messages(api_base, token):
    headers = {"Authorization": f"Bearer {token}"}
    path = get_cloudflare_path("cloudflare_path_messages", "/messages")
    params = {"limit": 20, "offset": 0}
    params = cloudflare_apply_auth_params(params)
    resp = http_get(f"{api_base}{path}", headers=headers, params=params)
    resp.raise_for_status()
    try:
        data = resp.json()
    except Exception:
        raise Exception(f"Cloudflare messages 返回非JSON: {resp.text[:300]}")
    return _pick_list_payload(data)


def cloudflare_get_message_detail(api_base, token, message_id):
    headers = {"Authorization": f"Bearer {token}"}
    candidates = [
        f"{api_base}/api/mail/{message_id}",
        f"{api_base}{get_cloudflare_path('cloudflare_path_messages', '/messages')}/{message_id}",
    ]
    last_err = None
    for url in candidates:
        try:
            resp = http_get(
                url,
                headers=headers,
                params=cloudflare_apply_auth_params(),
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict) and isinstance(data.get("data"), dict):
                return data["data"]
            return data
        except Exception as exc:
            last_err = exc
            continue
    raise Exception(f"Cloudflare 获取邮件详情失败: {last_err}")


YYDS_API_BASE = "https://maliapi.215.im/v1"


def get_yyds_api_key():
    return config.get("yyds_api_key", "")


def get_yyds_jwt():
    return config.get("yyds_jwt", "")


def yyds_get_domains(api_key=None, jwt=None):
    key = api_key or get_yyds_api_key()
    token = jwt or get_yyds_jwt()
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif key:
        headers["X-API-Key"] = key
    resp = http_get(f"{YYDS_API_BASE}/domains", headers=headers)
    resp.raise_for_status()
    data = resp.json()
    return data.get("data", []) if data.get("success") else []


def yyds_create_account(address=None, domain=None, api_key=None, jwt=None):
    key = api_key or get_yyds_api_key()
    token = jwt or get_yyds_jwt()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif key:
        headers["X-API-Key"] = key
    payload = {}
    if address:
        payload["address"] = address
    if domain:
        payload["domain"] = domain
    elif key or token:
        payload["autoDomainStrategy"] = "prefer_owned"
    resp = http_post(f"{YYDS_API_BASE}/accounts", json=payload, headers=headers)
    resp.raise_for_status()
    data = resp.json()
    if data.get("success"):
        return data.get("data", {})
    raise Exception(f"YYDS 鍒涘缓閭澶辫触: {data}")


def yyds_get_token(address, api_key=None, jwt=None):
    key = api_key or get_yyds_api_key()
    token = jwt or get_yyds_jwt()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif key:
        headers["X-API-Key"] = key
    resp = http_post(
        f"{YYDS_API_BASE}/token", json={"address": address}, headers=headers
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("success"):
        return data.get("data", {}).get("token")
    raise Exception(f"YYDS 鑾峰彇token澶辫触: {data}")


def yyds_get_messages(address, token=None, api_key=None, jwt=None):
    key = api_key or get_yyds_api_key()
    temp_token = token or jwt or get_yyds_jwt()
    headers = {}
    if temp_token:
        headers["Authorization"] = f"Bearer {temp_token}"
    elif key:
        headers["X-API-Key"] = key
    resp = http_get(
        f"{YYDS_API_BASE}/messages",
        params={"address": address},
        headers=headers,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("success"):
        return data.get("data", {}).get("messages", [])
    return []


def yyds_get_message_detail(message_id, token=None, api_key=None, jwt=None):
    key = api_key or get_yyds_api_key()
    temp_token = token or jwt or get_yyds_jwt()
    headers = {}
    if temp_token:
        headers["Authorization"] = f"Bearer {temp_token}"
    elif key:
        headers["X-API-Key"] = key
    resp = http_get(f"{YYDS_API_BASE}/messages/{message_id}", headers=headers)
    resp.raise_for_status()
    data = resp.json()
    if data.get("success"):
        return data.get("data", {})
    raise Exception(f"YYDS 鑾峰彇閭欢璇︽儏澶辫触: {data}")


def yyds_generate_username(length=10):
    chars = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(length))


def yyds_pick_domain(api_key=None, jwt=None):
    domains = yyds_get_domains(api_key=api_key, jwt=jwt)
    if not domains:
        raise Exception("YYDS 娌℃湁杩斿洖浠讳綍鍙敤鍩熷悕")
    private = [d for d in domains if d.get("isVerified") and not d.get("isPublic")]
    if private:
        return private[0]["domain"]
    public = [d for d in domains if d.get("isVerified") and d.get("isPublic")]
    if public:
        return public[0]["domain"]
    verified = [d for d in domains if d.get("isVerified")]
    if verified:
        return verified[0]["domain"]
    raise Exception("YYDS 鏃犲凡楠岃瘉鍩熷悕鍙敤")


def yyds_get_email_and_token(api_key=None, jwt=None):
    key = api_key or get_yyds_api_key()
    token = jwt or get_yyds_jwt()
    if not token and not key:
        raise Exception("YYDS API Key 或 JWT 未配置")
    domain = yyds_pick_domain(api_key=key, jwt=token)
    username = yyds_generate_username(10)
    result = yyds_create_account(
        address=username, domain=domain, api_key=key, jwt=token
    )
    address = result.get("address") or f"{username}@{domain}"
    temp_token = result.get("token")
    if not temp_token:
        temp_token = yyds_get_token(address, api_key=key, jwt=token)
    if not temp_token:
        raise Exception("鑾峰彇 YYDS token 澶辫触")
    print(f"[*] 宸插垱寤?YYDS 閭: {address}")
    return address, temp_token


def yyds_get_oai_code(
    token,
    address,
    timeout=180,
    poll_interval=3,
    log_callback=None,
    jwt=None,
    cancel_callback=None,
):
    deadline = time.time() + timeout
    seen_ids = set()
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        try:
            messages = yyds_get_messages(address, token=token, jwt=jwt)
        except Exception as exc:
            if log_callback:
                log_callback(f"[Debug] YYDS 鎷夊彇閭欢鍒楄〃澶辫触: {exc}")
            sleep_with_cancel(poll_interval, cancel_callback)
            continue
        for msg in messages:
            msg_id = msg.get("id")
            if not msg_id or msg_id in seen_ids:
                continue
            seen_ids.add(msg_id)
            to_addrs = [t.get("address", "").lower() for t in (msg.get("to") or [])]
            if address.lower() not in to_addrs:
                continue
            try:
                detail = yyds_get_message_detail(msg_id, token=token, jwt=jwt)
            except Exception as exc:
                if log_callback:
                    log_callback(f"[Debug] YYDS 鑾峰彇閭欢璇︽儏澶辫触: {exc}")
                continue
            parts = []
            text_body = detail.get("text") or ""
            if text_body:
                parts.append(text_body)
            html_list = detail.get("html") or []
            for h in html_list:
                parts.append(re.sub(r"<[^>]+>", " ", h))
            combined = "\n".join(parts)
            subject = detail.get("subject", "")
            if log_callback:
                log_callback(f"[Debug] YYDS 鏀跺埌閭欢: {subject}")
            code = extract_verification_code(combined, subject)
            if code:
                if log_callback:
                    log_callback(f"[*] YYDS 浠庨偖浠朵腑鎻愬彇鍒伴獙璇佺爜: {code}")
                return code
        sleep_with_cancel(poll_interval, cancel_callback)
    raise Exception(f"YYDS 在 {timeout}s 内未收到验证码邮件")


def generate_username(length=10):
    chars = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(length))


def pick_domain(api_key=None):
    domains = get_domains(api_key=api_key)
    if not domains:
        raise Exception("DuckMail 娌℃湁杩斿洖浠讳綍鍙敤鍩熷悕")
    private = [d for d in domains if d.get("ownerId")]
    verified_private = [d for d in private if d.get("isVerified")]
    if verified_private:
        return verified_private[0]["domain"]
    public = [d for d in domains if d.get("isVerified")]
    if public:
        return public[0]["domain"]
    raise Exception("DuckMail 鏃犲凡楠岃瘉鍩熷悕鍙敤")


def get_email_provider():
    return config.get("email_provider", "duckmail")


def get_email_and_token(api_key=None):
    provider = get_email_provider()
    if provider == "yyds":
        return yyds_get_email_and_token(api_key=api_key, jwt=get_yyds_jwt())
    if provider == "cloudflare":
        api_base = get_cloudflare_api_base()
        if not api_base:
            raise Exception("Cloudflare API Base 未配置")
        try:
            # cloudflare_temp_email 专用模式
            return cloudflare_create_temp_address(api_base)
        except Exception as primary_exc:
            # 兜底回退到 Mail.tm 风格
            key = api_key or get_cloudflare_api_key()
            domains = cloudflare_get_domains(api_base, api_key=key)
            if not domains:
                raise Exception(f"Cloudflare 创建邮箱失败: {primary_exc}")
            verified = [d for d in domains if d.get("isVerified")]
            target = verified[0] if verified else domains[0]
            domain = target.get("domain")
            if not domain:
                raise Exception("Cloudflare 域名数据格式错误，缺少 domain 字段")
            username = generate_username(10)
            address = f"{username}@{domain}"
            password = secrets.token_urlsafe(12)
            cloudflare_create_account(
                api_base, address, password, api_key=key, expires_in=0
            )
            token = cloudflare_get_token(api_base, address, password, api_key=key)
            if not token:
                raise Exception("获取 Cloudflare 邮箱 token 失败")
            return address, token
    key = api_key or get_duckmail_api_key()
    domain = pick_domain(api_key=key)
    username = generate_username(10)
    address = f"{username}@{domain}"
    password = secrets.token_urlsafe(12)
    create_account(address, password, api_key=key, expires_in=0)
    token = get_token(address, password)
    if not token:
        raise Exception("鑾峰彇 DuckMail token 澶辫触")
    return address, token


def get_oai_code(
    dev_token,
    email,
    timeout=180,
    poll_interval=3,
    log_callback=None,
    cancel_callback=None,
    resend_callback=None,
):
    provider = get_email_provider()
    if provider == "yyds":
        return yyds_get_oai_code(
            dev_token,
            email,
            timeout=timeout,
            poll_interval=poll_interval,
            log_callback=log_callback,
            jwt=get_yyds_jwt(),
            cancel_callback=cancel_callback,
        )
    if provider == "cloudflare":
        return cloudflare_get_oai_code(
            dev_token,
            email,
            timeout=timeout,
            poll_interval=poll_interval,
            log_callback=log_callback,
            cancel_callback=cancel_callback,
            resend_callback=resend_callback,
        )
    return duckmail_get_oai_code(
        dev_token,
        email,
        timeout=timeout,
        poll_interval=poll_interval,
        log_callback=log_callback,
        cancel_callback=cancel_callback,
    )


def extract_verification_code(text, subject=""):
    if subject:
        match = re.search(r"^([A-Z0-9]{3}-[A-Z0-9]{3})\s+xAI", subject, re.IGNORECASE)
        if match:
            return match.group(1)
    match = re.search(r"\b([A-Z0-9]{3}-[A-Z0-9]{3})\b", text, re.IGNORECASE)
    if match:
        return match.group(1)
    patterns = [
        r"verification\s+code[:\s]+(\d{4,8})",
        r"your\s+code[:\s]+(\d{4,8})",
        r"confirm(?:ation)?\s+code[:\s]+(\d{4,8})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def duckmail_get_oai_code(
    dev_token,
    email,
    timeout=180,
    poll_interval=3,
    log_callback=None,
    cancel_callback=None,
):
    deadline = time.time() + timeout
    seen_ids = set()
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        try:
            messages = get_messages(dev_token)
        except Exception as exc:
            if log_callback:
                log_callback(f"[Debug] 鎷夊彇閭欢鍒楄〃澶辫触: {exc}")
            sleep_with_cancel(poll_interval, cancel_callback)
            continue
        for msg in messages:
            msg_id = msg.get("id") or msg.get("msgid")
            if not msg_id or msg_id in seen_ids:
                continue
            seen_ids.add(msg_id)
            recipients = [t.get("address", "").lower() for t in (msg.get("to") or [])]
            if email.lower() not in recipients:
                continue
            try:
                detail = get_message_detail(dev_token, msg_id)
            except Exception as exc:
                if log_callback:
                    log_callback(f"[Debug] 鑾峰彇閭欢璇︽儏澶辫触: {exc}")
                continue
            parts = []
            text_body = detail.get("text") or ""
            if text_body:
                parts.append(text_body)
            html_list = detail.get("html") or []
            for h in html_list:
                parts.append(re.sub(r"<[^>]+>", " ", h))
            combined = "\n".join(parts)
            subject = detail.get("subject", "")
            if log_callback:
                log_callback(f"[Debug] 鏀跺埌閭欢: {subject}")
            code = extract_verification_code(combined, subject)
            if code:
                if log_callback:
                    log_callback(f"[*] 浠庨偖浠朵腑鎻愬彇鍒伴獙璇佺爜: {code}")
                return code
        sleep_with_cancel(poll_interval, cancel_callback)
    raise Exception(f"在 {timeout}s 内未收到验证码邮件")


def cloudflare_get_oai_code(
    dev_token,
    email,
    timeout=180,
    poll_interval=3,
    log_callback=None,
    cancel_callback=None,
    resend_callback=None,
):
    api_base = get_cloudflare_api_base()
    if not api_base:
        raise Exception("Cloudflare API Base 未配置")
    deadline = time.time() + timeout
    # 同一封邮件正文可能延迟可读，允许多次重试解析，避免偶发漏码
    seen_attempts = {}
    next_resend_at = time.time() + 35
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        if resend_callback and time.time() >= next_resend_at:
            try:
                resend_callback()
                if log_callback:
                    log_callback("[*] 已触发重新发送验证码")
            except Exception as exc:
                if log_callback:
                    log_callback(f"[Debug] 触发重发验证码失败: {exc}")
            next_resend_at = time.time() + 35
        try:
            messages = cloudflare_get_messages(api_base, dev_token)
        except Exception as exc:
            if log_callback:
                log_callback(f"[Debug] Cloudflare 拉取邮件列表失败: {exc}")
            sleep_with_cancel(poll_interval, cancel_callback)
            continue
        if log_callback:
            log_callback(f"[Debug] Cloudflare 本轮邮件数量: {len(messages)}")

        for msg in messages:
            msg_id = msg.get("id") or msg.get("msgid")
            if not msg_id:
                continue
            attempt = int(seen_attempts.get(msg_id, 0))
            if attempt >= 5:
                continue
            seen_attempts[msg_id] = attempt + 1
            recipients = [t.get("address", "").lower() for t in (msg.get("to") or [])]
            msg_addr = str(msg.get("address", "")).lower()
            # 优先匹配目标邮箱；若结构不一致也允许继续解析，避免接口字段漂移导致漏码
            address_matched = True
            if recipients:
                address_matched = email.lower() in recipients
            elif msg_addr:
                address_matched = msg_addr == email.lower()
            if not address_matched and log_callback:
                log_callback(f"[Debug] 跳过疑似非目标邮件 id={msg_id} address={msg_addr} to={recipients}")
                continue
            parts = []
            # 先直接从列表项取内容，避免 detail 接口差异导致漏码
            for field in ("text", "raw", "content", "intro", "body", "snippet"):
                value = msg.get(field)
                if isinstance(value, str) and value.strip():
                    parts.append(value)
            html_list = msg.get("html") or []
            if isinstance(html_list, str):
                html_list = [html_list]
            for h in html_list:
                parts.append(re.sub(r"<[^>]+>", " ", h))
            subject = str(msg.get("subject", "") or "")
            combined = "\n".join(parts)
            # 再尝试 detail 接口补全内容
            try:
                detail = cloudflare_get_message_detail(api_base, dev_token, msg_id)
                for field in ("text", "raw", "content", "intro", "body", "snippet"):
                    value = detail.get(field)
                    if isinstance(value, str) and value.strip():
                        combined += "\n" + value
                html_list2 = detail.get("html") or []
                if isinstance(html_list2, str):
                    html_list2 = [html_list2]
                for h in html_list2:
                    combined += "\n" + re.sub(r"<[^>]+>", " ", h)
                if not subject:
                    subject = str(detail.get("subject", "") or "")
            except Exception as exc:
                if log_callback:
                    log_callback(f"[Debug] Cloudflare detail接口失败，改用列表内容解析: {exc}")
            if log_callback:
                log_callback(f"[Debug] Cloudflare 收到邮件: {subject}")
            code = extract_verification_code(combined, subject)
            if code:
                if log_callback:
                    log_callback(f"[*] Cloudflare 从邮件中提取到验证码: {code}")
                return code
            elif log_callback:
                log_callback(f"[Debug] 邮件已解析但未提取到验证码 id={msg_id} attempt={seen_attempts[msg_id]}")
        sleep_with_cancel(poll_interval, cancel_callback)
    raise Exception(f"Cloudflare 在 {timeout}s 内未收到验证码邮件")


def generate_random_birthdate():
    import datetime as dt

    today = dt.date.today()
    age = random.randint(20, 40)
    birth_year = today.year - age
    birth_month = random.randint(1, 12)
    birth_day = random.randint(1, 28)
    return f"{birth_year}-{birth_month:02d}-{birth_day:02d}T16:00:00.000Z"


def response_preview(res, limit=200):
    try:
        text = str(res.text or "")
    except Exception:
        text = ""
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def is_cloudflare_block_response(res):
    try:
        headers = {str(k).lower(): str(v).lower() for k, v in dict(res.headers).items()}
        text = str(res.text or "").lower()
        server = headers.get("server", "")
        content_type = headers.get("content-type", "")
        return (
            res.status_code in (403, 429, 503)
            and (
                "cloudflare" in server
                or "cloudflare" in text
                or "cf-error" in text
                or "__cf_chl" in text
                or "text/html" in content_type
            )
        )
    except Exception:
        return False


def set_birth_date(session, log_callback=None):
    url = "https://grok.com/rest/auth/set-birth-date"
    new_headers = {
        "content-type": "application/json",
        "origin": "https://grok.com",
        "referer": "https://grok.com/",
    }
    payload = {"birthDate": generate_random_birthdate()}
    try:
        res = session.post(url, json=payload, headers=new_headers, timeout=15)
        if log_callback:
            log_callback(
                f"[Debug] set_birth_date status: {res.status_code}, body: {response_preview(res)}"
            )
        if 200 <= res.status_code < 300:
            return True, "ok"
        if is_cloudflare_block_response(res):
            return (
                False,
                "set_birth_date 被 grok.com 的 Cloudflare 防护拦截，HTTP "
                f"{res.status_code}",
            )
        return False, f"set_birth_date HTTP {res.status_code}: {response_preview(res)}"
    except Exception as e:
        if log_callback:
            log_callback(f"[set_birth_date] 异常: {e}")
        return False, f"set_birth_date 异常: {e}"


def set_tos_accepted(session, log_callback=None):
    url = "https://accounts.x.ai/auth_mgmt.AuthManagement/SetTosAcceptedVersion"
    payload = struct.pack("B", (2 << 3) | 0) + struct.pack("B", 1)
    data = b"\x00" + struct.pack(">I", len(payload)) + payload
    new_headers = {
        "content-type": "application/grpc-web+proto",
        "x-grpc-web": "1",
        "x-user-agent": "connect-es/2.1.1",
        "origin": "https://accounts.x.ai",
        "referer": "https://accounts.x.ai/accept-tos",
    }
    try:
        res = session.post(url, data=data, headers=new_headers, timeout=15)
        if log_callback:
            log_callback(f"[Debug] set_tos_accepted status: {res.status_code}")
        if 200 <= res.status_code < 300:
            return True, "ok"
        if is_cloudflare_block_response(res):
            return (
                False,
                "set_tos_accepted 被 accounts.x.ai 的 Cloudflare 防护拦截，HTTP "
                f"{res.status_code}",
            )
        return False, f"set_tos_accepted HTTP {res.status_code}: {response_preview(res)}"
    except Exception as e:
        if log_callback:
            log_callback(f"[set_tos_accepted] 异常: {e}")
        return False, f"set_tos_accepted 异常: {e}"


def encode_grpc_nsfw_settings():
    field1_content = bytes([0x10, 0x01])
    field1 = bytes([0x0A, len(field1_content)]) + field1_content
    nsfw_string = b"always_show_nsfw_content"
    field2_inner = bytes([0x0A, len(nsfw_string)]) + nsfw_string
    field2 = bytes([0x12, len(field2_inner)]) + field2_inner
    payload = field1 + field2
    return b"\x00" + struct.pack(">I", len(payload)) + payload


def update_nsfw_settings(session, log_callback=None):
    url = "https://grok.com/auth_mgmt.AuthManagement/UpdateUserFeatureControls"
    data = encode_grpc_nsfw_settings()
    new_headers = {
        "content-type": "application/grpc-web+proto",
        "x-grpc-web": "1",
        "origin": "https://grok.com",
        "referer": "https://grok.com/",
    }
    try:
        res = session.post(url, data=data, headers=new_headers, timeout=15)
        if log_callback:
            log_callback(
                f"[Debug] update_nsfw status: {res.status_code}, body: {response_preview(res)}"
            )
        if 200 <= res.status_code < 300:
            return True, "ok"
        if is_cloudflare_block_response(res):
            return (
                False,
                "update_nsfw_settings 被 grok.com 的 Cloudflare 防护拦截，HTTP "
                f"{res.status_code}",
            )
        return False, f"update_nsfw_settings HTTP {res.status_code}: {response_preview(res)}"
    except Exception as e:
        if log_callback:
            log_callback(f"[update_nsfw] 异常: {e}")
        return False, f"update_nsfw_settings 异常: {e}"


def enable_nsfw_for_token(token, cf_clearance="", log_callback=None):
    proxies = get_proxies()
    user_agent = get_user_agent()
    try:
        with requests.Session(impersonate="chrome120", proxies=proxies) as session:
            cookie_parts = [f"sso={token}", f"sso-rw={token}"]
            if cf_clearance:
                cookie_parts.append(f"cf_clearance={cf_clearance}")
            session.headers.update(
                {
                    "user-agent": user_agent,
                    "cookie": "; ".join(cookie_parts),
                }
            )
            ok, message = set_tos_accepted(session, log_callback)
            if not ok:
                return False, message
            ok, message = set_birth_date(session, log_callback)
            if not ok:
                return False, message
            ok, message = update_nsfw_settings(session, log_callback)
            if not ok:
                return False, message
            return True, "成功开启 NSFW"
    except Exception as e:
        return False, f"异常: {str(e)}"


SIGNUP_URL = "https://accounts.x.ai/sign-up?redirect=grok-com"

_tls = threading.local()
_cpa_async_threads: list = []


def _wait_cpa_async_threads(timeout=300, log_callback=None, skip_if_stopping=None):
    global _cpa_async_threads
    if skip_if_stopping and skip_if_stopping():
        timeout = min(float(timeout or 0), 5.0)
        if log_callback:
            log_callback(f"[*] 停止中，仅短暂等待 CPA mint 线程（{timeout:.0f}s）...")
    with _cpa_threads_lock:
        threads = [t for t in _cpa_async_threads if t.is_alive()]
        _cpa_async_threads = [t for t in _cpa_async_threads if t.is_alive()]
    if not threads:
        return
    if log_callback and not (skip_if_stopping and skip_if_stopping()):
        log_callback(f"[*] 等待 {len(threads)} 个异步 CPA mint 线程完成...")
    deadline = time.time() + max(float(timeout or 0), 0)
    for t in threads:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        t.join(timeout=remaining)
    alive = [t for t in threads if t.is_alive()]
    if log_callback:
        if alive:
            log_callback(f"[!] {len(alive)} 个 CPA mint 线程超时未完成")
        else:
            log_callback("[+] 所有 CPA mint 线程已完成")


def _track_cpa_async_thread(thread):
    with _cpa_threads_lock:
        _cpa_async_threads.append(thread)


def _join_threads_interruptible(threads, should_stop=None, timeout=None, poll=0.5):
    """可被 stop/Ctrl+C 打断的线程等待，避免 join() 永久阻塞。"""
    threads = [t for t in (threads or []) if t is not None]
    if not threads:
        return
    deadline = None if timeout is None else (time.time() + max(float(timeout), 0))
    while any(t.is_alive() for t in threads):
        if should_stop and should_stop():
            # 给 worker 一点时间走 finally/stop_browser，再返回
            grace_deadline = time.time() + 3
            while any(t.is_alive() for t in threads) and time.time() < grace_deadline:
                for t in threads:
                    t.join(timeout=poll)
            return
        if deadline is not None and time.time() >= deadline:
            return
        for t in threads:
            t.join(timeout=poll)


def _get_browser():
    return getattr(_tls, 'browser', None)


def _set_browser(b):
    _tls.browser = b


def _get_page():
    return getattr(_tls, 'page', None)


def _set_page(p):
    _tls.page = p


def _get_worker_id():
    return getattr(_tls, 'worker_id', 0)


def _set_worker_id(wid):
    _tls.worker_id = wid


def setup_light_theme(root):
    try:
        root.option_add("*Background", UI_BG)
        root.option_add("*Foreground", UI_FG)
        root.option_add("*selectBackground", UI_ACTIVE_BG)
        root.option_add("*selectForeground", UI_FG)
        root.option_add("*insertBackground", UI_FG)
        root.option_add("*Entry.Background", UI_ENTRY_BG)
        root.option_add("*Text.Background", UI_ENTRY_BG)
        root.option_add("*Menu.Background", UI_ENTRY_BG)
        root.option_add("*Menu.Foreground", UI_FG)
        style = ttk.Style(root)
        available = set(style.theme_names())
        if "clam" in available:
            style.theme_use("clam")
        elif "default" in available:
            style.theme_use("default")
        root.configure(bg=UI_BG)
        style.configure(".", background=UI_BG, foreground=UI_FG, fieldbackground=UI_ENTRY_BG)
        style.configure("TFrame", background=UI_BG)
        style.configure("TLabelframe", background=UI_BG, foreground=UI_FG)
        style.configure("TLabelframe.Label", background=UI_BG, foreground=UI_FG)
        style.configure("TLabel", background=UI_BG, foreground=UI_FG)
        style.configure("TCheckbutton", background=UI_BG, foreground=UI_FG)
        style.configure("TButton", background=UI_BUTTON_BG, foreground=UI_FG)
        style.configure("TEntry", fieldbackground=UI_ENTRY_BG, foreground=UI_FG)
        style.configure("TCombobox", fieldbackground=UI_ENTRY_BG, foreground=UI_FG)
        style.configure("TSpinbox", fieldbackground=UI_ENTRY_BG, foreground=UI_FG)
    except Exception:
        pass


def tk_label(parent, text="", **kwargs):
    return tk.Label(parent, text=text, bg=kwargs.pop("bg", UI_BG), fg=kwargs.pop("fg", UI_FG), **kwargs)


def tk_entry(parent, textvariable=None, width=30, **kwargs):
    return tk.Entry(
        parent,
        textvariable=textvariable,
        width=width,
        bg=UI_ENTRY_BG,
        fg=UI_FG,
        insertbackground=UI_FG,
        disabledbackground="#2f2f2f",
        disabledforeground=UI_MUTED_FG,
        highlightthickness=1,
        highlightbackground="#555555",
        relief=tk.SOLID,
        **kwargs,
    )


def tk_button(parent, text="", command=None, state=tk.NORMAL, **kwargs):
    return tk.Button(
        parent,
        text=text,
        command=command,
        state=state,
        bg=UI_BUTTON_BG,
        fg=UI_FG,
        activebackground=UI_ACTIVE_BG,
        activeforeground=UI_FG,
        disabledforeground="#777777",
        relief=tk.RAISED,
        padx=10,
        pady=3,
        **kwargs,
    )


def tk_checkbutton(parent, text="", variable=None, **kwargs):
    return tk.Checkbutton(
        parent,
        text=text,
        variable=variable,
        bg=UI_BG,
        fg=UI_FG,
        activebackground=UI_BG,
        activeforeground=UI_FG,
        selectcolor="#3d7be0",
        **kwargs,
    )


def tk_option_menu(parent, variable, values, width=12):
    menu = tk.OptionMenu(parent, variable, *values)
    menu.configure(
        width=width,
        bg=UI_ENTRY_BG,
        fg=UI_FG,
        activebackground=UI_ACTIVE_BG,
        activeforeground=UI_FG,
        highlightthickness=1,
        highlightbackground="#555555",
        relief=tk.SOLID,
    )
    menu["menu"].configure(bg=UI_ENTRY_BG, fg=UI_FG, activebackground=UI_ACTIVE_BG, activeforeground=UI_FG)
    return menu


def start_browser(log_callback=None):
    last_exc = None
    for attempt in range(1, 5):
        try:
            _set_browser(Chromium(create_browser_options()))
            tabs = _get_browser().get_tabs()
            _set_page(tabs[-1] if tabs else _get_browser().new_tab())
            if log_callback and getattr(_get_browser(), "user_data_path", None):
                log_callback(f"[Debug] 当前浏览器资料目录: {_get_browser().user_data_path}")
            if log_callback and os.path.exists(EXTENSION_PATH):
                log_callback("[Debug] 已加载 turnstilePatch 扩展")
            restore_cf_cookies(log_callback=log_callback)
            if log_callback and attempt > 1:
                log_callback(f"[*] 浏览器第 {attempt} 次启动成功")
            return _get_browser(), _get_page()
        except Exception as exc:
            last_exc = exc
            if log_callback:
                log_callback(f"[Debug] 浏览器启动失败(第{attempt}/4次): {exc}")
            try:
                if _get_browser() is not None:
                    _get_browser().quit(del_data=True)
            except Exception:
                pass
            _set_browser(None)
            _set_page(None)
            time.sleep(min(1.5 * attempt, 4))
    raise Exception(f"浏览器启动失败，已重试4次: {last_exc}")


def stop_browser():
    snapshot_cf_cookies()
    profile_path = None
    browser = _get_browser()
    if browser is not None:
        try:
            profile_path = getattr(browser, "user_data_path", None)
        except Exception:
            profile_path = None
        try:
            browser.quit(del_data=True)
        except Exception:
            pass
    _set_browser(None)
    _set_page(None)
    if profile_path:
        try:
            import shutil

            root = os.path.abspath(
                os.path.join(os.path.dirname(os.path.abspath(__file__)), ".browser_profiles")
            )
            abs_profile = os.path.abspath(str(profile_path))
            if abs_profile.startswith(root) and os.path.isdir(abs_profile):
                shutil.rmtree(abs_profile, ignore_errors=True)
        except Exception:
            pass


def restart_browser(log_callback=None):
    stop_browser()
    return start_browser(log_callback=log_callback)


def prepare_clean_browser_session(log_callback=None, cancel_callback=None):
    """轻量清理：清登录态，可选保留 Cloudflare cookie，且不预访问注册站。"""
    raise_if_cancelled(cancel_callback)
    page = _get_page()
    browser = _get_browser()
    if page is None or browser is None:
        start_browser(log_callback=log_callback)
        page = _get_page()
        browser = _get_browser()
    try:
        if page is not None:
            try:
                page.get("about:blank")
            except Exception:
                pass
            try:
                page.run_js(
                    """
try { localStorage.clear(); } catch (e) {}
try { sessionStorage.clear(); } catch (e) {}
"""
                )
            except Exception:
                pass
        keep_cf = bool(config.get("keep_cf_cookies", True))
        cf_cookies = cf_turnstile.collect_cf_cookies(page, browser) if keep_cf else []
        if cf_cookies:
            _tls.cf_cookies = cf_cookies
        elif keep_cf:
            cf_cookies = list(getattr(_tls, "cf_cookies", None) or [])
        cf_turnstile.clear_browser_cookies(page, browser)
        if keep_cf and cf_cookies:
            cf_turnstile.inject_cookies(page, cf_cookies, log=log_callback)
            if log_callback:
                log_callback(f"[Debug] 已清理登录态并保留 {len(cf_cookies)} 个 Cloudflare cookie")
        elif log_callback:
            log_callback("[Debug] 已做轻量会话清理，准备打开注册页")
    except Exception as exc:
        if log_callback:
            log_callback(f"[Debug] 清理浏览器会话失败，将重启浏览器: {exc}")
        restart_browser(log_callback=log_callback)


def inspect_current_cloudflare(log_callback=None):
    """Return (kind, detail) where kind is hard-block, challenge, or none."""
    page = _get_page()
    if page is None:
        return "none", ""
    try:
        return cf_turnstile.inspect_cloudflare_page(page, log=log_callback)
    except Exception as exc:
        if log_callback:
            log_callback(f"[Debug] 读取页面检测 CF 失败: {exc}")
        return "none", ""


def detect_cloudflare_block_page(log_callback=None):
    """检测当前页是否为 Cloudflare 硬拦截/故障排除页（不含可点击的人机验证）。"""
    kind, detail = inspect_current_cloudflare(log_callback=log_callback)
    if kind == "hard-block":
        return True, detail
    return False, ""


def cleanup_runtime_memory(log_callback=None, reason="定期清理"):
    if log_callback:
        log_callback(f"[*] {reason}: 关闭浏览器并清理内存")
    stop_browser()
    collected = gc.collect()
    if log_callback:
        log_callback(f"[*] Python GC 已回收对象数: {collected}")


def refresh_active_page():
    if _get_browser() is None:
        restart_browser()
    try:
        tabs = _get_browser().get_tabs()
        if tabs:
            _set_page(tabs[-1])
        else:
            _set_page(_get_browser().new_tab())
    except Exception:
        restart_browser()
    return _get_page()


_EMAIL_SIGNUP_JS = r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
function nodeText(node) {
    return [
        node.innerText,
        node.textContent,
        node.getAttribute('aria-label'),
        node.getAttribute('title'),
        node.getAttribute('value'),
        node.getAttribute('href'),
        node.getAttribute('data-testid'),
        node.getAttribute('name'),
        node.getAttribute('id'),
    ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
function scoreEntry(node) {
    const text = nodeText(node);
    const compact = text.replace(/\s+/g, '');
    const lower = compact.toLowerCase();
    if (compact.includes('使用邮箱注册') || compact.includes('用邮箱注册') || compact.includes('邮箱注册')) return 100;
    if (lower.includes('signupwithemail') || lower.includes('sign-up-with-email') || lower.includes('sign_up_with_email')) return 95;
    if (lower.includes('continuewithemail') || lower.includes('continue-with-email')) return 90;
    if ((lower.includes('email') || compact.includes('邮箱')) &&
        (lower.includes('sign') || lower.includes('continue') || lower.includes('use') || lower.includes('with') || compact.includes('注册') || compact.includes('继续'))) {
        return 80;
    }
    if (lower === 'email' || lower === '邮箱' || compact.includes('电子邮箱')) return 70;
    return 0;
}
function emailInputReady() {
    const selectors = [
        'input[data-testid="email"]',
        'input[name="email"]',
        'input[type="email"]',
        'input[autocomplete="email"]',
        'input[placeholder*="mail" i]',
        'input[aria-label*="mail" i]',
        'input[aria-label*="邮箱"]',
        'input[placeholder*="邮箱"]',
    ];
    for (const sel of selectors) {
        const node = document.querySelector(sel);
        if (node && isVisible(node) && !node.disabled && !node.readOnly) return true;
    }
    return false;
}
function collectCandidates() {
    const nodes = Array.from(document.querySelectorAll(
        'button, a, [role="button"], input[type="button"], input[type="submit"], div[role="button"], span[role="button"]'
    ));
    return nodes
        .filter((node) => isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true')
        .map((node) => ({ node, score: scoreEntry(node), text: nodeText(node) }))
        .filter((item) => item.score > 0)
        .sort((a, b) => b.score - a.score);
}
const url = location.href || '';
const title = document.title || '';
const bodyText = (document.body && (document.body.innerText || document.body.textContent) || '').replace(/\s+/g, ' ').trim().slice(0, 240);
const candidates = collectCandidates();
const buttons = candidates.slice(0, 8).map((item) => item.text || '').filter(Boolean);
if (emailInputReady()) {
    return {
        state: 'email-form-ready',
        url,
        title,
        buttons,
        body: bodyText,
    };
}
const target = candidates[0] || null;
if (!target) {
    return {
        state: 'not-found',
        url,
        title,
        buttons: Array.from(document.querySelectorAll('button, a, [role="button"]'))
            .filter((node) => isVisible(node))
            .map(nodeText)
            .filter(Boolean)
            .slice(0, 10),
        body: bodyText,
    };
}
try { target.node.scrollIntoView({ block: 'center', inline: 'center' }); } catch (e) {}
target.node.click();
return {
    state: 'clicked',
    text: target.text || true,
    url,
    title,
    buttons,
    body: bodyText,
};
"""


def _signup_page_snapshot(log_callback=None):
    page = _get_page()
    if page is None:
        return {"url": "none", "title": "", "buttons": [], "body": ""}
    try:
        snap = page.run_js(
            r"""
function isVisible(node) {
  if (!node) return false;
  const style = window.getComputedStyle(node);
  if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
  const rect = node.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
}
function nodeText(node) {
  return [node.innerText, node.textContent, node.getAttribute('aria-label'), node.getAttribute('title'), node.getAttribute('href')]
    .filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
return {
  url: location.href || '',
  title: document.title || '',
  buttons: Array.from(document.querySelectorAll('button, a, [role="button"]'))
    .filter((n) => isVisible(n))
    .map(nodeText)
    .filter(Boolean)
    .slice(0, 12),
  body: ((document.body && (document.body.innerText || document.body.textContent)) || '').replace(/\s+/g, ' ').trim().slice(0, 300),
  hasEmail: !!document.querySelector('input[type="email"], input[name="email"], input[data-testid="email"]'),
};
"""
        )
        if isinstance(snap, dict):
            return snap
    except Exception as exc:
        if log_callback:
            log_callback(f"[Debug] 读取注册页快照失败: {exc}")
    try:
        return {
            "url": getattr(page, "url", "") or "",
            "title": "",
            "buttons": [],
            "body": (page.html or "")[:300],
            "hasEmail": False,
        }
    except Exception:
        return {"url": "none", "title": "", "buttons": [], "body": "", "hasEmail": False}


def click_email_signup_button(timeout=18, log_callback=None, cancel_callback=None):
    deadline = time.time() + timeout
    last_diag = 0.0
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        kind, detail = inspect_current_cloudflare()
        if kind == "hard-block":
            raise Exception(f"Cloudflare 拦截页，无法点击邮箱注册: {detail}")
        if kind == "challenge":
            if log_callback:
                log_callback("[*] 注册入口遇到人机验证，尝试自动点击")
            remain = max(3.0, deadline - time.time())
            try_solve_turnstile(
                log_callback=log_callback,
                cancel_callback=cancel_callback,
                timeout=min(_cf_timeout_sec(), remain),
            )
        if log_callback:
            log_callback("[Debug] 尝试查找“使用邮箱注册”按钮...")

        try:
            clicked = _get_page().run_js(_EMAIL_SIGNUP_JS)
        except Exception as exc:
            if log_callback:
                log_callback(f"[Debug] 查找邮箱注册按钮异常: {exc}")
            clicked = None

        state = clicked.get("state") if isinstance(clicked, dict) else clicked
        if state in ("clicked", True) or (isinstance(clicked, str) and clicked):
            detail = ""
            if isinstance(clicked, dict):
                detail = f": {clicked.get('text')}" if clicked.get("text") else ""
            elif isinstance(clicked, str):
                detail = f": {clicked}"
            if log_callback:
                log_callback(f"[*] 已点击「使用邮箱注册」按钮{detail}")
            sleep_with_cancel(1.5, cancel_callback)
            return True
        if state == "email-form-ready":
            if log_callback:
                log_callback("[*] 已处于邮箱注册表单，跳过入口按钮点击")
            return True

        now = time.time()
        if log_callback and now - last_diag >= 2:
            last_diag = now
            snap = clicked if isinstance(clicked, dict) else _signup_page_snapshot(log_callback)
            url = (snap or {}).get("url") or (_get_page().url if _get_page() else "none")
            buttons = " | ".join((snap or {}).get("buttons") or []) or "none"
            body = ((snap or {}).get("body") or "")[:160]
            log_callback(f"[Debug] 当前URL: {url}; buttons={buttons}; body={body}")

        # 页面若仍空白/未加载完，主动再刷一次注册页
        try:
            url_now = (_get_page().url if _get_page() else "") or ""
            if "about:blank" in url_now or not url_now:
                _get_page().get(SIGNUP_URL)
                _get_page().wait.doc_loaded()
        except Exception:
            pass
        sleep_with_cancel(0.8, cancel_callback)

    kind, detail = inspect_current_cloudflare(log_callback=log_callback)
    if kind == "hard-block":
        raise Exception(f"Cloudflare 拦截页，无法点击邮箱注册: {detail}")
    snap = _signup_page_snapshot(log_callback)
    if log_callback:
        log_callback(
            f"[Debug] 页面内容片段: url={snap.get('url')}; title={snap.get('title')}; "
            f"buttons={' | '.join(snap.get('buttons') or []) or 'none'}; body={(snap.get('body') or '')[:300]}"
        )
    fail_url = str(snap.get("url") or "unknown")
    fail_buttons = " | ".join(snap.get("buttons") or []) or "none"
    residual_hint = ""
    low = fail_url.lower()
    if any(k in low for k in ("tos-gate", "accept-tos", "/tos", "grok.com")) or any(
        k in fail_buttons for k in ("知道了", "Got it", "I understand")
    ):
        residual_hint = "；疑似上号会话/TOS 残留（非缺点击流程），账号结束后将完整重启浏览器"
    raise Exception(
        "未找到「使用邮箱注册」按钮"
        f"（url={fail_url}; buttons={fail_buttons}{residual_hint}）"
    )


def open_signup_page(log_callback=None, cancel_callback=None):
    raise_if_cancelled(cancel_callback)
    if _get_browser() is None:
        start_browser(log_callback=log_callback)
        if log_callback:
            log_callback("[*] 浏览器已启动")
        if not os.path.exists(EXTENSION_PATH) and log_callback:
            log_callback("[!] 未找到 turnstilePatch 扩展目录，Turnstile 辅助可能不可用")
    prepare_clean_browser_session(log_callback=log_callback, cancel_callback=cancel_callback)
    last_exc = None
    opened = False
    for attempt in range(1, 4):
        raise_if_cancelled(cancel_callback)
        try:
            browser = _get_browser()
            if browser is None:
                start_browser(log_callback=log_callback)
                browser = _get_browser()
            try:
                tabs = browser.get_tabs()
                _set_page(tabs[0] if tabs else browser.new_tab())
            except Exception:
                _set_page(browser.new_tab())
            _get_page().get(SIGNUP_URL)
            _get_page().wait.doc_loaded()
            # 给 CF/前端一点渲染时间
            sleep_with_cancel(1.2, cancel_callback)
            kind, detail = inspect_current_cloudflare(log_callback=log_callback)
            if kind == "challenge":
                if log_callback:
                    log_callback("[*] 打开注册页后出现人机验证，尝试自动处理")
                token = try_solve_turnstile(
                    log_callback=log_callback, cancel_callback=cancel_callback
                )
                kind, detail = inspect_current_cloudflare(log_callback=log_callback)
                if kind == "challenge" and not token:
                    last_exc = Exception(f"Cloudflare 人机验证未通过: {detail}")
                    if log_callback:
                        log_callback(f"[!] 人机验证未自动通过，重启浏览器重试 ({attempt}/3): {detail}")
                    restart_browser(log_callback=log_callback)
                    sleep_with_cancel(1.5, cancel_callback)
                    continue
            if kind == "hard-block":
                last_exc = Exception(f"Cloudflare 拦截页: {detail}")
                if log_callback:
                    log_callback(f"[!] 检测到 Cloudflare 拦截/故障排除页，重启浏览器重试 ({attempt}/3): {detail}")
                restart_browser(log_callback=log_callback)
                sleep_with_cancel(1.5, cancel_callback)
                continue
            last_exc = None
            opened = True
            break
        except RegistrationCancelled:
            raise
        except Exception as e:
            last_exc = e
            if log_callback:
                log_callback(f"[Debug] 打开注册页失败(第{attempt}/3次): {e}")
            try:
                restart_browser(log_callback=log_callback)
            except Exception as e2:
                if log_callback:
                    log_callback(f"[Debug] 重启浏览器失败: {e2}")
            sleep_with_cancel(1, cancel_callback)
    if not opened:
        raise Exception(f"打开注册页失败: {last_exc}")

    _deadline = time.time() + 10
    while time.time() < _deadline:
        raise_if_cancelled(cancel_callback)
        kind, detail = inspect_current_cloudflare(log_callback=log_callback)
        if kind == "challenge":
            try_solve_turnstile(log_callback=log_callback, cancel_callback=cancel_callback)
            kind, detail = inspect_current_cloudflare(log_callback=log_callback)
        if kind == "hard-block":
            if log_callback:
                log_callback(f"[!] 注册页加载后仍是 Cloudflare 拦截页: {detail}")
            raise Exception(f"Cloudflare 拦截页: {detail}")
        try:
            _ready = _get_page().run_js(
                "return !!document.querySelector('button, input[type=\"email\"], a[href*=\"sign\"], a[href*=\"email\"], form')"
            )
            if _ready:
                break
        except Exception:
            pass
        time.sleep(0.3)
    if log_callback:
        log_callback(f"[*] 当前URL: {_get_page().url}")
    click_email_signup_button(
        log_callback=log_callback, cancel_callback=cancel_callback
    )


def has_profile_form(log_callback=None):
    refresh_active_page()
    try:
        return bool(
            _get_page().run_js(
                """
const givenInput = document.querySelector('input[data-testid="givenName"], input[name="givenName"], input[autocomplete="given-name"]');
const familyInput = document.querySelector('input[data-testid="familyName"], input[name="familyName"], input[autocomplete="family-name"]');
const passwordInput = document.querySelector('input[data-testid="password"], input[name="password"], input[type="password"]');
return !!(givenInput && familyInput && passwordInput);
            """
            )
        )
    except Exception:
        return False


def fill_email_and_submit(timeout=45, log_callback=None, cancel_callback=None):
    raise_if_cancelled(cancel_callback)
    email, dev_token = get_email_and_token()
    if not email or not dev_token:
        raise Exception("获取邮箱失败")
    if log_callback:
        log_callback(f"[*] 已创建邮箱: {email}")
    deadline = time.time() + timeout
    last_diag_time = 0
    last_reclick_time = 0
    last_cf_time = 0
    last_snapshot = None
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        now = time.time()
        if now - last_cf_time >= 3:
            kind, _detail = inspect_current_cloudflare()
            if kind == "challenge":
                try_solve_turnstile(log_callback=log_callback, cancel_callback=cancel_callback)
            last_cf_time = now
        filled = _get_page().run_js(
            """
const email = arguments[0];
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
function textOf(node) {
    return [
        node.innerText,
        node.textContent,
        node.getAttribute('aria-label'),
        node.getAttribute('title'),
        node.getAttribute('placeholder'),
        node.getAttribute('data-testid'),
        node.getAttribute('name'),
        node.getAttribute('id'),
        node.getAttribute('autocomplete'),
    ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
function describeInput(node) {
    return [
        `type=${node.getAttribute('type') || ''}`,
        `name=${node.getAttribute('name') || ''}`,
        `id=${node.getAttribute('id') || ''}`,
        `placeholder=${node.getAttribute('placeholder') || ''}`,
        `aria=${node.getAttribute('aria-label') || ''}`,
        `testid=${node.getAttribute('data-testid') || ''}`,
    ].join(' ').replace(/\s+/g, ' ').trim().slice(0, 160);
}
function describeAction(node) {
    return textOf(node).slice(0, 120);
}
function emailCandidates() {
    const direct = Array.from(document.querySelectorAll('input[data-testid="email"], input[name="email"], input[type="email"], input[autocomplete="email"], input[placeholder*="mail" i], input[aria-label*="mail" i]'));
    const all = Array.from(document.querySelectorAll('input, textarea'));
    for (const node of all) {
        const type = (node.getAttribute('type') || '').toLowerCase();
        if (['hidden', 'submit', 'button', 'checkbox', 'radio', 'file', 'search'].includes(type)) continue;
        const meta = textOf(node).toLowerCase();
        if (meta.includes('email') || meta.includes('e-mail') || meta.includes('mail') || meta.includes('邮箱') || meta.includes('电子邮件')) {
            direct.push(node);
        }
    }
    return Array.from(new Set(direct));
}
const visibleInputs = Array.from(document.querySelectorAll('input, textarea'))
    .filter((node) => isVisible(node) && !node.disabled && !node.readOnly)
    .map(describeInput)
    .slice(0, 8);
const visibleActions = Array.from(document.querySelectorAll('button, a, [role="button"]'))
    .filter((node) => isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true')
    .map(describeAction)
    .filter(Boolean)
    .slice(0, 10);
const input = emailCandidates().find((node) => isVisible(node) && !node.disabled && !node.readOnly) || null;
if (!input) {
    return {
        state: 'not-ready',
        url: location.href,
        title: document.title,
        inputs: visibleInputs,
        buttons: visibleActions,
    };
}
input.focus(); input.click();
const valueProto = input instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
const valueSetter = Object.getOwnPropertyDescriptor(valueProto, 'value')?.set;
const tracker = input._valueTracker;
if (tracker) tracker.setValue('');
if (valueSetter) valueSetter.call(input, email); else input.value = email;
input.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, data: email, inputType: 'insertText' }));
input.dispatchEvent(new InputEvent('input', { bubbles: true, data: email, inputType: 'insertText' }));
input.dispatchEvent(new Event('change', { bubbles: true }));
const inputType = (input.getAttribute('type') || '').toLowerCase();
const isValid = inputType !== 'email' || input.checkValidity();
if ((input.value || '').trim() !== email || !isValid) {
    return {
        state: 'fill-failed',
        value: input.value || '',
        valid: isValid,
        input: describeInput(input),
        url: location.href,
    };
}
input.blur();
return {
    state: 'filled',
    input: describeInput(input),
    url: location.href,
};
            """,
            email,
        )
        state = filled.get("state") if isinstance(filled, dict) else filled
        if isinstance(filled, dict):
            last_snapshot = filled
        if state == "not-ready":
            now = time.time()
            if now - last_reclick_time >= 3:
                try:
                    reclicked = _get_page().run_js(_EMAIL_SIGNUP_JS)
                except Exception:
                    reclicked = None
                last_reclick_time = now
                re_state = reclicked.get("state") if isinstance(reclicked, dict) else reclicked
                if re_state == "email-form-ready":
                    if log_callback:
                        log_callback("[Debug] 邮箱输入框检测中：页面已进入邮箱表单")
                elif re_state in ("clicked", True) or (isinstance(reclicked, str) and reclicked):
                    detail = ""
                    if isinstance(reclicked, dict) and reclicked.get("text"):
                        detail = f": {reclicked.get('text')}"
                    elif isinstance(reclicked, str):
                        detail = f": {reclicked}"
                    if log_callback:
                        log_callback(f"[Debug] 邮箱输入框未出现，已再次触发邮箱注册入口{detail}")
            if log_callback and now - last_diag_time >= 5:
                last_diag_time = now
                inputs = " | ".join((filled or {}).get("inputs", [])[:6]) if isinstance(filled, dict) else ""
                buttons = " | ".join((filled or {}).get("buttons", [])[:8]) if isinstance(filled, dict) else ""
                url = (filled or {}).get("url", _get_page().url if _get_page() else "") if isinstance(filled, dict) else (_get_page().url if _get_page() else "")
                log_callback(f"[Debug] 等待邮箱输入框: url={url}; inputs={inputs or 'none'}; buttons={buttons or 'none'}")
            sleep_with_cancel(0.5, cancel_callback)
            continue
        if state != "filled":
            if log_callback:
                log_callback(f"[Debug] 邮箱输入框已出现，但写入失败: {filled}")
            sleep_with_cancel(0.5, cancel_callback)
            continue
        sleep_with_cancel(0.8, cancel_callback)
        clicked = _get_page().run_js(
            r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
function textOf(node) {
    return [
        node.innerText,
        node.textContent,
        node.getAttribute('aria-label'),
        node.getAttribute('title'),
        node.getAttribute('placeholder'),
        node.getAttribute('data-testid'),
        node.getAttribute('name'),
        node.getAttribute('id'),
        node.getAttribute('autocomplete'),
    ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
function emailCandidates() {
    const direct = Array.from(document.querySelectorAll('input[data-testid="email"], input[name="email"], input[type="email"], input[autocomplete="email"], input[placeholder*="mail" i], input[aria-label*="mail" i]'));
    const all = Array.from(document.querySelectorAll('input, textarea'));
    for (const node of all) {
        const type = (node.getAttribute('type') || '').toLowerCase();
        if (['hidden', 'submit', 'button', 'checkbox', 'radio', 'file', 'search'].includes(type)) continue;
        const meta = textOf(node).toLowerCase();
        if (meta.includes('email') || meta.includes('e-mail') || meta.includes('mail') || meta.includes('邮箱') || meta.includes('电子邮件')) {
            direct.push(node);
        }
    }
    return Array.from(new Set(direct));
}
const input = emailCandidates().find((node) => isVisible(node) && !node.disabled && !node.readOnly) || null;
if (!input || !(input.value || '').trim()) return false;
const inputType = (input.getAttribute('type') || '').toLowerCase();
if (inputType === 'email' && !input.checkValidity()) return false;
const buttons = Array.from(document.querySelectorAll('button[type="submit"], button, [role="button"], input[type="submit"]'))
    .filter((node) => isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true');
const submitButton = buttons.find((node) => {
    const text = textOf(node).replace(/\s+/g, '');
    const lower = text.toLowerCase();
    return (
        text === '注册' ||
        text.includes('注册') ||
        text.includes('继续') ||
        text.includes('下一步') ||
        text.includes('确认') ||
        lower.includes('signup') ||
        lower.includes('sign up') ||
        lower.includes('continue') ||
        lower.includes('next') ||
        lower.includes('createaccount') ||
        lower.includes('submit')
    );
});
if (submitButton) {
    submitButton.click();
    return textOf(submitButton) || true;
}
const form = input.closest('form');
if (form) {
    if (form.requestSubmit) form.requestSubmit();
    else form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    return 'form-submit';
}
input.focus();
input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', bubbles: true, cancelable: true }));
input.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', code: 'Enter', bubbles: true, cancelable: true }));
return 'enter';
            """
        )
        if clicked:
            if log_callback:
                detail = f" ({clicked})" if isinstance(clicked, str) else ""
                log_callback(f"[*] 已填写邮箱并提交: {email}{detail}")
            return email, dev_token
        sleep_with_cancel(0.5, cancel_callback)
    if last_snapshot:
        inputs = " | ".join(last_snapshot.get("inputs", [])[:6])
        buttons = " | ".join(last_snapshot.get("buttons", [])[:8])
        url = last_snapshot.get("url", _get_page().url if _get_page() else "")
        raise Exception(
            f"未找到邮箱输入框或注册按钮，最后页面: url={url}; inputs={inputs or 'none'}; buttons={buttons or 'none'}"
        )
    raise Exception("未找到邮箱输入框或注册按钮")


def fill_code_and_submit(email, dev_token, timeout=180, log_callback=None, cancel_callback=None):
    def _resend_code():
        _get_page().run_js(
            r"""
const nodes = Array.from(document.querySelectorAll('button, a, [role="button"]'));
const target = nodes.find((node) => {
  const t = (node.innerText || node.textContent || '').replace(/\s+/g, '').toLowerCase();
  return t.includes('重新发送') || t.includes('resend') || t.includes('再次发送');
});
if (target && !target.disabled) { target.click(); return true; }
return false;
            """
        )

    code = get_oai_code(
        dev_token,
        email,
        log_callback=log_callback,
        cancel_callback=cancel_callback,
        resend_callback=_resend_code,
    )
    if not code:
        raise Exception("获取验证码失败")
    clean_code = str(code).replace("-", "").strip()
    deadline = time.time() + timeout

    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        filled = _get_page().run_js(
            """
const code = String(arguments[0] || '').trim();
if (!code) return 'empty-code';

function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}

function setInputValue(input, value) {
    const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
    const tracker = input._valueTracker;
    if (tracker) tracker.setValue('');
    if (nativeSetter) nativeSetter.call(input, value);
    else input.value = value;
    input.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, data: value, inputType: 'insertText' }));
    input.dispatchEvent(new InputEvent('input', { bubbles: true, data: value, inputType: 'insertText' }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
}

const aggregate = Array.from(document.querySelectorAll(
  'input[data-input-otp=\"true\"], input[name=\"code\"], input[autocomplete=\"one-time-code\"], input[inputmode=\"numeric\"], input[inputmode=\"text\"]'
)).find((node) => isVisible(node) && !node.disabled && !node.readOnly && Number(node.maxLength || 6) > 1);

if (aggregate) {
    aggregate.focus();
    aggregate.click();
    setInputValue(aggregate, code);
    return String(aggregate.value || '').replace(/\\s+/g, '') ? 'filled-aggregate' : 'aggregate-failed';
}

const otpBoxes = Array.from(document.querySelectorAll('input')).filter((node) => {
    if (!isVisible(node) || node.disabled || node.readOnly) return false;
    const maxLength = Number(node.maxLength || 0);
    const ac = String(node.autocomplete || '').toLowerCase();
    return maxLength === 1 || ac === 'one-time-code';
});

if (otpBoxes.length >= code.length) {
    for (let i = 0; i < code.length; i += 1) {
        const ch = code[i] || '';
        const box = otpBoxes[i];
        box.focus();
        box.click();
        setInputValue(box, ch);
        box.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: ch }));
        box.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: ch }));
    }
    const merged = otpBoxes.slice(0, code.length).map((x) => String(x.value || '').trim()).join('');
    return merged.length ? 'filled-boxes' : 'boxes-failed';
}

return 'not-ready';
            """,
            clean_code,
        )

        if filled == "not-ready":
            sleep_with_cancel(0.5, cancel_callback)
            continue
        if "failed" in str(filled):
            if log_callback:
                log_callback(f"[Debug] 验证码填写失败: {filled}")
            sleep_with_cancel(0.5, cancel_callback)
            continue

        clicked = _get_page().run_js(
            r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}

const buttons = Array.from(document.querySelectorAll('button[type=\"submit\"], button')).filter((node) => {
    return isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true';
});

const btn = buttons.find((node) => {
    const t = (node.innerText || node.textContent || '').replace(/\\s+/g, '').toLowerCase();
    return (
        t.includes('确认邮箱') ||
        t.includes('继续') ||
        t.includes('下一步') ||
        t.includes('confirm') ||
        t.includes('continue') ||
        t.includes('next')
    );
});

if (!btn) return 'no-button';
btn.focus();
btn.click();
return 'clicked';
            """
        )

        if clicked == "clicked" or clicked == "no-button":
            if log_callback:
                log_callback(f"[*] 已填写验证码并提交: {code}")
            sleep_with_cancel(1.5, cancel_callback)
            return code

        sleep_with_cancel(0.5, cancel_callback)

    raise Exception("验证码已获取，但自动填写/提交失败")


def getTurnstileToken(log_callback=None, cancel_callback=None):
    if _get_page() is None:
        raise Exception("页面未就绪，无法执行 Turnstile")
    token = try_solve_turnstile(
        log_callback=log_callback, cancel_callback=cancel_callback, require=True
    )
    if cf_turnstile.is_turnstile_token_ready(token):
        return token
    # 不抛错：外层等待循环可继续，方便手动点完后自动接着走
    if log_callback:
        log_callback("[!] Turnstile 本轮未拿到 token，继续等待/重试自动点击")
    return ""


def _sync_turnstile_token(token, log_callback=None):
    if not token or token == "passed" or _get_page() is None:
        return None
    synced = _get_page().run_js(
        """
const token = String(arguments[0] || '').trim();
const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
if (!cfInput || !token) return false;
const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
if (nativeSetter) nativeSetter.call(cfInput, token);
else cfInput.value = token;
cfInput.dispatchEvent(new Event('input', { bubbles: true }));
cfInput.dispatchEvent(new Event('change', { bubbles: true }));
return String(cfInput.value || '').trim().length;
        """,
        token,
    )
    if log_callback:
        log_callback(f"[*] Turnstile 处理完成，回填长度={synced}")
    return synced


def build_profile():
    given_name_pool = [
        "Neo", "Ethan", "Liam", "Noah", "Lucas", "Mason", "Ryan", "Leo",
        "Owen", "Aiden", "Elio", "Aron", "Ivan", "Nolan", "Evan", "Kai",
        "Caleb", "Adam", "Ezra", "Miles", "Logan", "Carter", "Hunter", "Jason",
        "Brian", "Dylan", "Alex", "Colin", "Blake", "Gavin", "Henry", "Julian",
        "Kevin", "Louis", "Marcus", "Nathan", "Oscar", "Peter", "Quinn", "Robin",
        "Simon", "Tristan", "Victor", "Wesley", "Xavier", "Yuri", "Zane", "Felix",
        "Aaron", "Damian",
    ]
    family_name_pool = [
        "Lin", "Wang", "Zhao", "Liu", "Chen", "Zhang", "Xu", "Sun",
        "Guo", "He", "Yang", "Wu", "Zhou", "Tang", "Qin", "Shi",
        "Fang", "Peng", "Cao", "Deng", "Fan", "Fu", "Gao", "Han",
        "Hu", "Jiang", "Kong", "Lu", "Ma", "Nie", "Pan", "Qiao",
        "Ren", "Shao", "Tian", "Xie", "Yan", "Yao", "Yu", "Zeng",
        "Bai", "Duan", "Hou", "Jin", "Kang", "Luo", "Mao", "Song",
        "Wei", "Xiong",
    ]
    given_name = random.choice(given_name_pool)
    family_name = random.choice(family_name_pool)
    password = "N" + secrets.token_hex(4) + "!a7#" + secrets.token_urlsafe(6)
    return given_name, family_name, password


def fill_profile_and_submit(timeout=120, log_callback=None, cancel_callback=None):
    given_name, family_name, password = build_profile()
    deadline = time.time() + timeout
    form_filled_once = False
    wait_cf_since = None
    last_cf_retry_at = 0.0

    # 资料页一进来就先快速点一次，不要等填完表单才开始处理
    try:
        early = try_solve_turnstile(
            log_callback=log_callback,
            cancel_callback=cancel_callback,
            timeout=8,
            require=False,
        )
        if early:
            last_cf_retry_at = time.time()
    except RegistrationCancelled:
        raise
    except Exception as exc:
        if log_callback:
            log_callback(f"[Debug] 资料页预点击 Turnstile 跳过: {exc}")

    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        if not form_filled_once:
            filled = _get_page().run_js(
                """
const givenName = arguments[0];
const familyName = arguments[1];
const password = arguments[2];

function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}

function pickInput(selector) {
    return Array.from(document.querySelectorAll(selector)).find((node) => {
        return isVisible(node) && !node.disabled && !node.readOnly;
    }) || null;
}

function setInputValue(input, value) {
    if (!input) return false;
    input.focus();
    input.click();
    const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
    const tracker = input._valueTracker;
    if (tracker) tracker.setValue('');
    if (nativeSetter) nativeSetter.call(input, value);
    else input.value = value;
    input.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, data: value, inputType: 'insertText' }));
    input.dispatchEvent(new InputEvent('input', { bubbles: true, data: value, inputType: 'insertText' }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    input.blur();
    return String(input.value || '').trim() === String(value || '').trim();
}

const givenInput = pickInput('input[data-testid="givenName"], input[name="givenName"], input[autocomplete="given-name"], input[aria-label*="名"]');
const familyInput = pickInput('input[data-testid="familyName"], input[name="familyName"], input[autocomplete="family-name"], input[aria-label*="姓"]');
const passwordInput = pickInput('input[data-testid="password"], input[name="password"], input[type="password"], input[autocomplete="new-password"]');

if (!givenInput || !familyInput || !passwordInput) return 'not-ready';

const ok1 = setInputValue(givenInput, givenName);
const ok2 = setInputValue(familyInput, familyName);
const ok3 = setInputValue(passwordInput, password);

if (!ok1 || !ok2 || !ok3) return 'fill-failed';

const buttons = Array.from(document.querySelectorAll('button[type="submit"], button, [role="button"], input[type="submit"]')).filter((node) => {
    return isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true';
});
const submitBtn = buttons.find((node) => {
    const t = (node.innerText || node.textContent || '').replace(/\\s+/g, '').toLowerCase();
    return t.includes('完成注册') || t.includes('创建账户') || t.includes('signup') || t.includes('createaccount');
});

// 必须等待 Cloudflare 校验通过后再提交
const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
const cfPresent = !!cfInput
  || !!document.querySelector('iframe[src*="turnstile"], div.cf-turnstile, [data-sitekey], script[src*="turnstile"]');
if (cfPresent) {
    const token = String((cfInput && cfInput.value) || '').trim();
    const solvedByToken = token.length >= 80;
    if (!solvedByToken) return 'wait-cloudflare:' + token.length;
}

if (submitBtn) {
    return 'ready-to-submit';
}
return 'filled-no-submit';
            """,
                given_name,
                family_name,
                password,
            )

            if isinstance(filled, str) and filled.startswith("wait-cloudflare"):
                form_filled_once = True
                token_len = filled.split(":", 1)[1] if ":" in filled else "0"
                if log_callback:
                    log_callback(f"[*] 资料已填写，等待 Cloudflare 人机验证通过... 当前token长度={token_len}")
                now = time.time()
                if wait_cf_since is None:
                    wait_cf_since = now
                if now - last_cf_retry_at >= 0.4:
                    try:
                        token = try_solve_turnstile(
                            log_callback=log_callback,
                            cancel_callback=cancel_callback,
                            timeout=min(12.0, max(3.0, deadline - time.time())),
                            require=True,
                        )
                        if token:
                            _sync_turnstile_token(token, log_callback=log_callback)
                    except Exception as cf_exc:
                        if log_callback:
                            log_callback(f"[Debug] Turnstile 自动处理异常: {cf_exc}")
                    last_cf_retry_at = now
                sleep_with_cancel(0.35, cancel_callback)
                continue

            if filled in ("ready-to-submit", "filled-no-submit"):
                form_filled_once = True
            elif filled == "fill-failed" and log_callback:
                log_callback("[Debug] 资料输入失败，重试中...")
                sleep_with_cancel(0.5, cancel_callback)
                continue
            elif filled == "not-ready":
                sleep_with_cancel(0.5, cancel_callback)
                continue

        submit_state = _get_page().run_js(
            r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}

const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
const cfPresent = !!cfInput
  || !!document.querySelector('iframe[src*="turnstile"], div.cf-turnstile, [data-sitekey], script[src*="turnstile"]');
if (cfPresent) {
    const token = String((cfInput && cfInput.value) || '').trim();
    const solvedByToken = token.length >= 80;
    if (!solvedByToken) return 'wait-cloudflare:' + token.length;
}

function buttonText(node) {
    return [
        node.innerText,
        node.textContent,
        node.getAttribute('value'),
        node.getAttribute('aria-label'),
        node.getAttribute('title'),
    ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
const buttons = Array.from(document.querySelectorAll('button[type="submit"], button, [role="button"], input[type="submit"]')).filter((node) => {
    return isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true';
});
const submitBtn = buttons.find((node) => {
    const t = buttonText(node).replace(/\s+/g, '').toLowerCase();
    return t.includes('完成注册') || t.includes('创建账户') || t.includes('signup') || t.includes('createaccount');
});
if (!submitBtn) {
    const visibleTexts = buttons.map(buttonText).filter(Boolean).slice(0, 8).join(' | ');
    return 'no-submit-button:' + visibleTexts;
}
submitBtn.focus();
submitBtn.click();
return 'submitted';
            """
        )

        if isinstance(submit_state, str) and submit_state.startswith("wait-cloudflare"):
            if log_callback:
                token_len = submit_state.split(":", 1)[1] if ":" in submit_state else "0"
                log_callback(f"[*] 等待 Cloudflare 人机验证通过后再提交... 当前token长度={token_len}")
            now = time.time()
            if wait_cf_since is None:
                wait_cf_since = now
            if now - last_cf_retry_at >= 0.4:
                try:
                    token = try_solve_turnstile(
                        log_callback=log_callback,
                        cancel_callback=cancel_callback,
                        timeout=min(12.0, max(3.0, deadline - time.time())),
                        require=True,
                    )
                    if token:
                        _sync_turnstile_token(token, log_callback=log_callback)
                except Exception as cf_exc:
                    if log_callback:
                        log_callback(f"[Debug] Turnstile 自动处理异常: {cf_exc}")
                last_cf_retry_at = now
            sleep_with_cancel(0.35, cancel_callback)
            continue

        if submit_state == "submitted":
            if log_callback:
                log_callback(f"[*] 已填写注册资料并提交: {given_name} {family_name}")
            return {"given_name": given_name, "family_name": family_name, "password": password}
        wait_cf_since = None
        if isinstance(submit_state, str) and submit_state.startswith("no-submit-button") and log_callback:
            visible_buttons = submit_state.split(":", 1)[1] if ":" in submit_state else ""
            suffix = f" 可见按钮: {visible_buttons}" if visible_buttons else ""
            log_callback(f"[Debug] 未找到提交按钮，继续等待页面稳定...{suffix}")

        sleep_with_cancel(0.5, cancel_callback)

    raise Exception("最终注册页资料填写失败")


def wait_for_sso_cookie(timeout=120, log_callback=None, cancel_callback=None):
    deadline = time.time() + timeout
    last_seen_names = set()
    last_submit_retry = 0.0
    last_cf_retry_at = 0.0
    final_no_submit_state = ""
    final_no_submit_since = None
    final_no_submit_timeout = 25

    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        try:
            refresh_active_page()
            if _get_page() is None:
                sleep_with_cancel(1, cancel_callback)
                continue

            # 仍停留在“完成注册”页时，若 Cloudflare 已通过，周期性重试点击提交
            now = time.time()
            if now - last_submit_retry >= 2.5:
                retried = _get_page().run_js(
                    r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
const titleHit = !!Array.from(document.querySelectorAll('h1,h2,div,span')).find((el) => {
    const t = (el.textContent || '').replace(/\s+/g, '');
    const lower = t.toLowerCase();
    return t.includes('完成注册') || lower.includes('completeyoursignup') || lower.includes('completesignup');
});
if (!titleHit) return 'not-final-page';

const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
const cfPresent = !!cfInput
  || !!document.querySelector('iframe[src*="turnstile"], div.cf-turnstile, [data-sitekey], script[src*="turnstile"]');
if (cfPresent) {
    const token = String((cfInput && cfInput.value) || '').trim();
    const solved = token.length >= 80;
    if (!solved) return 'final-page-wait-cf:' + token.length;
}

function buttonText(node) {
    return [
        node.innerText,
        node.textContent,
        node.getAttribute('value'),
        node.getAttribute('aria-label'),
        node.getAttribute('title'),
    ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
const buttons = Array.from(document.querySelectorAll('button[type="submit"], button, [role="button"], input[type="submit"]')).filter((node) => {
    return isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true';
});
const submitBtn = buttons.find((node) => {
    const t = buttonText(node).replace(/\s+/g, '').toLowerCase();
    return t.includes('完成注册') || t.includes('创建账户') || t.includes('signup') || t.includes('createaccount');
});
if (!submitBtn) {
    const visibleTexts = buttons.map(buttonText).filter(Boolean).slice(0, 8).join(' | ');
    return 'final-page-no-submit:' + visibleTexts;
}
submitBtn.focus();
submitBtn.click();
return 'final-page-clicked-submit';
                    """
                )
                last_submit_retry = now
                if log_callback and (retried == "final-page-clicked-submit" or (isinstance(retried, str) and retried.startswith("final-page-no-submit"))):
                    log_callback(f"[Debug] 最终页状态: {retried}")
                if isinstance(retried, str) and retried.startswith("final-page-no-submit"):
                    if retried != final_no_submit_state:
                        final_no_submit_state = retried
                        final_no_submit_since = now
                    elif final_no_submit_since and now - final_no_submit_since >= final_no_submit_timeout:
                        raise AccountRetryNeeded(
                            f"最终注册页状态 {final_no_submit_timeout}s 未变化且未找到提交按钮，重试当前账号: {retried}"
                        )
                else:
                    final_no_submit_state = ""
                    final_no_submit_since = None
                if log_callback and isinstance(retried, str) and retried.startswith("final-page-wait-cf"):
                    token_len = retried.split(":", 1)[1] if ":" in retried else "0"
                    log_callback(f"[Debug] 最终页状态: final-page-wait-cf, token长度={token_len}")
                    if now - last_cf_retry_at >= 0.4:
                        if log_callback:
                            log_callback("[*] 最终页 Cloudflare 未通过，自动点击人机验证...")
                        try:
                            token = try_solve_turnstile(
                                log_callback=log_callback,
                                cancel_callback=cancel_callback,
                                timeout=min(12.0, max(3.0, deadline - time.time())),
                                require=True,
                            )
                            if token:
                                _sync_turnstile_token(token, log_callback=log_callback)
                        except Exception as cf_exc:
                            if log_callback:
                                log_callback(f"[Debug] 最终页 Turnstile 自动处理异常: {cf_exc}")
                        last_cf_retry_at = now

            cookies = _get_page().cookies(all_domains=True, all_info=True) or []
            for item in cookies:
                if isinstance(item, dict):
                    name = str(item.get("name", "")).strip()
                    value = str(item.get("value", "")).strip()
                else:
                    name = str(getattr(item, "name", "")).strip()
                    value = str(getattr(item, "value", "")).strip()

                if name:
                    last_seen_names.add(name)

                if name == "sso" and value:
                    if log_callback:
                        log_callback("[*] 已获取到 sso cookie")
                    return value
        except PageDisconnectedError:
            refresh_active_page()
        except AccountRetryNeeded:
            raise
        except Exception:
            pass

        sleep_with_cancel(1, cancel_callback)

    raise Exception(
        f"等待超时：未获取到 sso cookie。已看到 cookies: {sorted(last_seen_names)}"
    )


class GrokRegisterGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Grok 注册机")
        self.root.geometry("1120x900")
        self.root.minsize(960, 700)
        self.is_running = False
        self.batch_count = 0
        self.success_count = 0
        self.fail_count = 0
        self.results = []
        self.stop_requested = False
        self.ui_queue = queue.Queue()
        self.accounts_output_file = ""
        self.setup_ui()

    def setup_ui(self):
        load_config()
        main_frame = tk.Frame(self.root, bg=UI_BG, padx=10, pady=10)
        main_frame.pack(fill=tk.BOTH, expand=True)
        main_frame.grid_columnconfigure(0, weight=1)
        main_frame.grid_rowconfigure(3, weight=1)

        config_frame = tk.LabelFrame(
            main_frame,
            text="配置",
            bg=UI_PANEL_BG,
            fg=UI_FG,
            padx=10,
            pady=10,
            relief=tk.GROOVE,
            borderwidth=1,
        )
        config_frame.grid(row=0, column=0, sticky=tk.EW, pady=(0, 8))
        config_frame.grid_columnconfigure(1, weight=1, minsize=260)
        config_frame.grid_columnconfigure(3, weight=1, minsize=260)

        def add_label(row, column, text):
            tk_label(config_frame, text=text, bg=UI_PANEL_BG).grid(
                row=row,
                column=column,
                sticky=tk.W,
                padx=(0, 6),
                pady=3,
            )

        def add_field(widget, row, column, columnspan=1, sticky=tk.EW):
            widget.grid(
                row=row,
                column=column,
                columnspan=columnspan,
                sticky=sticky,
                padx=(0, 14),
                pady=3,
            )

        add_label(0, 0, "邮箱服务商:")
        self.email_provider_var = tk.StringVar(value=config.get("email_provider", "duckmail"))
        self.email_provider_combo = tk_option_menu(config_frame, self.email_provider_var, ["duckmail", "yyds", "cloudflare"], width=12)
        add_field(self.email_provider_combo, 0, 1, sticky=tk.W)

        add_label(0, 2, "注册数量:")
        self.count_var = tk.StringVar(value=str(config.get("register_count", 1)))
        self.count_spinbox = tk.Spinbox(
            config_frame,
            from_=1,
            to=2500,
            width=8,
            textvariable=self.count_var,
            bg=UI_ENTRY_BG,
            fg=UI_FG,
            insertbackground=UI_FG,
            buttonbackground=UI_BUTTON_BG,
            disabledbackground="#2f2f2f",
            disabledforeground=UI_MUTED_FG,
            relief=tk.SOLID,
        )
        add_field(self.count_spinbox, 0, 3, sticky=tk.W)

        add_label(1, 0, "注册选项:")
        self.nsfw_var = tk.BooleanVar(value=config.get("enable_nsfw", True))
        self.cf_auto_click_var = tk.BooleanVar(value=bool(config.get("cf_auto_click", True)))
        self.keep_cf_cookies_var = tk.BooleanVar(value=bool(config.get("keep_cf_cookies", True)))
        opt_box = tk.Frame(config_frame, bg=UI_PANEL_BG)
        self.nsfw_check = tk_checkbutton(opt_box, text="注册后开启 NSFW", variable=self.nsfw_var)
        self.nsfw_check.pack(side=tk.LEFT)
        self.cf_auto_click_check = tk_checkbutton(opt_box, text="自动点击人机验证", variable=self.cf_auto_click_var)
        self.cf_auto_click_check.pack(side=tk.LEFT, padx=(10, 0))
        self.keep_cf_cookies_check = tk_checkbutton(opt_box, text="保留CF Cookie", variable=self.keep_cf_cookies_var)
        self.keep_cf_cookies_check.pack(side=tk.LEFT, padx=(10, 0))
        add_field(opt_box, 1, 1, sticky=tk.W)

        add_label(1, 2, "代理（可选）:")
        self.proxy_var = tk.StringVar(value=config.get("proxy", ""))
        self.proxy_entry = tk_entry(config_frame, textvariable=self.proxy_var, width=34)
        add_field(self.proxy_entry, 1, 3)

        add_label(2, 0, "DuckMail API Key:")
        self.api_key_var = tk.StringVar(value=config.get("duckmail_api_key", ""))
        self.api_key_entry = tk_entry(config_frame, textvariable=self.api_key_var, width=34)
        add_field(self.api_key_entry, 2, 1)

        add_label(2, 2, "Cloudflare 鉴权模式:")
        self.cloudflare_auth_mode_var = tk.StringVar(value=config.get("cloudflare_auth_mode", "none"))
        self.cloudflare_auth_mode_combo = tk_option_menu(
            config_frame, self.cloudflare_auth_mode_var, ["query-key", "bearer", "x-api-key", "x-admin-auth", "none"], width=12
        )
        add_field(self.cloudflare_auth_mode_combo, 2, 3, sticky=tk.W)

        add_label(3, 0, "Cloudflare API Base:")
        self.cloudflare_api_base_var = tk.StringVar(value=config.get("cloudflare_api_base", ""))
        self.cloudflare_api_base_entry = tk_entry(config_frame, textvariable=self.cloudflare_api_base_var, width=72)
        add_field(self.cloudflare_api_base_entry, 3, 1, columnspan=3)

        add_label(4, 0, "Cloudflare API Key:")
        self.cloudflare_api_key_var = tk.StringVar(value=config.get("cloudflare_api_key", ""))
        self.cloudflare_api_key_entry = tk_entry(config_frame, textvariable=self.cloudflare_api_key_var, width=34)
        add_field(self.cloudflare_api_key_entry, 4, 1)

        add_label(4, 2, "CF 路径:")
        self.cloudflare_paths_var = tk.StringVar(
            value=",".join(
                [
                    config.get("cloudflare_path_domains", "/api/domains"),
                    config.get("cloudflare_path_accounts", "/api/new_address"),
                    config.get("cloudflare_path_token", "/api/token"),
                    config.get("cloudflare_path_messages", "/api/mails"),
                ]
            )
        )
        self.cloudflare_paths_entry = tk_entry(config_frame, textvariable=self.cloudflare_paths_var, width=34)
        add_field(self.cloudflare_paths_entry, 4, 3)

        add_label(5, 0, "grok2api 本地入池:")
        self.grok2api_local_auto_var = tk.BooleanVar(value=bool(config.get("grok2api_auto_add_local", True)))
        self.grok2api_local_auto_check = tk_checkbutton(config_frame, variable=self.grok2api_local_auto_var)
        add_field(self.grok2api_local_auto_check, 5, 1, sticky=tk.W)

        add_label(5, 2, "grok2api 池名:")
        self.grok2api_pool_name_var = tk.StringVar(value=str(config.get("grok2api_pool_name", "ssoBasic")))
        self.grok2api_pool_name_combo = tk_option_menu(
            config_frame, self.grok2api_pool_name_var, ["ssoBasic", "ssoSuper"], width=12
        )
        add_field(self.grok2api_pool_name_combo, 5, 3, sticky=tk.W)

        add_label(6, 0, "本地 token.json:")
        self.grok2api_local_file_var = tk.StringVar(value=str(config.get("grok2api_local_token_file", "")))
        self.grok2api_local_file_entry = tk_entry(config_frame, textvariable=self.grok2api_local_file_var, width=72)
        add_field(self.grok2api_local_file_entry, 6, 1, columnspan=3)

        add_label(7, 0, "grok2api 远端入池:")
        self.grok2api_remote_auto_var = tk.BooleanVar(value=bool(config.get("grok2api_auto_add_remote", False)))
        self.grok2api_remote_auto_check = tk_checkbutton(config_frame, variable=self.grok2api_remote_auto_var)
        add_field(self.grok2api_remote_auto_check, 7, 1, sticky=tk.W)

        add_label(8, 0, "grok2api 远端 Base:")
        self.grok2api_remote_base_var = tk.StringVar(value=str(config.get("grok2api_remote_base", "")))
        self.grok2api_remote_base_entry = tk_entry(config_frame, textvariable=self.grok2api_remote_base_var, width=72)
        add_field(self.grok2api_remote_base_entry, 8, 1, columnspan=3)

        add_label(9, 0, "grok2api 远端 app_key:")
        self.grok2api_remote_key_var = tk.StringVar(value=str(config.get("grok2api_remote_app_key", "")))
        self.grok2api_remote_key_entry = tk_entry(config_frame, textvariable=self.grok2api_remote_key_var, width=72)
        add_field(self.grok2api_remote_key_entry, 9, 1, columnspan=3)

        btn_frame = tk.Frame(main_frame, bg=UI_BG)
        btn_frame.grid(row=1, column=0, sticky=tk.EW, pady=(0, 6))
        self.start_btn = tk_button(btn_frame, text="开始注册", command=self.start_registration)
        self.start_btn.pack(side=tk.LEFT, padx=5)
        self.stop_btn = tk_button(btn_frame, text="停止", command=self.stop_registration, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=5)
        self.clear_btn = tk_button(btn_frame, text="清空日志", command=self.clear_log)
        self.clear_btn.pack(side=tk.LEFT, padx=5)

        status_frame = tk.Frame(main_frame, bg=UI_BG)
        status_frame.grid(row=2, column=0, sticky=tk.EW, pady=(0, 6))
        self.status_var = tk.StringVar(value="就绪")
        tk_label(status_frame, text="状态: ").pack(side=tk.LEFT)
        self.status_label = tk.Label(status_frame, textvariable=self.status_var, bg=UI_BG, fg="green")
        self.status_label.pack(side=tk.LEFT)
        self.stats_var = tk.StringVar(value="成功: 0 | 失败: 0")
        tk.Label(status_frame, textvariable=self.stats_var, bg=UI_BG, fg=UI_FG).pack(side=tk.RIGHT)
        log_frame = tk.LabelFrame(
            main_frame,
            text="日志",
            bg=UI_PANEL_BG,
            fg=UI_FG,
            padx=5,
            pady=5,
            relief=tk.GROOVE,
            borderwidth=1,
        )
        log_frame.grid(row=3, column=0, sticky=tk.NSEW)
        log_frame.grid_columnconfigure(0, weight=1)
        log_frame.grid_rowconfigure(0, weight=1)
        self.log_text = scrolledtext.ScrolledText(
            log_frame,
            height=18,
            width=60,
            bg="#111111",
            fg="#f5f5f5",
            insertbackground="#f5f5f5",
            selectbackground="#345a8a",
            selectforeground="#ffffff",
            relief=tk.SOLID,
            borderwidth=1,
            highlightthickness=1,
            highlightbackground="#555555",
        )
        self.log_text.grid(row=0, column=0, sticky=tk.NSEW)
        self.log("[*] GUI 已就绪，配置已加载")
        self.log(f"[*] 当前邮箱服务商: {self.email_provider_var.get()} | 注册数量: {self.count_var.get()}")

    def log(self, message):
        if not should_emit_log(message):
            return
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}"
        print(line, flush=True)
        try:
            self.log_text.insert(tk.END, f"{line}\n")
            # 防止长时间运行日志区无限增长导致卡顿
            try:
                line_count = int(float(str(self.log_text.index("end-1c").split(".")[0])))
                if line_count > 5000:
                    self.log_text.delete("1.0", f"{line_count - 4000}.0")
            except Exception:
                pass
            self.log_text.see(tk.END)
        except Exception:
            pass

    def clear_log(self):
        self.log_text.delete(1.0, tk.END)

    def update_stats(self):
        self.stats_var.set(f"成功: {self.success_count} | 失败: {self.fail_count}")

    def _set_running_ui(self, running):
        self.is_running = running
        self.start_btn.config(state=tk.DISABLED if running else tk.NORMAL)
        self.stop_btn.config(state=tk.NORMAL if running else tk.DISABLED)
        self.status_var.set("运行中..." if running else "就绪")
        self.status_label.config(foreground="blue" if running else "green")

    def should_stop(self):
        return self.stop_requested or not self.is_running

    def start_registration(self):
        if self.is_running:
            self.log("[!] 当前已有任务在运行")
            return

        config["email_provider"] = self.email_provider_var.get().strip() or "duckmail"
        config["enable_nsfw"] = bool(self.nsfw_var.get())
        config["cf_auto_click"] = bool(self.cf_auto_click_var.get())
        config["keep_cf_cookies"] = bool(self.keep_cf_cookies_var.get())
        config["proxy"] = self.proxy_var.get().strip()
        config["duckmail_api_key"] = self.api_key_var.get().strip()
        config["cloudflare_api_base"] = self.cloudflare_api_base_var.get().strip()
        config["cloudflare_api_key"] = self.cloudflare_api_key_var.get().strip()
        config["cloudflare_auth_mode"] = self.cloudflare_auth_mode_var.get().strip() or "none"
        config["grok2api_auto_add_local"] = bool(self.grok2api_local_auto_var.get())
        config["grok2api_local_token_file"] = self.grok2api_local_file_var.get().strip()
        config["grok2api_pool_name"] = self.grok2api_pool_name_var.get().strip() or "ssoBasic"
        config["grok2api_auto_add_remote"] = bool(self.grok2api_remote_auto_var.get())
        config["grok2api_remote_base"] = self.grok2api_remote_base_var.get().strip()
        config["grok2api_remote_app_key"] = self.grok2api_remote_key_var.get().strip()
        raw_paths = [x.strip() for x in self.cloudflare_paths_var.get().split(",") if x.strip()]
        if len(raw_paths) >= 4:
            config["cloudflare_path_domains"] = raw_paths[0] if raw_paths[0].startswith("/") else ("/" + raw_paths[0])
            config["cloudflare_path_accounts"] = raw_paths[1] if raw_paths[1].startswith("/") else ("/" + raw_paths[1])
            config["cloudflare_path_token"] = raw_paths[2] if raw_paths[2].startswith("/") else ("/" + raw_paths[2])
            config["cloudflare_path_messages"] = raw_paths[3] if raw_paths[3].startswith("/") else ("/" + raw_paths[3])
        save_config()
        if config["email_provider"] == "cloudflare" and not config["cloudflare_api_base"]:
            self.log("[!] Cloudflare 模式需要先填写 Cloudflare API Base")
            return
        try:
            count = int(self.count_var.get())
        except Exception:
            self.log("[!] 注册数量无效")
            return
        config["register_count"] = count
        save_config()
        self.stop_requested = False
        self.success_count = 0
        self.fail_count = 0
        self.results = []
        now = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.accounts_output_file = os.path.join(
            os.path.dirname(__file__), f"accounts_{now}.txt"
        )
        self.update_stats()
        self._set_running_ui(True)
        self.log(f"[*] 配置已保存，开始执行。目标数量: {count}")
        self.log(f"[*] 成功账号将实时保存到: {self.accounts_output_file}")
        threading.Thread(
            target=self.run_registration,
            args=(count,),
            daemon=True,
        ).start()

    def stop_registration(self):
        self.stop_requested = True
        self.log("[!] 用户停止注册")

    def run_registration(self, count):
        stop_speed = threading.Event()
        interval = float(config.get("speed_log_interval_sec", 60) or 60)
        def _gui_counts():
            with _stats_lock:
                return self.success_count, self.fail_count

        speed_thread, _meter = start_speed_logger(
            get_counts=_gui_counts,
            log_callback=self.log,
            stop_event=stop_speed,
            interval_sec=interval,
        )
        try:
            concurrent = max(1, int(config.get("concurrent_count", 1) or 1))
            self.log(f"[*] 日志级别: {get_log_level()} | 速度统计间隔: {int(interval)}s")
            if concurrent <= 1:
                self._run_single_worker(count, worker_id=0)
            else:
                self._run_concurrent_workers(count, concurrent)
        except Exception as exc:
            self.log(f"[!] 任务异常: {exc}")
        finally:
            stop_speed.set()
            try:
                speed_thread.join(timeout=2)
            except Exception:
                pass
            _wait_cpa_async_threads(
                timeout=5 if self.should_stop() else 300,
                log_callback=self.log,
                skip_if_stopping=self.should_stop,
            )
            self._set_running_ui(False)
            self.log(
                f"[*] 任务结束。成功 {self.success_count} | 失败 {self.fail_count}"
            )

    def _run_concurrent_workers(self, total_count, worker_count):
        import queue
        task_queue = queue.Queue()
        for idx in range(total_count):
            task_queue.put(idx)
        threads = []
        for wid in range(worker_count):
            if self.should_stop():
                break
            t = threading.Thread(
                target=self._worker_loop,
                args=(wid, task_queue, total_count),
                daemon=True,
            )
            t.start()
            threads.append(t)
            sleep_with_cancel(2, self.should_stop)
        _join_threads_interruptible(
            threads,
            should_stop=self.should_stop,
            timeout=None,
            poll=0.5,
        )
        if self.should_stop():
            _join_threads_interruptible(threads, should_stop=None, timeout=5, poll=0.5)

    def _worker_loop(self, worker_id, task_queue, total_count):
        _set_worker_id(worker_id)
        prefix = f"[W{worker_id}]"
        log_fn = lambda msg: self.log(f"{prefix} {msg}")
        try:
            start_browser(log_callback=log_fn)
            log_fn(f"[*] Worker-{worker_id} 浏览器已启动")
        except Exception as e:
            log_fn(f"[!] Worker-{worker_id} 浏览器启动失败: {e}")
            return
        restart_every = int(config.get("browser_restart_every", 10) or 0)
        local_success = 0
        local_attempts = 0
        max_slot_retry = 3
        try:
            while not self.should_stop():
                try:
                    task_queue.get_nowait()
                except Exception:
                    break
                slot_done = False
                retry_count_for_slot = 0
                while not slot_done and not self.should_stop():
                    try:
                        self._register_one_account(log_fn, worker_id, local_success)
                        local_success += 1
                        slot_done = True
                    except RegistrationCancelled:
                        return
                    except AccountRetryNeeded as exc:
                        retry_count_for_slot += 1
                        if retry_count_for_slot <= max_slot_retry:
                            log_fn(
                                f"[!] 账号流程卡住，重试第 {retry_count_for_slot}/{max_slot_retry} 次: {exc}"
                            )
                            restart_browser(log_callback=log_fn)
                            continue
                        with _stats_lock:
                            self.fail_count += 1
                        log_fn(f"[-] 当前账号已达到最大重试次数，跳过: {exc}")
                        slot_done = True
                    except Exception as exc:
                        with _stats_lock:
                            self.fail_count += 1
                        log_fn(f"[-] 注册失败: {exc}")
                        slot_done = True
                    finally:
                        local_attempts += 1
                        self.update_stats()
                        if self.should_stop():
                            break
                        # 与稳定版/单 worker 一致：每账号完整重启，避免 SSO/TOS 会话残留落到 tos-gate
                        if _get_browser() is None:
                            start_browser(log_callback=log_fn)
                        else:
                            if restart_every > 0 and local_attempts % restart_every == 0:
                                log_fn(
                                    f"[*] Worker-{worker_id} 已处理 {local_attempts} 个账号，周期重启浏览器"
                                )
                            restart_browser(log_callback=log_fn)
                        sleep_with_cancel(1, self.should_stop)
        finally:
            stop_browser()

    def _register_one_account(self, log_fn, worker_id=0, local_success=0):
        email = ""
        dev_token = ""
        code = ""
        mail_ok = False
        max_mail_retry = 3
        for mail_try in range(1, max_mail_retry + 1):
            log_fn(f"[*] 1. 打开注册页 (尝试 {mail_try}/{max_mail_retry})")
            open_signup_page(log_callback=log_fn, cancel_callback=self.should_stop)
            log_fn("[*] 2. 创建邮箱并提交")
            email, dev_token = fill_email_and_submit(
                log_callback=log_fn, cancel_callback=self.should_stop
            )
            log_fn(f"[*] 邮箱: {email}")
            try:
                with _io_lock:
                    with open(
                        os.path.join(os.path.dirname(__file__), "mail_credentials.txt"),
                        "a", encoding="utf-8",
                    ) as f:
                        f.write(f"{email}\t{dev_token}\n")
            except Exception:
                pass
            log_fn("[*] 3. 拉取验证码")
            try:
                code = fill_code_and_submit(
                    email, dev_token,
                    log_callback=log_fn, cancel_callback=self.should_stop,
                )
                mail_ok = True
                break
            except Exception as mail_exc:
                msg = str(mail_exc)
                if ("未收到验证码" in msg or "验证码" in msg) and mail_try < max_mail_retry:
                    log_fn(f"[!] 本邮箱未取到验证码，自动更换新邮箱重试: {msg}")
                    restart_browser(log_callback=log_fn)
                    sleep_with_cancel(1, self.should_stop)
                    continue
                raise
        if not mail_ok:
            raise Exception("验证码阶段失败，已达到最大重试次数")
        log_fn(f"[*] 验证码: {code}")
        log_fn("[*] 4. 填写资料")
        profile = fill_profile_and_submit(
            log_callback=log_fn, cancel_callback=self.should_stop
        )
        log_fn(f"[*] 资料已填: {profile.get('given_name')} {profile.get('family_name')}")
        log_fn("[*] 5. 等待 sso cookie")
        sso = wait_for_sso_cookie(
            log_callback=log_fn, cancel_callback=self.should_stop
        )
        _cpa_page = _get_page()
        if config.get("cpa_export_enabled", True):
            cpa_async = bool(config.get("cpa_mint_async", True))
            if cpa_async:
                log_fn("[*] 6. CPA xAI 导出 (异步)")
                _cpa_bg_page = None
                def _cpa_mint_bg():
                    time.sleep(5)
                    try:
                        r = export_cpa_xai_for_account(
                            email, profile.get("password", ""), sso=sso,
                            log_callback=log_fn, page=_cpa_bg_page,
                        )
                        if r.get("ok"):
                            log_fn(f"[+] CPA xAI 导出成功: {r.get('path', '')}")
                        elif not r.get("skipped"):
                            log_fn(f"[!] CPA xAI 导出失败: {r.get('error', '未知错误')}")
                    except Exception as e:
                        log_fn(f"[!] CPA xAI 导出异常: {e}")
                _t = threading.Thread(target=_cpa_mint_bg, daemon=True)
                _t.start()
                _track_cpa_async_thread(_t)
            else:
                log_fn("[*] 6. CPA xAI 导出 (同步)")
                cpa_result = export_cpa_xai_for_account(
                    email, profile.get("password", ""), sso=sso,
                    log_callback=log_fn, page=_cpa_page,
                )
                if cpa_result.get("ok"):
                    log_fn(f"[+] CPA xAI 导出成功: {cpa_result.get('path', '')}")
                elif not cpa_result.get("skipped"):
                    log_fn(f"[!] CPA xAI 导出失败: {cpa_result.get('error', '未知错误')}")
        if config.get("enable_nsfw", True):
            log_fn("[*] 6. 开启 NSFW")
            nsfw_ok, nsfw_msg = enable_nsfw_for_token(sso, log_callback=log_fn)
            if nsfw_ok:
                log_fn(f"[+] NSFW 开启成功: {nsfw_msg}")
            else:
                log_fn(f"[!] NSFW 未开启，继续保存账号: {nsfw_msg}")
        with _stats_lock:
            self.results.append({"email": email, "sso": sso, "profile": profile})
        try:
            line = f"{email}----{profile.get('password','')}----{sso}\n"
            with _io_lock:
                with open(self.accounts_output_file, "a", encoding="utf-8") as f:
                    f.write(line)
        except Exception as file_exc:
            log_fn(f"[Debug] 保存账号文件失败: {file_exc}")
        add_token_to_grok2api_pools(sso, email=email, log_callback=log_fn)
        add_token_to_token_only_file(sso, log_callback=log_fn)
        with _stats_lock:
            self.success_count += 1
        log_fn(f"[+] 注册成功: {email}")

    def _run_single_worker(self, count, worker_id=0):
        _set_worker_id(worker_id)
        start_browser(log_callback=self.log)
        self.log("[*] 浏览器已启动")
        restart_every = int(config.get("browser_restart_every", 10) or 0)
        i = 0
        retry_count_for_slot = 0
        max_slot_retry = 3
        while i < count:
            if self.should_stop():
                break
            self.log(f"--- 开始第 {i + 1}/{count} 个账号 ---")
            try:
                self._register_one_account(self.log, worker_id, i)
                retry_count_for_slot = 0
                i += 1
                if restart_every > 0 and i > 0 and i % restart_every == 0:
                    self.log(f"[*] 已注册 {i} 个账号，重启浏览器")
                    restart_browser(log_callback=self.log)
                if (
                    self.success_count > 0
                    and self.success_count % MEMORY_CLEANUP_INTERVAL == 0
                    and i < count
                ):
                    cleanup_runtime_memory(
                        log_callback=self.log,
                        reason=f"已成功 {self.success_count} 个账号，执行定期清理",
                    )
            except RegistrationCancelled:
                self.log("[!] 注册被用户停止")
                break
            except AccountRetryNeeded as exc:
                retry_count_for_slot += 1
                if retry_count_for_slot <= max_slot_retry:
                    self.log(f"[!] 当前账号流程卡住，重试第 {retry_count_for_slot}/{max_slot_retry} 次: {exc}")
                else:
                    with _stats_lock:
                        self.fail_count += 1
                    self.log(f"[-] 当前账号已达到最大重试次数，跳过: {exc}")
                    retry_count_for_slot = 0
                    i += 1
            except Exception as exc:
                with _stats_lock:
                    self.fail_count += 1
                retry_count_for_slot = 0
                i += 1
                self.log(f"[-] 注册失败: {exc}")
            finally:
                self.update_stats()
                if self.should_stop():
                    break
                if _get_browser() is None:
                    start_browser(log_callback=self.log)
                else:
                    restart_browser(log_callback=self.log)
                sleep_with_cancel(1, self.should_stop)
        stop_browser()


class CliStopController:
    def __init__(self):
        self.stop_requested = False
        self._sigint_count = 0
        self._lock = threading.Lock()

    def should_stop(self):
        return self.stop_requested

    def stop(self):
        with self._lock:
            self.stop_requested = True

    def handle_sigint(self, signum=None, frame=None):
        """第一次 Ctrl+C 请求优雅停止；第二次强制退出。"""
        with self._lock:
            self._sigint_count += 1
            count = self._sigint_count
            self.stop_requested = True
        if count == 1:
            cli_log("[!] 收到 Ctrl+C，正在停止...（再按一次强制退出）")
            return
        cli_log("[!] 再次收到 Ctrl+C，强制退出")
        try:
            os._exit(1)
        except Exception:
            raise SystemExit(1)


def cli_log(message):
    if not should_emit_log(message):
        return
    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def _install_cli_sigint_handler(controller):
    """安装可重入的 Ctrl+C 处理。Windows/Git Bash 下尽量可用。"""
    previous = None
    try:
        import signal

        previous = signal.getsignal(signal.SIGINT)

        def _handler(signum, frame):
            controller.handle_sigint(signum, frame)

        signal.signal(signal.SIGINT, _handler)
        return previous
    except Exception:
        return previous


def _restore_sigint_handler(previous):
    try:
        import signal

        if previous is not None:
            signal.signal(signal.SIGINT, previous)
    except Exception:
        pass


def _register_one_account_cli(log_fn, stop_fn, accounts_output_file):
    email = ""
    dev_token = ""
    code = ""
    mail_ok = False
    max_mail_retry = 3
    for mail_try in range(1, max_mail_retry + 1):
        log_fn(f"[*] 1. 打开注册页 (尝试 {mail_try}/{max_mail_retry})")
        open_signup_page(log_callback=log_fn, cancel_callback=stop_fn)
        log_fn("[*] 2. 创建邮箱并提交")
        email, dev_token = fill_email_and_submit(
            log_callback=log_fn, cancel_callback=stop_fn
        )
        log_fn(f"[*] 邮箱: {email}")
        try:
            with _io_lock:
                with open(
                    os.path.join(os.path.dirname(__file__), "mail_credentials.txt"),
                    "a", encoding="utf-8",
                ) as f:
                    f.write(f"{email}\t{dev_token}\n")
        except Exception:
            pass
        log_fn("[*] 3. 拉取验证码")
        try:
            code = fill_code_and_submit(
                email, dev_token,
                log_callback=log_fn, cancel_callback=stop_fn,
            )
            mail_ok = True
            break
        except Exception as mail_exc:
            msg = str(mail_exc)
            if ("未收到验证码" in msg or "验证码" in msg) and mail_try < max_mail_retry:
                log_fn(f"[!] 本邮箱未取到验证码，自动更换新邮箱重试: {msg}")
                restart_browser(log_callback=log_fn)
                sleep_with_cancel(1, stop_fn)
                continue
            raise
    if not mail_ok:
        raise Exception("验证码阶段失败，已达到最大重试次数")
    log_fn(f"[*] 验证码: {code}")
    log_fn("[*] 4. 填写资料")
    profile = fill_profile_and_submit(
        log_callback=log_fn, cancel_callback=stop_fn
    )
    log_fn(f"[*] 资料已填: {profile.get('given_name')} {profile.get('family_name')}")
    log_fn("[*] 5. 等待 sso cookie")
    sso = wait_for_sso_cookie(
        log_callback=log_fn, cancel_callback=stop_fn
    )
    _cpa_page = _get_page()
    if config.get("cpa_export_enabled", True):
        cpa_async = bool(config.get("cpa_mint_async", True))
        if cpa_async:
            log_fn("[*] 6. CPA xAI 导出 (异步)")
            _cpa_bg_page = None
            def _cpa_mint_bg():
                time.sleep(5)
                try:
                    r = export_cpa_xai_for_account(
                        email, profile.get("password", ""), sso=sso,
                        log_callback=log_fn, page=_cpa_bg_page,
                    )
                    if r.get("ok"):
                        log_fn(f"[+] CPA xAI 导出成功: {r.get('path', '')}")
                    elif not r.get("skipped"):
                        log_fn(f"[!] CPA xAI 导出失败: {r.get('error', '未知错误')}")
                except Exception as e:
                    log_fn(f"[!] CPA xAI 导出异常: {e}")
            _t = threading.Thread(target=_cpa_mint_bg, daemon=True)
            _t.start()
            _track_cpa_async_thread(_t)
        else:
            log_fn("[*] 6. CPA xAI 导出 (同步)")
            cpa_result = export_cpa_xai_for_account(
                email, profile.get("password", ""), sso=sso,
                log_callback=log_fn, page=_cpa_page,
            )
            if cpa_result.get("ok"):
                log_fn(f"[+] CPA xAI 导出成功: {cpa_result.get('path', '')}")
            elif not cpa_result.get("skipped"):
                log_fn(f"[!] CPA xAI 导出失败: {cpa_result.get('error', '未知错误')}")
    if config.get("enable_nsfw", True):
        log_fn("[*] 6. 开启 NSFW")
        nsfw_ok, nsfw_msg = enable_nsfw_for_token(sso, log_callback=log_fn)
        if nsfw_ok:
            log_fn(f"[+] NSFW 开启成功: {nsfw_msg}")
        else:
            log_fn(f"[!] NSFW 未开启，继续保存账号: {nsfw_msg}")
    try:
        line = f"{email}----{profile.get('password','')}----{sso}\n"
        with _io_lock:
            with open(accounts_output_file, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception as file_exc:
        log_fn(f"[Debug] 保存账号文件失败: {file_exc}")
    add_token_to_grok2api_pools(sso, email=email, log_callback=log_fn)
    add_token_to_token_only_file(sso, log_callback=log_fn)
    log_fn(f"[+] 注册成功: {email}")


def _cli_worker_loop(worker_id, task_queue, total_count, controller, accounts_output_file, stats):
    _set_worker_id(worker_id)
    prefix = f"[W{worker_id}]"
    log_fn = lambda msg: cli_log(f"{prefix} {msg}")
    try:
        start_browser(log_callback=log_fn)
        log_fn(f"[*] Worker-{worker_id} 浏览器已启动")
    except Exception as e:
        log_fn(f"[!] Worker-{worker_id} 浏览器启动失败: {e}")
        return
    restart_every = int(config.get("browser_restart_every", 10) or 0)
    local_success = 0
    local_attempts = 0
    max_slot_retry = 3
    try:
        while not controller.should_stop():
            try:
                task_queue.get_nowait()
            except Exception:
                break
            slot_done = False
            retry_count_for_slot = 0
            while not slot_done and not controller.should_stop():
                try:
                    _register_one_account_cli(log_fn, controller.should_stop, accounts_output_file)
                    with stats["lock"]:
                        stats["success"] += 1
                        local_success += 1
                    slot_done = True
                except RegistrationCancelled:
                    return
                except AccountRetryNeeded as exc:
                    retry_count_for_slot += 1
                    if retry_count_for_slot <= max_slot_retry:
                        log_fn(
                            f"[!] 账号流程卡住，重试第 {retry_count_for_slot}/{max_slot_retry} 次: {exc}"
                        )
                        restart_browser(log_callback=log_fn)
                        continue
                    with stats["lock"]:
                        stats["fail"] += 1
                    log_fn(f"[-] 当前账号已达到最大重试次数，跳过: {exc}")
                    slot_done = True
                except Exception as exc:
                    with stats["lock"]:
                        stats["fail"] += 1
                    log_fn(f"[-] 注册失败: {exc}")
                    slot_done = True
                finally:
                    local_attempts += 1
                    if controller.should_stop():
                        break
                    # 与稳定版/单 worker 一致：每账号完整重启，避免 SSO/TOS 会话残留落到 tos-gate
                    if _get_browser() is None:
                        start_browser(log_callback=log_fn)
                    else:
                        if restart_every > 0 and local_attempts % restart_every == 0:
                            log_fn(
                                f"[*] Worker-{worker_id} 已处理 {local_attempts} 个账号，周期重启浏览器"
                            )
                        restart_browser(log_callback=log_fn)
                    sleep_with_cancel(1, controller.should_stop)
    finally:
        stop_browser()


def run_registration_cli(count):
    controller = CliStopController()
    prev_handler = _install_cli_sigint_handler(controller)
    accounts_output_file = os.path.join(
        os.path.dirname(__file__),
        f"accounts_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
    )
    worker_count = max(1, int(config.get("concurrent_count", 1) or 1))
    stats = {"success": 0, "fail": 0, "lock": threading.Lock()}
    stop_speed = threading.Event()
    interval = float(config.get("speed_log_interval_sec", 60) or 60)

    def _cli_counts():
        with stats["lock"]:
            return stats["success"], stats["fail"]

    speed_thread, _meter = start_speed_logger(
        get_counts=_cli_counts,
        log_callback=cli_log,
        stop_event=stop_speed,
        interval_sec=interval,
    )
    cli_log(f"[*] 终端模式启动，目标数量: {count}，并发: {worker_count}")
    cli_log(f"[*] 成功账号将实时保存到: {accounts_output_file}")
    cli_log(f"[*] 日志级别: {get_log_level()} | 速度统计间隔: {int(interval)}s")
    cli_log("[*] 按 Ctrl+C 停止（连按两次强制退出）")
    try:
        if worker_count > 1:
            import queue
            task_queue = queue.Queue()
            for idx in range(count):
                task_queue.put(idx)
            threads = []
            for wid in range(worker_count):
                if controller.should_stop():
                    break
                t = threading.Thread(
                    target=_cli_worker_loop,
                    args=(wid, task_queue, count, controller, accounts_output_file, stats),
                    daemon=True,
                )
                t.start()
                threads.append(t)
                # 可中断的启动间隔
                sleep_with_cancel(2, controller.should_stop)
            _join_threads_interruptible(
                threads,
                should_stop=controller.should_stop,
                timeout=None,
                poll=0.5,
            )
            if controller.should_stop():
                cli_log("[!] 已请求停止，等待 worker 收尾...")
                _join_threads_interruptible(
                    threads,
                    should_stop=None,
                    timeout=5,
                    poll=0.5,
                )
        else:
            start_browser(log_callback=cli_log)
            cli_log("[*] 浏览器已启动")
            restart_every = int(config.get("browser_restart_every", 10) or 0)
            i = 0
            retry_count_for_slot = 0
            max_slot_retry = 3
            while i < count:
                if controller.should_stop():
                    break
                cli_log(f"--- 开始第 {i + 1}/{count} 个账号 ---")
                try:
                    _register_one_account_cli(cli_log, controller.should_stop, accounts_output_file)
                    with stats["lock"]:
                        stats["success"] += 1
                    retry_count_for_slot = 0
                    i += 1
                    cli_log(f"[*] 当前统计: 成功 {stats['success']} | 失败 {stats['fail']}")
                    if restart_every > 0 and i > 0 and i % restart_every == 0:
                        cli_log(f"[*] 已注册 {i} 个账号，重启浏览器")
                        restart_browser(log_callback=cli_log)
                    if (
                        stats["success"] > 0
                        and stats["success"] % MEMORY_CLEANUP_INTERVAL == 0
                        and i < count
                    ):
                        cleanup_runtime_memory(
                            log_callback=cli_log,
                            reason=f"已成功 {stats['success']} 个账号，执行定期清理",
                        )
                except RegistrationCancelled:
                    cli_log("[!] 注册被停止")
                    break
                except AccountRetryNeeded as exc:
                    retry_count_for_slot += 1
                    if retry_count_for_slot <= max_slot_retry:
                        cli_log(
                            f"[!] 当前账号流程卡住，重试第 {retry_count_for_slot}/{max_slot_retry} 次: {exc}"
                        )
                    else:
                        with stats["lock"]:
                            stats["fail"] += 1
                        retry_count_for_slot = 0
                        i += 1
                        cli_log(f"[-] 当前账号已达到最大重试次数，跳过: {exc}")
                except Exception as exc:
                    with stats["lock"]:
                        stats["fail"] += 1
                    retry_count_for_slot = 0
                    i += 1
                    cli_log(f"[-] 注册失败: {exc}")
                finally:
                    if controller.should_stop():
                        break
                    if _get_browser() is None:
                        start_browser(log_callback=cli_log)
                    else:
                        restart_browser(log_callback=cli_log)
                    sleep_with_cancel(1, controller.should_stop)
    except KeyboardInterrupt:
        controller.stop()
        cli_log("[!] 收到 KeyboardInterrupt，正在停止并清理")
    except Exception as exc:
        cli_log(f"[!] 任务异常: {exc}")
    finally:
        stop_speed.set()
        try:
            speed_thread.join(timeout=2)
        except Exception:
            pass
        stopping = controller.should_stop()
        controller.stop()
        _wait_cpa_async_threads(
            timeout=5 if stopping else 300,
            log_callback=cli_log,
            skip_if_stopping=(lambda: stopping),
        )
        try:
            cleanup_runtime_memory(log_callback=cli_log, reason="任务结束")
        except Exception as clean_exc:
            cli_log(f"[Debug] 结束清理异常: {clean_exc}")
        _restore_sigint_handler(prev_handler)
        with stats["lock"]:
            ok, bad = stats["success"], stats["fail"]
        cli_log(f"[*] 任务结束。成功 {ok} | 失败 {bad}")


def main_cli():
    load_config()
    count = int(config.get("register_count", 1) or 1)
    cli_log("[*] CLI 已加载配置")
    cli_log(f"[*] 当前邮箱服务商: {config.get('email_provider', 'duckmail')} | 注册数量: {count}")
    cli_log("[*] 输入 start 后开始；按 Ctrl+C 可强制停止")
    try:
        command = input("> ").strip().lower()
    except KeyboardInterrupt:
        cli_log("[!] 已取消")
        return
    if command != "start":
        cli_log("[!] 未输入 start，已退出")
        return
    run_registration_cli(count)


def main():
    if len(sys.argv) > 1 and sys.argv[1].strip().lower() in ("start", "cli", "--cli"):
        main_cli()
        return
    root = tk.Tk()
    setup_light_theme(root)
    app = GrokRegisterGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
