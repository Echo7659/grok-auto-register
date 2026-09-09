<div align="center">

[![Grok Auto Register — multi-platform registration automation](assets/banner.png)](https://github.com/Echo7659/grok-auto-register)

**Grok Auto Register GUI** 是一个面向自动化流程研究、测试环境验证和个人学习的 Python 多平台注册工具 —— 支持 GUI / CLI、临时邮箱、真实浏览器流程、多并发 worker，以及平台本地授权导出。

<p>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License: MIT"></a>
  <img src="https://img.shields.io/badge/Python-3.9%2B-3776AB.svg" alt="Python 3.9+">
  <img src="https://img.shields.io/badge/Interface-GUI%20%2B%20CLI-success.svg" alt="GUI + CLI">
  <img src="https://img.shields.io/badge/Platforms-Grok%20%7C%20Fish%20Audio%20%7C%20ElevenLabs-success.svg" alt="Platforms">
  <img src="https://img.shields.io/badge/Browser-Chromium%2FChrome-4285F4.svg" alt="Chromium/Chrome">
</p>

</div>

---

> **二次增强说明**：本项目基于 [`maxucheng0/grok-auto-register`](https://github.com/maxucheng0/grok-auto-register) 二次增强开发，在原有 Grok 注册能力之上扩展了多平台、GUI、并发稳定性与本地授权导出等能力。感谢原作者开源。

> 本项目仅用于自动化流程研究、测试环境验证和个人学习。请遵守目标网站服务条款、当地法律法规和第三方服务限制。

## 项目优点

- **多平台统一入口**：一个程序切换 `Grok` / `Fish Audio` / `ElevenLabs`，配置、邮箱、并发、日志共用一套框架。
- **GUI + CLI 双模式**：日常调试用图形界面，长时间批量用终端模式。
- **真实浏览器链路**：基于 Chromium/Chrome 完成页面注册，兼容 Turnstile / hCaptcha 等常见前端风控场景。
- **多并发隔离**：每个 worker 独立浏览器与 profile，账号结束后完整重启，降低会话串号风险。
- **临时邮箱可插拔**：DuckMail、Mail.tm、1secMail、YYDS、Cloudflare 自有域名按需切换。
- **授权产物可落地**：Fish Audio / ElevenLabs 写出本地授权 JSON；Grok 可对接 grok2api / CPA。
- **可观测与可停机**：日志分级、每分钟速度统计、Ctrl+C 优雅停机。

## 功能

### 平台能力

| 平台 | 注册方式 | 成功产物 |
| --- | --- | --- |
| **Grok** | 邮箱验证码 + 浏览器流程 | SSO token；可选 grok2api 入池、CPA xAI 凭证 |
| **Fish Audio** | 邮箱 OTP 注册 | `fish_auths/*.json` 会话授权 |
| **ElevenLabs** | 邮箱密码注册 → 邮件验证链接 → 再登录 | `eleven_auths/*.json` **网页 Firebase ID Token**（含 `refresh_token`） |

ElevenLabs 说明：

- 保存的是网页授权：`Authorization: Bearer <access_token>`
- `access_token` / `id_token` 为 Firebase ID Token
- `refresh_token` 为 Firebase refresh token
- **不是**开发者控制台里的 `xi-api-key` / `sk_...`

### 通用能力

- GUI 图形界面与 CLI 终端运行
- 多 worker 并发（`concurrent_count`）
- 验证码 / 验证邮件轮询与解析
- 成功账号实时写入 `accounts_*.txt` / `accounts_fish_*.txt` / `accounts_eleven_*.txt`
- Cloudflare Turnstile 自动点击与 CF cookie 复用（Grok 场景）
- 日志级别：`quiet` / `info` / `debug`
- 卡住检测、当前账号重试、每账号浏览器重启、内存清理

## 环境要求

- Python 3.9+
- Google Chrome 或 Chromium
- 可访问目标注册页与临时邮箱 API 的网络环境（可配置代理）

## 安装

```bash
# 本仓库（二次增强版）
git clone https://github.com/Echo7659/grok-auto-register.git
cd grok-auto-register

# 或从上游原项目开始，再合并本仓库增强
# git clone https://github.com/maxucheng0/grok-auto-register.git
```

```bash
pip install -r requirements.txt
cp config.example.json config.json
```

按需编辑 `config.json`。该文件含个人密钥，已被 `.gitignore` 忽略。

## 快速开始

### GUI

```bash
./.venv/bin/python grok_register_ttk.py
# 或
python grok_register_ttk.py
```

界面分区：

- **基本设置**：平台、数量、并发、代理、日志；Fish / ElevenLabs 各自授权输出目录
- **邮箱服务**：DuckMail / Mail.tm / 1secMail / YYDS / Cloudflare
- **自动导入**：仅 Grok 使用（CPA / grok2api）；Fish / ElevenLabs 只写本地授权
- **高级设置**：浏览器与人机验证相关选项

**保存配置**只落盘；**开始注册**会校验、保存并启动当前批次。

### CLI

```bash
python grok_register_ttk.py cli
```

输入 `start` 开始；`Ctrl+C` 停止。

并发示例：

```json
{
  "platform": "elevenlabs",
  "register_count": 3,
  "concurrent_count": 3,
  "email_provider": "duckmail",
  "eleven_auth_dir": "eleven_auths",
  "eleven_session_ttl_sec": 3600,
  "proxy": "http://127.0.0.1:7897",
  "log_level": "info"
}
```

## 配置要点

| 配置项 | 说明 |
| --- | --- |
| `platform` | `grok` / `fishaudio` / `elevenlabs` |
| `register_count` | 目标注册数量 |
| `concurrent_count` | 并发 worker 数 |
| `email_provider` | `duckmail` / `mailtm` / `onesecmail` / `yyds` / `cloudflare` |
| `proxy_mode` | `fixed` 固定代理 / `pool` 代理池随机出口 |
| `proxy` | 固定代理（`proxy_mode=fixed` 时使用），可留空 |
| `proxy_pool_file` | 代理池文件（`proxy_mode=pool`），每行一条 |
| `fish_auth_dir` | Fish Audio 授权目录，默认 `fish_auths` |
| `eleven_auth_dir` | ElevenLabs 授权目录，默认 `eleven_auths` |
| `eleven_session_ttl_sec` | ElevenLabs ID Token 记录的有效期（秒），默认 `3600` |
| `log_level` | `quiet` / `info` / `debug` |
| `cf_auto_click` | Cloudflare Turnstile 自动点击（默认开启） |
| `keep_cf_cookies` | 账号间重启时保留 CF cookie（默认开启） |

更多邮箱、CPA、grok2api 细节见下方章节与 `config.example.json`。  
手动从浏览器提取 ElevenLabs 网页凭证：见 [`docs/elevenlabs-manual-auth.md`](docs/elevenlabs-manual-auth.md)。

## 邮箱服务

推荐优先级（目标站容易拒公开临时域时）：

1. **Cloudflare 自有域名**（最稳）
2. **Mail.tm**（免密钥兜底）
3. **DuckMail / YYDS**
4. **1secMail**（部分网络对官方接口 `403`，可换镜像）

Mail.tm 示例：

```json
{
  "email_provider": "mailtm",
  "mailtm_api_base": "https://api.mail.tm"
}
```

Cloudflare 匿名模式示例：

```json
{
  "email_provider": "cloudflare",
  "cloudflare_api_base": "https://你的-worker-api-域名",
  "cloudflare_api_key": "",
  "cloudflare_auth_mode": "none",
  "defaultDomains": "你的收信域名.com"
}
```

## 输出文件

| 文件 / 目录 | 说明 |
| --- | --- |
| `accounts_*.txt` | Grok 成功账号 |
| `accounts_fish_*.txt` | Fish Audio 成功账号 |
| `accounts_eleven_*.txt` | ElevenLabs 成功账号 |
| `fish_auths/` | Fish Audio 会话授权 JSON |
| `eleven_auths/` | ElevenLabs 网页授权 JSON（含 `access_token` + `refresh_token`） |
| `cpa_auths/` | CPA xAI 凭证（Grok） |
| `mail_credentials.txt` | 临时邮箱凭证 |

以上敏感产物均已被 gitignore。

### ElevenLabs 授权 JSON 示例

```json
{
  "type": "elevenlabs",
  "auth_kind": "web_firebase_id_token",
  "access_token": "<Firebase ID Token>",
  "refresh_token": "<Firebase Refresh Token>",
  "id_token": "<Firebase ID Token>",
  "email": "you@example.com",
  "user_id": "user_...",
  "workspace_id": "...",
  "auth_account_id": "...",
  "api_base": "https://api.us.elevenlabs.io",
  "firebase_api_key": "AIzaSy..."
}
```

调用方式（网页凭证，不是 `xi-api-key`）：

```bash
curl -s https://api.us.elevenlabs.io/v1/user \
  -H "Authorization: Bearer <access_token>" \
  -H "Origin: https://elevenlabs.io" \
  -H "Referer: https://elevenlabs.io/app/home" \
  -H "x-generation-surface: Speech Synthesis" \
  -H "x-generation-actor: User"
```

探测脚本：`scripts/verify_eleven_web_auth.py`。

## 下一步计划（待完成）

以下为当前已知、尚未闭环的工作，方便跟进：

1. **代理池落地验证**  
   将常用配置切到 `proxy_mode=pool`，把 Decodo 等住宅代理写成多 session / 多出口池（`proxies.txt`），再跑一轮注册 + TTS 对比，确认可见 hCaptcha 跳过换 IP、以及 `detected_unusual_activity` 是否随出口改善。

2. **Free Tier `401 detected_unusual_activity` 继续压测**  
   官方文案指向代理/VPN、多免费号。需验证：独立干净出口、降低同 IP 密度、注册与调用是否同出口。浏览器 UA/指纹不是主因，但调用侧 TLS（`curl_cffi` vs 真 Chrome）可做对照实验。

3. **可选：打码平台接入**  
   若不想依赖“可见选图就换代理”，可接入 CapSolver / 2Captcha 等自动解 hCaptcha（需用户自备 Key）。

4. **文档与示例同步**  
   保持 `docs/elevenlabs-manual-auth.md` 与导出 JSON 字段、网页调用头一致；按实测结果更新 FAQ。

## 稳定性机制

- 每个账号结束后完整重启浏览器，避免会话残留
- 并发 worker 独立 Chromium / profile
- 验证码未收到时自动换邮箱重试
- 页面卡住自动重试当前账号
- 全局每分钟输出创建速度
- CLI：第一次 Ctrl+C 请求停机收尾，连按两次强制退出

## 常见问题

**CLI 为什么还会开浏览器？**  
CLI 只是不启动 Tk；注册页与验证码仍依赖真实浏览器。

**ElevenLabs 出现可见 hCaptcha 选图？**  
程序会**中止本轮账号**，自动更换代理出口后重试（代理池换一条；固定住宅代理会旋转 `-session-` 出口）。建议 `proxy_mode=pool` 并准备多条代理。

**ElevenLabs 网页凭证调 TTS 返回 401 `detected_unusual_activity`？**  
多为免费档风控（代理/VPN、批量免费号），与是否完成 onboarding、是否创建官方 API Key 无关。可换更干净出口后重试；手动提凭证方法见 [`docs/elevenlabs-manual-auth.md`](docs/elevenlabs-manual-auth.md)。

**ElevenLabs 卡在 Sign up Loading？**  
通常是 invisible hCaptcha 未放行。可降低并发、更换出口 IP / 代理，或稍后重试。

**ElevenLabs 收不到验证邮件？**  
公开临时域可能被拒；优先 Cloudflare 自有域名，或换 Mail.tm / DuckMail 再试。

**Fish Audio 收不到 OTP？**  
同样优先自有域名邮箱。

**日志太多？**  
`log_level` 设为 `quiet` / `info` / `debug`。

## 目录结构

```text
.
├── grok_register_ttk.py     # 主程序（GUI / CLI）
├── gui_settings.py          # GUI 配置表单
├── proxy_bridge.py          # 固定代理 / 代理池 / session 旋转
├── temp_mail_providers.py   # Mail.tm / 1secMail
├── platforms/
│   ├── fishaudio.py         # Fish Audio
│   └── elevenlabs.py        # ElevenLabs（网页 Firebase 授权）
├── docs/
│   └── elevenlabs-manual-auth.md  # 手动提取网页凭证
├── scripts/
│   └── verify_eleven_web_auth.py  # 网页凭证探测
├── cf_turnstile.py          # Turnstile / CF cookie
├── turnstilePatch/          # 浏览器扩展补丁
├── cpa_export.py / cpa_xai/ # CPA 导出
├── config.example.json
├── requirements.txt
└── README.md
```

## 致谢

- 上游项目：[`maxucheng0/grok-auto-register`](https://github.com/maxucheng0/grok-auto-register)
- 本仓库在其基础上进行二次增强：多平台扩展、GUI 体验、并发稳定性、ElevenLabs 网页授权导出等

## License

[MIT](LICENSE)
