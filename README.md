<div align="center">

[![Grok Register — GUI and CLI registration automation toolkit](assets/banner.png)](https://github.com/AaronL725/grok-register)

Grok Register 是一个面向自动化流程研究、测试环境验证和个人学习的 Python 自动化注册工具 — 支持 GUI / CLI、临时邮箱、浏览器流程控制、账号输出和 grok2api token 池写入。

<p>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License: MIT"></a>
  <img src="https://img.shields.io/badge/Python-3.9%2B-3776AB.svg" alt="Python 3.9+">
  <img src="https://img.shields.io/badge/Interface-GUI%20%2B%20CLI-success.svg" alt="GUI + CLI">
  <img src="https://img.shields.io/badge/Browser-Chromium%2FChrome-4285F4.svg" alt="Chromium/Chrome">
  <a href="http://makeapullrequest.com"><img src="https://img.shields.io/badge/PRs-welcome-brightgreen.svg" alt="PRs Welcome"></a>
  <a href="https://linux.do"><img src="https://img.shields.io/badge/Join-linux.do-orange" alt="linux.do"></a>
</p>

<p align="center">
 <a href="https://www.star-history.com/aaronl725/grok-register">
  <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/badge?repo=AaronL725/grok-register&theme=dark" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/badge?repo=AaronL725/grok-register" />
   <img alt="Star History Rank" src="https://api.star-history.com/badge?repo=AaronL725/grok-register" />
  </picture>
 </a>
</p>

</div>

---

> 本项目仅用于自动化流程研究、测试环境验证和个人学习。请遵守目标网站服务条款、当地法律法规和第三方服务限制。

## Contents

- [功能](#功能)
- [环境要求](#环境要求)
- [安装](#安装)
- [配置](#配置)
- [邮箱服务配置教程](#邮箱服务配置教程)
- [运行](#运行)
- [输出文件](#输出文件)
- [稳定性机制](#稳定性机制)
- [常见问题](#常见问题)
- [目录结构](#目录结构)
- [License](#license)
- [Acknowledgments](#acknowledgments)
- [Star History](#star-history)

## 功能

- 支持 GUI 图形界面运行。
- 支持 CLI 终端运行，不启动 Tk GUI。
- 支持平台切换：`Grok` / `Fish Audio`（配置项 `platform`）。
- 注册流程使用 Chromium/Chrome 浏览器页面完成。
- 支持多 worker 并发注册（`concurrent_count`），每个 worker 独立浏览器与隔离 profile。
- 支持 DuckMail、Mail.tm、1secMail、YYDS、Cloudflare 临时邮箱接口。
- 支持验证码邮件轮询和解析。
- 支持成功账号实时写入 `accounts_*.txt`（Fish Audio 为 `accounts_fish_*.txt`）。
- Grok：支持将 SSO token 写入 grok2api 本地或远端池；支持 NSFW；支持 CPA xAI 凭证异步导出。
- Fish Audio：邮箱 OTP 注册后写出本地授权 JSON（`fish_auth_dir`，默认 `fish_auths/`），不创建 API Key、不做远端导入。
- 支持日志级别（`quiet` / `info` / `debug`）与每分钟创建速度统计。
- 支持页面卡住检测、当前账号重试、每账号浏览器重启和内存清理。

## 环境要求

- Python 3.9+
- Google Chrome 或 Chromium
- 可访问注册页面和临时邮箱 API 的网络环境

## 安装

下载项目到电脑：

```bash
git clone https://github.com/maxucheng0/grok-register.git
cd grok-register
```

安装依赖：

```bash
pip install -r requirements.txt
```

复制配置文件：

```bash
cp config.example.json config.json
```

然后按需编辑 `config.json`。

## 配置

### GUI 配置

macOS 可使用项目虚拟环境启动：

```bash
./.venv/bin/python grok_register_ttk.py
```

界面使用浅色高对比度控件，配置通过顶部四个等高导航按钮切换，内容较多时可滚动：

- **基本设置**：注册平台（Grok / Fish Audio）、注册数量、并发浏览器数、代理、日志级别和输出文件；Fish Audio 另有授权输出目录。
- **邮箱服务**：选择 DuckMail、Mail.tm、1secMail、YYDS 或 Cloudflare，仅显示对应的配置。
- **自动导入**：Grok 可选 CPA、grok2api、两者同时或不自动导入；Fish Audio 仅写本地授权文件。
- **高级设置**：浏览器、人机验证；Grok 另有 CPA 凭证生成和可选 SSH 上传配置。

导入 CPA 时，选择 **CPA**，勾选 **自动导入 CPA**，填写 **CPA 管理地址**和 **Management Key**。管理地址可填站点根地址或 `/v0/management` 地址；凭证生成后会调用管理 API 导入，本地凭证仍保留。grok2api 的本地、远端入池均不会在仅选择 CPA 时执行。只需生成本地 CPA 文件时，取消勾选自动导入即可。

**保存配置**只保存设置，不启动任务。**开始注册**会校验、保存并使用当前表单。运行期间仍可修改和保存，修改用于下一批；当前批次保持启动时的设置。停止后需等待浏览器和 CPA 后台任务收尾，之后才能开始下一批，无需重启 GUI。

配置区和日志区之间的分隔条可上下拖动，拖动时实时调整高度；底部操作按钮保持可见。

窗口顶部显示实际读取的配置文件路径，并提供“重新读取配置”。程序检测到文件在外部修改后，会自动刷新未编辑的表单；界面存在未保存修改时，会保留草稿并阻止直接覆盖文件，需先重新读取。文件格式错误时保留当前表单并提示错误，不会静默用默认值覆盖文件。

密钥默认隐藏，可通过“显示密钥”临时查看。设置保存在脚本旁的 `config.json`，文件权限为 `0600`；输入无效或保存失败时不会启动任务。界面读取以实际业务字段为准，不再使用旧的 `gui_grok2api_auto_add_local` / `gui_grok2api_auto_add_remote` 覆盖文件开关；保存后界面开关与文件中的实际值保持一致。

常用配置项：

| 配置项 | 说明 |
| --- | --- |
| `platform` | 注册平台：`grok`（默认）或 `fishaudio` |
| `fish_auth_dir` | Fish Audio 授权 JSON 输出目录，默认 `fish_auths` |
| `fish_session_ttl_sec` | Fish session 过期时长（秒），默认 `604800`（7 天） |
| `email_provider` | 邮箱服务商：`duckmail`、`mailtm`、`onesecmail`、`yyds`、`cloudflare` |
| `mailtm_api_base` | Mail.tm API 地址，默认 `https://api.mail.tm`（通常无需改） |
| `onesecmail_api_base` | 1secMail API 地址，默认 `https://www.1secmail.com/api/v1/` |
| `register_count` | 本次目标注册数量 |
| `proxy` | 代理地址，可留空 |
| `enable_nsfw` | 注册后是否尝试开启 NSFW（仅 Grok） |
| `cloudflare_api_base` | Cloudflare 临时邮箱 API 地址 |
| `cloudflare_api_key` | Cloudflare 临时邮箱接口密钥；默认匿名模式留空，admin 模式填 `ADMIN_PASSWORD` |
| `cloudflare_auth_mode` | Cloudflare API 鉴权模式；默认 `none`，可选 `bearer`、`x-api-key`、`x-admin-auth`、`query-key` |
| `cloudflare_path_domains` | Cloudflare 域名列表路径；默认 `/api/domains` |
| `cloudflare_path_accounts` | Cloudflare 创建邮箱路径；默认匿名模式用 `/api/new_address`，admin 模式用 `/admin/new_address` |
| `cloudflare_path_token` | Cloudflare token 路径；默认 `/api/token` |
| `cloudflare_path_messages` | Cloudflare 收件列表路径；默认 `/api/mails` |
| `defaultDomains` | Cloudflare 临时邮箱默认域名 |
| `grok2api_auto_add_local` | 是否写入本地 grok2api token 池 |
| `grok2api_local_token_file` | 本地 grok2api token 文件路径 |
| `grok2api_auto_add_remote` | 是否写入远端 grok2api |
| `grok2api_remote_base` | 远端 grok2api 地址，可填站点根地址或 `/admin/api` 管理 API 地址 |
| `grok2api_remote_app_key` | 远端 grok2api app key |
| `concurrent_count` | 并发 worker 数；`1` 为单浏览器顺序注册，`>1` 为多浏览器并发 |
| `browser_restart_every` | 额外周期重启提示间隔（账号数）；**每个账号结束后仍会完整重启浏览器**，避免会话残留 |
| `cpa_export_enabled` | 是否在注册成功后导出 CPA xAI 凭证 |
| `cpa_mint_async` | 是否异步 mint CPA（默认 `true`：独立浏览器 + 后台线程，不阻塞下一号注册） |
| `cpa_probe_after_write` | 写出 CPA 文件后是否探测接口可用性 |
| `log_level` | 日志级别：`quiet` / `info`（默认）/ `debug`；`info` 会隐藏高频 `[Debug]` |
| `speed_log_interval_sec` | 创建速度统计间隔秒数，默认 `60`；输出类似 `成功 9/min` |
| `browser_use_custom_ua` | 是否强制使用配置中的自定义 UA（默认 `false`，更贴近本机 Chrome） |
| `token_only_file` | 仅写入 SSO token 的附加文件路径，可留空 |
| `cf_auto_click` | 遇到 Cloudflare 人机验证时自动点击勾选框（默认 `true`） |
| `keep_cf_cookies` | 账号间重启浏览器时保留 `cf_clearance` 等 CF cookie，清掉 SSO 登录态（默认 `true`） |
| `cf_os_click` | 元素点击失败时用系统鼠标点验证框；窗口需在前台（默认 `true`） |
| `cf_turnstile_timeout_sec` | 单次自动处理人机验证的超时秒数，默认 `45` |

### Cloudflare 临时邮箱匿名模式（默认）

默认情况下，Cloudflare 邮箱使用 `dreamhunter2333/cloudflare_temp_email` 的匿名接口创建邮箱并读取邮件：

- 创建邮箱：`POST /api/new_address`
- 读取邮件：`GET /api/mails`
- 鉴权模式：`none`
- `cloudflare_api_key`：留空

这是项目的默认路线。没有特殊需求时，保持下面配置即可：

```json
{
  "email_provider": "cloudflare",
  "cloudflare_api_base": "https://你的-worker-api-域名",
  "cloudflare_api_key": "",
  "cloudflare_auth_mode": "none",
  "cloudflare_path_domains": "/api/domains",
  "cloudflare_path_accounts": "/api/new_address",
  "cloudflare_path_token": "/api/token",
  "cloudflare_path_messages": "/api/mails",
  "defaultDomains": "你的收信域名.com"
}
```

### Cloudflare 临时邮箱 admin 模式（可选）

如果使用 `dreamhunter2333/cloudflare_temp_email` 且匿名 `/api/new_address` 开启了 Turnstile，可以改用 admin 创建邮箱接口：

```json
{
  "email_provider": "cloudflare",
  "cloudflare_api_base": "https://你的-worker-api-域名",
  "cloudflare_api_key": "你的 ADMIN_PASSWORD",
  "cloudflare_auth_mode": "x-admin-auth",
  "cloudflare_path_accounts": "/admin/new_address",
  "cloudflare_path_messages": "/api/mails",
  "defaultDomains": "你的收信域名.com"
}
```

创建邮箱会使用 `x-admin-auth` 调用 `/admin/new_address`，后续收件仍使用接口返回的地址 JWT 调用 `/api/mails`。也就是说，admin 密码只用于创建邮箱，不用于读取邮箱邮件。

可先用调试脚本验证 admin 创建接口：

```bash
python cf_mail_debug.py --api-base "https://你的-worker-api-域名" --auth-mode x-admin-auth --api-key "你的 ADMIN_PASSWORD" --create-path /admin/new_address --domain "你的收信域名.com"
```

### grok2api 远端入池配置

如果开启 `grok2api_auto_add_remote`，`grok2api_remote_base` 可以填写站点根地址，也可以直接填写管理 API 地址：

```json
{
  "grok2api_auto_add_remote": true,
  "grok2api_remote_base": "https://你的-grok2api-域名",
  "grok2api_remote_app_key": "你的 app_key"
}
```

或：

```json
{
  "grok2api_auto_add_remote": true,
  "grok2api_remote_base": "https://你的-grok2api-域名/admin/api",
  "grok2api_remote_app_key": "你的 app_key"
}
```

程序会优先尝试 `/tokens/add`，并兼容 `/admin/api/tokens/add`；旧版全量保存接口也会兼容 `/tokens` 和 `/admin/api/tokens`。

`config.json` 包含个人配置和密钥，不要提交到 Git。

## 运行

### CLI 模式

CLI 模式不会启动 Tk GUI，但注册流程仍会打开 Chromium/Chrome 浏览器页面。

```bash
python grok_register_ttk.py cli
```

看到提示后输入：

```text
start
```

停止任务：

```text
Ctrl+C
```

CLI 模式适合长时间批量运行。每个账号结束后会完整重启浏览器；另外每成功注册 5 个账号会做一次运行时内存清理。

并发示例（在 `config.json` 中设置）：

```json
{
  "register_count": 20,
  "concurrent_count": 3,
  "log_level": "info",
  "speed_log_interval_sec": 60
}
```

### GUI 模式

```bash
python grok_register_ttk.py
```

GUI 模式会打开 Tkinter 窗口，适合手动调整配置和观察日志。日志同样受 `log_level` 过滤，并会打印全局创建速度。

## 邮箱服务配置教程

GUI 路径：**邮箱服务** 页 → **服务商** 下拉框。切换后只显示当前服务商字段，其它已填内容会保留。

### 推荐顺序（尤其是 Fish Audio）

1. **Cloudflare 自有域名**（最稳，不容易被目标站拒信）
2. **Mail.tm**（免密钥，公开 API，适合兜底）
3. **DuckMail / YYDS**（按你已有账号使用）
4. **1secMail**（免密钥；官方接口在部分网络会 `403`，可换镜像 Base）

### Mail.tm（推荐兜底）

1. 打开 GUI → **邮箱服务**
2. 服务商选择 `mailtm`
3. `Mail.tm API Base` 保持默认：`https://api.mail.tm`
4. 保存配置后开始注册

无需 API Key。程序会自动：拉取域名 → 创建邮箱 → 取 token → 轮询验证码邮件。

官方文档：<https://docs.mail.tm/>

`config.json` 示例：

```json
{
  "email_provider": "mailtm",
  "mailtm_api_base": "https://api.mail.tm"
}
```

### 1secMail

1. GUI → **邮箱服务** → 服务商选择 `onesecmail`
2. `1secMail API Base` 默认：`https://www.1secmail.com/api/v1/`
3. 若日志出现 `403`，把 Base 换成你可用的兼容镜像地址（需同样支持 `genRandomMailbox` / `getMessages` / `readMessage`）
4. 保存后开始注册

无需 API Key。程序用 `genRandomMailbox` 创建地址，再按 `login` + `domain` 拉信。

`config.json` 示例：

```json
{
  "email_provider": "onesecmail",
  "onesecmail_api_base": "https://www.1secmail.com/api/v1/"
}
```

说明：官方 `1secmail.com` 目前对不少出口 IP 直接返回 403。若你这边不通，优先切回 **Mail.tm** 或 **Cloudflare 自有域名**。

### DuckMail

1. 服务商选择 `duckmail`
2. 如有密钥则填写 `DuckMail API Key`（也可留空走匿名）
3. 保存后开始

### Cloudflare 自有域名（稳定首选）

1. 服务商选择 `cloudflare`
2. 填写你的临时邮 Worker / 站点：
   - `Cloudflare API Base`
   - `默认邮箱域名`（`defaultDomains`）
   - 鉴权模式与 Key（按你的部署：`none` / `bearer` / `x-api-key` / `x-admin-auth` / `query-key`）
3. 四个路径默认可用；若你改过路由再调整

适合 Fish Audio 等容易拒收公开临时域的站点。

### YYDS

1. 服务商选择 `yyds`
2. 填写 `YYDS API Key` 与 `YYDS JWT`
3. 保存后开始

## 输出文件

运行过程中会生成：

- `accounts_*.txt`：Grok 成功账号、密码和 SSO token。
- `accounts_fish_*.txt`：Fish Audio 成功账号行（`email----password----token----team----workspace`）。
- `fish_auths/`：Fish Audio 授权 JSON（`platform=fishaudio` 时）。
- `mail_credentials.txt`：临时邮箱凭证。
- `cpa_auths/`：CPA xAI 凭证 JSON（Grok 且开启 `cpa_export_enabled` 时）。
- `.browser_profiles/`：并发 worker 临时浏览器 profile（运行中生成，已 gitignore）。
- `*.log`：可选日志文件。

这些文件包含敏感信息，已被 `.gitignore` 忽略。

## 稳定性机制

- **每个账号结束后完整重启浏览器**（`restart_browser`），避免复用上号 SSO / 落到 `tos-gate` 等错误页。
- 并发 worker 使用独立 Chromium 与隔离 user-data 目录。
- 默认 CPA 异步 mint 使用独立浏览器（`page=None`），不占用注册 tab。
- Cloudflare 拦截页检测与打开注册页重试。
- 每成功 5 个账号执行一次内存清理。
- CLI 支持 `Ctrl+C`：第一次请求停止并收尾，连按两次强制退出。
- 最终页长时间无变化时自动重试当前账号。
- 验证码未收到时自动更换邮箱重试。
- 全局每分钟输出创建速度（成功数 / min）。

## 常见问题

### CLI 模式为什么还会打开浏览器？

CLI 模式只是不启动 Tk GUI。注册页、Turnstile、验证码提交和 SSO cookie 获取仍依赖真实浏览器环境。

### 并发时前几个成功、后面提示找不到「使用邮箱注册」？

常见原因是账号间会话残留（例如页面落到 `grok.com/tos-gate`）。当前版本在每个账号结束后都会完整重启浏览器；请确认使用最新代码，且不要改回「仅轻量清 cookie、不重启」。

### Cloudflare 人机验证每次都要手点？

注册页上的 Turnstile 勾选框会由程序自动点：先点 iframe 坐标，失败再用系统鼠标。请保持浏览器窗口在前台，不要最小化。

- 确认项目里有 `turnstilePatch/` 扩展目录。
- `cf_auto_click` / `keep_cf_cookies` 保持开启。
- 单开时可用系统鼠标点击（需 macOS 辅助功能权限）；`concurrent_count > 1` 时会自动禁用系统鼠标，改用各浏览器独立 CDP 点击，避免点错窗口。
- 多开 10 个浏览器可以跑：每个 worker 独立 Chromium / profile / CF cookie。但同一代理 IP 更容易被加强验证，建议住宅代理或降低并发；机器也要扛得住 10 个 Chrome。
- 并发建议先用 `1` 验证流程，再逐步加大。

### NSFW 开启失败怎么办？

如果日志显示 `Cloudflare 防护拦截，HTTP 403`，说明请求被目标站点防护拦截。程序会继续保存账号和写入 grok2api。

### 日志太多 / 想看 Debug？

在 `config.json` 设置：

- `"log_level": "quiet"`：只看成功/失败/关键警告与速度
- `"log_level": "info"`：默认，隐藏 `[Debug]`
- `"log_level": "debug"`：全量诊断

### GUI 显示的数量和配置不同？

GUI 数量控件可能有上限。CLI 模式直接读取 `config.json` 中的 `register_count`。

### Mail.tm / 1secMail 收不到验证码？

- 先看日志是否已成功创建邮箱；若卡在「拉取验证码」，多半是目标站拒收该临时域。
- Fish Audio 对公开临时域不稳定：可多试几次，或改用 Cloudflare 自有域名。
- 1secMail 若一创建就 `403`，换 `onesecmail_api_base` 或改用 `mailtm`。

## 目录结构

```text
.
├── grok_register_ttk.py   # 主程序（GUI/CLI 注册）
├── gui_settings.py        # GUI 配置表单
├── temp_mail_providers.py # Mail.tm / 1secMail 邮箱实现
├── platforms/             # 平台实现（含 Fish Audio）
├── cf_turnstile.py        # Turnstile 检测 / 自动点击 / CF cookie
├── turnstilePatch/        # CDP 鼠标坐标补丁扩展
├── cpa_export.py          # CPA xAI 导出入口
├── cpa_xai/               # CPA mint / OAuth / schema
├── cf_mail_debug.py       # Cloudflare 邮箱调试工具
├── config.example.json    # 配置示例
├── requirements.txt       # Python 依赖
└── README.md
```

## License

[MIT](LICENSE).

## Acknowledgments

Thanks to [linux.do](https://linux.do) — a vibrant tech community where this project is shared and discussed.

## Star History

<a href="https://www.star-history.com/?repos=AaronL725%2Fgrok-register&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=AaronL725/grok-register&type=date&theme=dark&legend=top-left&sealed_token=uCM--S2xEp0n8rFUZHUg6wUJOgYcfO4XEVCIF9UZAT04YjL9YsMEOVOGAOlQfqwsoS7cQef0Rwc1cYCY4lAmTuMmcg-hKzNnx1A7KNekuCXQotFd4YifLIkvJWOEy5vxiREJX80Mwxbr8F-3GfCv0utIsQz_iq19nS57svUqwv0mSosV8OTxqXTLjmsI" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=AaronL725/grok-register&type=date&legend=top-left&sealed_token=uCM--S2xEp0n8rFUZHUg6wUJOgYcfO4XEVCIF9UZAT04YjL9YsMEOVOGAOlQfqwsoS7cQef0Rwc1cYCY4lAmTuMmcg-hKzNnx1A7KNekuCXQotFd4YifLIkvJWOEy5vxiREJX80Mwxbr8F-3GfCv0utIsQz_iq19nS57svUqwv0mSosV8OTxqXTLjmsI" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=AaronL725/grok-register&type=date&legend=top-left&sealed_token=uCM--S2xEp0n8rFUZHUg6wUJOgYcfO4XEVCIF9UZAT04YjL9YsMEOVOGAOlQfqwsoS7cQef0Rwc1cYCY4lAmTuMmcg-hKzNnx1A7KNekuCXQotFd4YifLIkvJWOEy5vxiREJX80Mwxbr8F-3GfCv0utIsQz_iq19nS57svUqwv0mSosV8OTxqXTLjmsI" />
 </picture>
</a>
