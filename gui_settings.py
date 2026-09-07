"""GUI 配置表单、主题和输入校验；不启动注册或执行网络请求。"""

import tkinter as tk
from tkinter import filedialog, ttk
from urllib.parse import urlsplit


UI_BG = "#f4f6fa"
UI_PANEL_BG = "#ffffff"
UI_FG = "#172235"
UI_MUTED_FG = "#526078"
IMPORT_TARGETS = {"CPA": "cpa", "grok2api": "grok2api", "CPA + grok2api": "both", "不自动导入": "none"}
PLATFORMS = {"Grok": "grok", "Fish Audio": "fishaudio"}
PLATFORM_LABELS = {value: label for label, value in PLATFORMS.items()}


def setup_light_theme(root):
    """统一使用可配置的 ttk 控件，避免 Aqua 原生背景与自定义文字颜色冲突。"""
    root.configure(background=UI_BG)
    root.option_add("*TCombobox*Listbox.background", UI_PANEL_BG)
    root.option_add("*TCombobox*Listbox.foreground", UI_FG)
    root.option_add("*TCombobox*Listbox.selectBackground", "#dbeafe")
    root.option_add("*TCombobox*Listbox.selectForeground", UI_FG)
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=UI_PANEL_BG, foreground=UI_FG, font=("Helvetica Neue", 13))
    style.configure("TFrame", background=UI_PANEL_BG)
    style.configure("Shell.TFrame", background=UI_BG)
    style.configure("Shell.TLabel", background=UI_BG)
    style.configure("Title.TLabel", background=UI_BG, font=("Helvetica Neue", 20, "bold"))
    style.configure("Source.TLabel", background=UI_BG, foreground=UI_MUTED_FG, font=("Helvetica Neue", 11))
    style.configure("Hint.TLabel", foreground=UI_MUTED_FG)
    style.configure("Section.TLabel", font=("Helvetica Neue", 14, "bold"))
    style.configure("Pages.TNotebook", background=UI_PANEL_BG, borderwidth=0, tabmargins=0)
    style.layout("Pages.TNotebook.Tab", [])
    style.configure("Nav.TFrame", background="#e8edf5")
    for name in ("TEntry", "TCombobox", "TSpinbox"):
        style.configure(name, padding=7, fieldbackground=UI_PANEL_BG, foreground=UI_FG,
                        insertcolor=UI_FG, bordercolor="#a7b3c4", lightcolor=UI_PANEL_BG,
                        darkcolor=UI_PANEL_BG, selectbackground="#dbeafe", selectforeground=UI_FG)
        style.map(name, fieldbackground=[("disabled", "#edf0f5"), ("readonly", UI_PANEL_BG)],
                  foreground=[("disabled", "#596579"), ("readonly", UI_FG)],
                  bordercolor=[("focus", "#2563eb")])
    style.configure("TButton", padding=(14, 8), background="#e7edf6", foreground=UI_FG,
                    borderwidth=1, bordercolor="#d4dce8", lightcolor="#e7edf6", darkcolor="#e7edf6", relief="flat")
    style.map("TButton", background=[("active", "#dbe4f2"), ("disabled", "#edf0f5")],
              foreground=[("disabled", "#596579")])
    style.configure("Primary.TButton", background="#2458c6", foreground="#ffffff")
    style.map("Primary.TButton", background=[("disabled", "#e0e6ef"), ("active", "#1949ad")],
              foreground=[("disabled", "#596579"), ("!disabled", "#ffffff")])
    for name, background, foreground in (("Nav.TButton", "#e8edf5", UI_MUTED_FG),
                                         ("Selected.Nav.TButton", UI_PANEL_BG, "#2458c6")):
        style.configure(name, padding=(16, 10), background=background, foreground=foreground,
                        bordercolor=background, lightcolor=background, darkcolor=background,
                        relief="flat", anchor="center", font=("Helvetica Neue", 13, "bold"))
        style.map(name, background=[("active", "#dce6f7"), ("!active", background)],
                  foreground=[("!disabled", foreground)],
                  bordercolor=[("focus", "#648bd5"), ("!focus", background)])
    for name in ("TCheckbutton", "TRadiobutton"):
        style.configure(name, padding=(0, 5), background=UI_PANEL_BG)
        style.map(name, background=[("active", UI_PANEL_BG)], foreground=[("disabled", "#596579")])


def infer_import_target(values):
    """从实际执行开关推断导入目标，兼容旧配置文件。"""
    cpa = bool(values.get("cpa_export_enabled", True))
    grok = bool(values.get("grok2api_auto_add_local", True) or values.get("grok2api_auto_add_remote", False))
    if cpa and grok:
        return "both"
    if cpa:
        return "cpa"
    if grok:
        return "grok2api"
    return "none"


def apply_import_target(values, target):
    """生成实际执行开关；配置文件中的业务字段是重新加载时的唯一来源。"""
    result = dict(values)
    result["cpa_export_enabled"] = target in ("cpa", "both")
    for key in ("grok2api_auto_add_local", "grok2api_auto_add_remote"):
        result.pop("gui_" + key, None)
        result[key] = target in ("grok2api", "both") and bool(values.get(key, False))
    return result


def normalize_platform(value):
    text = str(value or "grok").strip()
    if text in PLATFORMS:
        return PLATFORMS[text]
    raw = text.lower().replace(" ", "_").replace("-", "_")
    if raw in ("fish", "fishaudio", "fish_audio"):
        return "fishaudio"
    return "grok"


def validate_config(values):
    """校验执行所需字段；错误只包含字段名称，不包含密钥值。"""
    if values["concurrent_count"] > values["register_count"]:
        raise ValueError("并发数不能大于注册数量。")

    def require(key, label):
        if not str(values.get(key, "")).strip():
            raise ValueError(f"请填写{label}。")

    def url(key, label):
        require(key, label)
        try:
            parsed = urlsplit(values[key])
            valid = parsed.scheme in ("http", "https") and parsed.hostname and parsed.port != 0
        except ValueError:
            valid = False
        if not valid:
            raise ValueError(f"{label}需要填写有效的 http:// 或 https:// 地址。")

    platform = normalize_platform(values.get("platform", "grok"))
    values["platform"] = platform

    provider = str(values.get("email_provider") or "duckmail").strip().lower()
    values["email_provider"] = "onesecmail" if provider in ("1secmail", "one_sec_mail") else provider
    if values["email_provider"] == "cloudflare":
        url("cloudflare_api_base", "Cloudflare API Base")
        for key in ("domains", "accounts", "token", "messages"):
            value = values.get("cloudflare_path_" + key, "")
            if not value.startswith("/"):
                raise ValueError("Cloudflare 的四个接口路径均需以 / 开头。")
    if values["email_provider"] == "mailtm":
        raw = str(values.get("mailtm_api_base") or "https://api.mail.tm").strip() or "https://api.mail.tm"
        values["mailtm_api_base"] = raw.rstrip("/")
        try:
            parsed = urlsplit(values["mailtm_api_base"])
            valid = parsed.scheme in ("http", "https") and bool(parsed.hostname)
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("Mail.tm API Base 需要填写有效的 http:// 或 https:// 地址。")
    if values["email_provider"] == "onesecmail":
        raw = str(values.get("onesecmail_api_base") or "https://www.1secmail.com/api/v1/").strip()
        if not raw:
            raw = "https://www.1secmail.com/api/v1/"
        values["onesecmail_api_base"] = raw if raw.endswith("/") else raw + "/"
        try:
            parsed = urlsplit(values["onesecmail_api_base"])
            valid = parsed.scheme in ("http", "https") and bool(parsed.hostname)
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("1secMail API Base 需要填写有效的 http:// 或 https:// 地址。")

    if platform == "fishaudio":
        require("fish_auth_dir", "Fish Audio 授权输出目录")
        return

    if values.get("grok2api_auto_add_remote"):
        url("grok2api_remote_base", "grok2api 远端 Base")
        require("grok2api_remote_app_key", "grok2api app_key")
    if values.get("cpa_export_enabled"):
        require("cpa_auth_dir", "CPA 本地输出目录")
        url("cpa_base_url", "CPA API Base")
        if values.get("cpa_management_auto_upload"):
            url("cpa_management_base", "CPA 管理地址")
            require("cpa_management_key", "CPA Management Key")
        if values.get("cpa_copy_to_hotload"):
            require("cpa_hotload_dir", "CPA 热加载目录")
        if values.get("cpa_server_host"):
            require("cpa_server_user", "CPA SSH 用户")
            require("cpa_server_auth_dir", "CPA SSH 远端目录")


class SettingsPanel(ttk.Frame):
    """管理配置草稿；表单修改不直接改变当前批次的配置。"""

    def __init__(self, parent, values, on_change):
        super().__init__(parent)
        self.base_values = dict(values)
        self.on_change = on_change
        self.variables = {}
        self.fields = {}
        self.number_rules = {}
        self.secret_fields = []
        self.tabs = {}
        self.rows = {}
        self.loading = False
        self.navigation = ttk.Frame(self, padding=5, style="Nav.TFrame")
        self.navigation.pack(fill="x", pady=(0, 12))
        self.nav_buttons = []
        self.notebook = ttk.Notebook(self, style="Pages.TNotebook")
        self.notebook.pack(fill="both", expand=True)
        for index, name in enumerate(("基本设置", "邮箱服务", "自动导入", "高级设置")):
            self._tab(name)
            self.navigation.columnconfigure(index, weight=1, uniform="navigation")
            button = ttk.Button(self.navigation, text=name, style="Nav.TButton",
                                command=lambda selected=index: self.select_page(selected))
            button.grid(row=0, column=index, sticky="ew", padx=2)
            self.nav_buttons.append(button)
        self.notebook.bind("<<NotebookTabChanged>>", self._sync_navigation)
        self._build_basic()
        self._build_mail()
        self._build_import()
        self._build_advanced()
        for body, tag in self.tabs.values():
            self._bind_scroll(body, tag)
        for variable in self.variables.values():
            variable.trace_add("write", self._changed)
        self.target_var.trace_add("write", self._changed)
        self.platform_var.trace_add("write", self._changed)
        self._update_visibility()
        self._sync_navigation()

    def select_page(self, index):
        """切换配置页，导航按钮保持相同尺寸和位置。"""
        self.notebook.select(index)
        self._sync_navigation()

    def _sync_navigation(self, event=None):
        selected = self.notebook.index(self.notebook.select())
        for index, button in enumerate(self.nav_buttons):
            button.configure(style="Selected.Nav.TButton" if index == selected else "Nav.TButton")

    def load_values(self, values):
        """替换表单来源；保留当前配置页，不把读取过程标记为用户编辑。"""
        self.loading = True
        try:
            self.base_values = dict(values)
            for key, variable in self.variables.items():
                value = values.get(key, "")
                if key == "email_provider":
                    provider = str(value or "duckmail").strip().lower()
                    if provider in ("1secmail", "one_sec_mail", "1sec_mail"):
                        provider = "onesecmail"
                    value = provider
                variable.set(value)
            platform = normalize_platform(values.get("platform", "grok"))
            self.platform_var.set(PLATFORM_LABELS.get(platform, "Grok"))
            target = infer_import_target(values)
            self.target_var.set(next(label for label, value in IMPORT_TARGETS.items() if value == target))
        finally:
            self.loading = False
        self._update_visibility()

    def _tab(self, name):
        wrapper = ttk.Frame(self.notebook)
        self.notebook.add(wrapper, text=name)
        canvas = tk.Canvas(wrapper, background=UI_PANEL_BG, highlightthickness=0, width=1, height=1)
        scrollbar = ttk.Scrollbar(wrapper, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        body = ttk.Frame(canvas, padding=(22, 12))
        body.columnconfigure(1, weight=1)
        window = canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        canvas.bind("<Map>", lambda event: canvas.yview_moveto(0))

        def scroll(event):
            if canvas.bbox("all")[3] > canvas.winfo_height():
                amount = int(-event.delta) if abs(event.delta) < 120 else int(-event.delta / 120)
                canvas.yview_scroll(amount, "units")
            return "break"

        def scroll_to_focus(event):
            widget = event.widget
            if not isinstance(widget, (ttk.Entry, ttk.Combobox, ttk.Spinbox, ttk.Checkbutton, ttk.Button)):
                return
            top = widget.winfo_rooty() - body.winfo_rooty()
            height = max(body.winfo_height(), 1)
            first = canvas.canvasy(0)
            if top < first or top + widget.winfo_height() > first + canvas.winfo_height():
                canvas.yview_moveto(max(0, top - 16) / height)

        tag = "SettingsScroll" + str(body)
        body.bind_class(tag, "<MouseWheel>", scroll)
        body.bind_class(tag, "<FocusIn>", scroll_to_focus)
        self.tabs[name] = (body, tag)
        self.rows[body] = 0

    def _section(self, parent, title, hint=""):
        row = self.rows[parent]
        ttk.Label(parent, text=title, style="Section.TLabel").grid(row=row, column=0, columnspan=2, sticky="w", pady=(10, 6))
        self.rows[parent] += 1
        if hint:
            label = ttk.Label(parent, text=hint, style="Hint.TLabel", wraplength=720)
            label.grid(row=row + 1, column=0, columnspan=2, sticky="ew", pady=(0, 10))
            self.rows[parent] += 1

    def _field(self, parent, key, label, *, choices=None, minimum=None, secret=False, path=None):
        value = self.base_values.get(key, "")
        boolean = isinstance(value, bool)
        variable = tk.BooleanVar(self, value=value) if boolean else tk.StringVar(self, value=str(value))
        self.variables[key] = variable
        row = self.rows[parent]
        self.rows[parent] += 1
        container = ttk.Frame(parent)
        container.grid(row=row, column=1, sticky="ew", pady=5)
        container.columnconfigure(0, weight=1)
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 24), pady=5)
        if boolean:
            widget = ttk.Checkbutton(container, variable=variable, text="启用")
        elif choices:
            widget = ttk.Combobox(container, textvariable=variable, values=choices, state="readonly", width=24, font=("Helvetica Neue", 13))
        elif minimum is not None:
            widget = ttk.Spinbox(container, textvariable=variable, from_=minimum, to=1000000, width=12, font=("Helvetica Neue", 13))
            self.number_rules[key] = (label, minimum)
        else:
            widget = ttk.Entry(container, textvariable=variable, show="•" if secret else "", width=25, font=("Helvetica Neue", 13))
        widget.grid(row=0, column=0, sticky="w" if boolean or choices or minimum is not None else "ew")
        controls = [widget]
        if secret:
            self.secret_fields.append(widget)
        if path:
            def browse():
                if path == "directory":
                    selected = filedialog.askdirectory(parent=self, title=label)
                else:
                    selected = filedialog.asksaveasfilename(parent=self, title=label, confirmoverwrite=False)
                if selected:
                    variable.set(selected)
            button = ttk.Button(container, text="选择…", command=browse)
            button.grid(row=0, column=1, padx=(8, 0))
            controls.append(button)
        self.fields[key] = (controls, "readonly" if choices else "normal")
        return widget

    def _build_basic(self):
        body = self.tabs["基本设置"][0]
        self._section(body, "任务参数", "修改后可单独保存。开始注册时，自动保存并使用当前表单中的设置。")
        platform = normalize_platform(self.base_values.get("platform", "grok"))
        self.platform_var = tk.StringVar(self, value=PLATFORM_LABELS.get(platform, "Grok"))
        row = self.rows[body]
        self.rows[body] += 1
        ttk.Label(body, text="注册平台").grid(row=row, column=0, sticky="w", padx=(0, 24), pady=5)
        platform_box = ttk.Combobox(
            body,
            textvariable=self.platform_var,
            values=list(PLATFORMS.keys()),
            state="readonly",
            width=24,
            font=("Helvetica Neue", 13),
        )
        platform_box.grid(row=row, column=1, sticky="w", pady=5)
        self.fields["platform"] = ([platform_box], "readonly")
        self._field(body, "register_count", "注册数量", minimum=1)
        self._field(body, "concurrent_count", "并发浏览器数", minimum=1)
        self._field(body, "proxy", "注册代理（可留空）")
        self._field(body, "enable_nsfw", "注册后开启 NSFW")
        self._section(body, "日志与输出")
        self._field(body, "log_level", "日志级别", choices=("quiet", "info", "debug"))
        self._field(body, "speed_log_interval_sec", "速度统计间隔（秒）", minimum=1)
        self._field(body, "token_only_file", "额外 token 输出文件", path="file")
        self._section(body, "Fish Audio 输出", "仅 Fish Audio 平台使用；成功后写入本地授权 JSON。")
        self.fish_frame = ttk.Frame(body)
        self.fish_frame.columnconfigure(1, weight=1)
        self.fish_frame.grid(row=self.rows[body], column=0, columnspan=2, sticky="ew")
        self.rows[body] += 1
        self.rows[self.fish_frame] = 0
        self._field(self.fish_frame, "fish_auth_dir", "授权输出目录", path="directory")
        self._field(self.fish_frame, "fish_session_ttl_sec", "Session 有效期（秒）", minimum=60)

    def _build_mail(self):
        body = self.tabs["邮箱服务"][0]
        self._section(
            body,
            "邮箱服务",
            "仅启用当前服务商对应的配置；切换服务商会保留已填写的内容。"
            "Mail.tm / 1secMail 通常无需密钥；Fish Audio 若拒收公开临时域，优先用 Cloudflare 自有域名。",
        )
        self._field(
            body,
            "email_provider",
            "服务商",
            choices=("duckmail", "mailtm", "onesecmail", "yyds", "cloudflare"),
        )
        self.mail_frames = {}
        for provider in ("duckmail", "mailtm", "onesecmail", "yyds", "cloudflare"):
            frame = ttk.Frame(body)
            frame.columnconfigure(1, weight=1)
            frame.grid(row=self.rows[body], column=0, columnspan=2, sticky="ew")
            self.rows[body] += 1
            self.rows[frame] = 0
            self.mail_frames[provider] = frame
        self._field(self.mail_frames["duckmail"], "duckmail_api_key", "DuckMail API Key", secret=True)
        self._field(self.mail_frames["mailtm"], "mailtm_api_base", "Mail.tm API Base")
        self._field(self.mail_frames["onesecmail"], "onesecmail_api_base", "1secMail API Base")
        ttk.Label(
            self.mail_frames["onesecmail"],
            text="官方接口若返回 403，可换成可用镜像地址；留空则用默认官方地址。",
            style="Hint.TLabel",
            wraplength=720,
        ).grid(row=self.rows[self.mail_frames["onesecmail"]], column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self.rows[self.mail_frames["onesecmail"]] += 1
        self._field(self.mail_frames["yyds"], "yyds_api_key", "YYDS API Key", secret=True)
        self._field(self.mail_frames["yyds"], "yyds_jwt", "YYDS JWT", secret=True)
        cf = self.mail_frames["cloudflare"]
        self._field(cf, "cloudflare_api_base", "Cloudflare API Base")
        self._field(cf, "cloudflare_auth_mode", "鉴权模式", choices=("none", "query-key", "bearer", "x-api-key", "x-admin-auth"))
        self._field(cf, "cloudflare_api_key", "Cloudflare API Key", secret=True)
        self._field(cf, "defaultDomains", "默认邮箱域名")
        for suffix, label in (("domains", "域名列表路径"), ("accounts", "创建邮箱路径"), ("token", "获取 token 路径"), ("messages", "邮件列表路径")):
            self._field(cf, "cloudflare_path_" + suffix, label)

    def _build_import(self):
        body = self.tabs["自动导入"][0]
        self._section(body, "注册成功后的导入目标", "CPA 会先生成本地凭证，再按选项导入；grok2api 使用 SSO token 入池。Fish Audio 仅写本地授权文件。")
        self.import_hint = ttk.Label(
            body,
            text="当前平台为 Fish Audio：跳过 CPA / grok2api 导入，仅写入 fish_auth_dir。",
            style="Hint.TLabel",
            wraplength=720,
        )
        self.import_hint.grid(row=self.rows[body], column=0, columnspan=2, sticky="ew", pady=(0, 12))
        self.rows[body] += 1
        self.import_hint.grid_remove()
        target = infer_import_target(self.base_values)
        self.target_var = tk.StringVar(self, value=next(label for label, value in IMPORT_TARGETS.items() if value == target))
        self.target_combo = ttk.Combobox(body, textvariable=self.target_var, values=list(IMPORT_TARGETS), state="readonly", font=("Helvetica Neue", 13))
        self.target_combo.grid(row=self.rows[body], column=0, columnspan=2, sticky="ew", pady=(0, 12))
        self.rows[body] += 1
        self.import_frames = {}
        for name in ("cpa", "grok2api"):
            frame = ttk.Frame(body)
            frame.columnconfigure(1, weight=1)
            frame.grid(row=self.rows[body], column=0, columnspan=2, sticky="ew")
            self.rows[body] += 1
            self.rows[frame] = 0
            self.import_frames[name] = frame
        cpa = self.import_frames["cpa"]
        self._section(cpa, "CPA 管理 API 自动导入", "管理地址填写 CPA 站点地址或 /v0/management；Management Key 默认隐藏。")
        self._field(cpa, "cpa_management_auto_upload", "自动导入 CPA")
        self._field(cpa, "cpa_management_base", "CPA 管理地址")
        self._field(cpa, "cpa_management_key", "Management Key", secret=True)
        self._field(cpa, "cpa_management_timeout_sec", "导入超时（秒）", minimum=3)
        self._field(cpa, "cpa_management_use_system_proxy", "管理 API 使用系统代理")
        self._field(cpa, "cpa_management_upload_required", "导入失败时标记导出失败")
        self._field(cpa, "cpa_auth_dir", "本地凭证输出目录", path="directory")
        self._section(cpa, "本地 CPA 热加载（可选）")
        self._field(cpa, "cpa_copy_to_hotload", "复制到热加载目录")
        self._field(cpa, "cpa_hotload_dir", "热加载目录", path="directory")
        grok = self.import_frames["grok2api"]
        self._section(grok, "grok2api 入池", "可选择本地、远端或同时入池。本地路径留空时使用现有自动查找逻辑。")
        self._field(grok, "grok2api_auto_add_local", "写入本地 token 池")
        self._field(grok, "grok2api_local_token_file", "本地 token.json", path="file")
        self._field(grok, "grok2api_pool_name", "token 池", choices=("ssoBasic", "ssoSuper"))
        self._field(grok, "grok2api_auto_add_remote", "写入远端 token 池")
        self._field(grok, "grok2api_remote_base", "远端 Base")
        self._field(grok, "grok2api_remote_app_key", "远端 app_key", secret=True)

    def _build_advanced(self):
        body = self.tabs["高级设置"][0]
        self._section(body, "浏览器与验证")
        for key, label in (("cf_auto_click", "自动点击人机验证"), ("keep_cf_cookies", "保留 CF Cookie"), ("cf_os_click", "使用系统鼠标点击"), ("browser_use_custom_ua", "使用自定义 User-Agent")):
            self._field(body, key, label)
        self._field(body, "user_agent", "User-Agent")
        self._field(body, "cf_turnstile_timeout_sec", "人机验证超时（秒）", minimum=1)
        self._field(body, "browser_restart_every", "浏览器周期重启提示（账号数）", minimum=0)
        self.cpa_advanced_frame = ttk.Frame(body)
        self.cpa_advanced_frame.columnconfigure(1, weight=1)
        self.cpa_advanced_frame.grid(row=self.rows[body], column=0, columnspan=2, sticky="ew")
        self.rows[body] += 1
        self.rows[self.cpa_advanced_frame] = 0
        self._section(self.cpa_advanced_frame, "CPA 凭证生成", "导入目标包含 CPA 时生效；CPA 代理留空时沿用注册代理或环境代理。")
        for key, label in (("cpa_mint_async", "后台生成凭证"), ("cpa_headless", "凭证浏览器无头模式"), ("cpa_probe_after_write", "生成后探测可用性"), ("cpa_force_standalone", "强制使用独立浏览器"), ("cpa_mint_cookie_inject", "注入注册 Cookie"), ("cpa_mint_browser_reuse", "复用凭证浏览器")):
            self._field(self.cpa_advanced_frame, key, label)
        self._field(self.cpa_advanced_frame, "cpa_proxy", "CPA 代理（可留空）")
        self._field(self.cpa_advanced_frame, "cpa_base_url", "CPA API Base")
        self._field(self.cpa_advanced_frame, "cpa_mint_timeout_sec", "凭证生成超时（秒）", minimum=1)
        self._field(self.cpa_advanced_frame, "cpa_mint_browser_recycle_every", "凭证浏览器回收间隔（账号数）", minimum=0)
        self._section(self.cpa_advanced_frame, "CPA SSH 上传（可选）", "这是另一种上传方式，可与管理 API 同时启用。主机留空时不执行 SSH 上传。")
        for key, label in (("cpa_server_host", "SSH 主机"), ("cpa_server_user", "SSH 用户"), ("cpa_server_password", "SSH 密码"), ("cpa_server_auth_dir", "SSH 远端凭证目录")):
            self._field(self.cpa_advanced_frame, key, label, secret=key.endswith("password"))

    def _changed(self, *_):
        if self.loading:
            return
        self._update_visibility()
        self.on_change()

    def current_platform(self):
        label = self.platform_var.get()
        return PLATFORMS.get(label) or normalize_platform(label)

    def _update_visibility(self):
        platform = self.current_platform()
        is_fish = platform == "fishaudio"
        target = IMPORT_TARGETS[self.target_var.get()]
        if hasattr(self, "fish_frame"):
            if is_fish:
                self.fish_frame.grid()
            else:
                self.fish_frame.grid_remove()
        if hasattr(self, "import_hint"):
            if is_fish:
                self.import_hint.grid()
                self.target_combo.configure(state="disabled")
            else:
                self.import_hint.grid_remove()
                self.target_combo.configure(state="readonly")
        if hasattr(self, "cpa_advanced_frame"):
            if is_fish:
                self.cpa_advanced_frame.grid_remove()
            else:
                self.cpa_advanced_frame.grid()
        for name, frame in self.import_frames.items():
            if is_fish:
                frame.grid_remove()
            elif target in (name, "both"):
                frame.grid()
            else:
                frame.grid_remove()
        for provider, frame in self.mail_frames.items():
            if provider == self.variables["email_provider"].get():
                frame.grid()
            else:
                frame.grid_remove()
        for key, (controls, normal_state) in self.fields.items():
            enabled = True
            if key == "enable_nsfw":
                enabled = not is_fish
            elif key == "token_only_file":
                enabled = not is_fish
            elif key.startswith("fish_"):
                enabled = is_fish
            elif key.startswith("cpa_"):
                enabled = (not is_fish) and target in ("cpa", "both")
                if key.startswith("cpa_management_") and key != "cpa_management_auto_upload":
                    enabled = enabled and self.variables["cpa_management_auto_upload"].get()
                if key == "cpa_hotload_dir":
                    enabled = enabled and self.variables["cpa_copy_to_hotload"].get()
            elif key.startswith("grok2api_"):
                enabled = not is_fish
                if key in ("grok2api_remote_base", "grok2api_remote_app_key"):
                    enabled = enabled and self.variables["grok2api_auto_add_remote"].get()
                elif key == "grok2api_local_token_file":
                    enabled = enabled and self.variables["grok2api_auto_add_local"].get()
            elif key == "user_agent":
                enabled = self.variables["browser_use_custom_ua"].get()
            for index, control in enumerate(controls):
                state = normal_state if index == 0 else "normal"
                control.configure(state=state if enabled else "disabled")
    def _bind_scroll(self, widget, tag):
        if tag not in widget.bindtags():
            widget.bindtags((tag,) + widget.bindtags())
        for child in widget.winfo_children():
            self._bind_scroll(child, tag)

    def show_secrets(self, visible):
        for widget in self.secret_fields:
            widget.configure(show="" if visible else "•")

    def collect(self):
        """返回完整、已校验的配置副本，保留表单未覆盖的扩展字段。"""
        values = dict(self.base_values)
        for key, variable in self.variables.items():
            value = variable.get()
            if key in self.number_rules:
                label, minimum = self.number_rules[key]
                try:
                    value = int(value)
                except (ValueError, TypeError):
                    raise ValueError(f"{label}必须是整数。") from None
                if value < minimum:
                    raise ValueError(f"{label}不能小于 {minimum}。")
            elif isinstance(value, str):
                value = value.strip()
            values[key] = value
        values["platform"] = self.current_platform()
        if values["platform"] == "fishaudio":
            values = apply_import_target(values, "none")
        else:
            target = IMPORT_TARGETS[self.target_var.get()]
            values = apply_import_target(values, target)
            if target in ("grok2api", "both") and not (values["grok2api_auto_add_local"] or values["grok2api_auto_add_remote"]):
                raise ValueError("请选择 grok2api 的本地或远端入池方式。")
        validate_config(values)
        return values
