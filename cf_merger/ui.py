from __future__ import annotations

import queue
import threading
import tkinter as tk
import tkinter.font as tkfont
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Callable

from . import APP_NAME, PROJECT_URL, __version__
from .config import (
    ConfigError,
    ConfigLoadResult,
    Source,
    load_config,
    save_config,
    validate_url,
)
from .downloader import DownloadClient
from .ip_filter import filter_ip_lines, ip_filter_label
from .paths import (
    get_app_dir,
    get_config_path,
    get_output_path,
    get_resource_path,
    get_settings_path,
    open_local_path,
)
from .service import MergeService, ProgressEvent, RunReport
from .settings import UiSettings, load_settings, save_settings

# 浅色界面配色
BG = "#F3F6FA"
SURFACE = "#FFFFFF"
NAVY = "#102A43"
TEXT = "#172B4D"
MUTED = "#6B7C93"
SUBTLE = "#8A9AAF"
BORDER = "#DDE6F1"
ACCENT = "#2563EB"
ACCENT_HOVER = "#1D4ED8"
GREEN = "#0F766E"
GREEN_SOFT = "#E7F6F2"
AMBER = "#B45309"
AMBER_SOFT = "#FFF6E6"
RED = "#B42318"
RED_SOFT = "#FDECEC"
BLUE_SOFT = "#EAF1FE"
HEADER_SUBTLE = "#8FB3D0"


@dataclass(slots=True)
class _RowRuntime:
    state: str = "待合并"
    lines: int | None = None
    detail: str = ""


class _CheckOption(tk.Frame):
    """使用 Canvas 绘制的清晰勾选项，避免 ttk 主题显示成叉号。"""

    def __init__(
        self,
        parent: tk.Misc,
        text: str,
        variable: tk.BooleanVar,
        font: tuple[str, int, str],
    ) -> None:
        super().__init__(parent, bg=SURFACE, bd=0, highlightthickness=0)
        self.variable = variable
        self._enabled = True
        self._hovered = False
        self.canvas = tk.Canvas(
            self,
            width=18,
            height=18,
            bg=SURFACE,
            bd=0,
            highlightthickness=0,
            cursor="hand2",
            takefocus=1,
        )
        self.canvas.pack(side="left")
        self.label = tk.Label(
            self,
            text=text,
            bg=SURFACE,
            fg=TEXT,
            font=font,
            cursor="hand2",
        )
        self.label.pack(side="left", padx=(5, 0))
        for widget in (self, self.canvas, self.label):
            widget.bind("<Button-1>", self._toggle)
            widget.bind("<Enter>", self._on_enter)
            widget.bind("<Leave>", self._on_leave)
        self.canvas.bind("<space>", self._toggle)
        self.canvas.bind("<Return>", self._toggle)
        self.variable.trace_add("write", lambda *_args: self._draw())
        self._draw()

    def _toggle(self, _event: tk.Event | None = None) -> str:
        if self._enabled:
            self.variable.set(not self.variable.get())
            self.canvas.focus_set()
        return "break"

    def _on_enter(self, _event: tk.Event | None = None) -> None:
        self._hovered = True
        if self._enabled:
            self.label.configure(fg=ACCENT)
        self._draw()

    def _on_leave(self, _event: tk.Event | None = None) -> None:
        self._hovered = False
        if self._enabled:
            self.label.configure(fg=TEXT)
        self._draw()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        cursor = "hand2" if enabled else "arrow"
        self.canvas.configure(cursor=cursor)
        self.label.configure(
            fg=TEXT if enabled else "#A5B2C2",
            cursor=cursor,
        )
        self._draw()

    def _draw(self) -> None:
        self.canvas.delete("all")
        checked = self.variable.get()
        outline = "#9AACBF" if self._enabled else "#C7D1DC"
        border_width = 2 if self._hovered and self._enabled else 1
        fill = ACCENT if checked else "#FBFDFF"
        if checked and not self._enabled:
            fill = "#9DB7E8"
        self.canvas.create_rectangle(
            2,
            2,
            16,
            16,
            fill=fill,
            outline=outline if not checked else fill,
            width=border_width,
        )
        if checked:
            self.canvas.create_line(
                5,
                9,
                8,
                12,
                14,
                5,
                fill="#FFFFFF",
                width=2,
                capstyle="round",
                joinstyle="round",
            )


class MergerApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.app_dir = get_app_dir()
        self.output_path = get_output_path(self.app_dir)
        self.config_path = get_config_path(self.app_dir)
        self.last_github_accelerate = False
        self.settings_path = get_settings_path(self.app_dir)
        self.settings_save_job: str | None = None
        self.current_ip_versions: frozenset[int] = frozenset({4})

        settings_result = load_settings(self.settings_path)
        self.ui_settings = settings_result.settings
        self.settings_error = settings_result.error
        if not settings_result.exists and self.settings_error is None:
            try:
                save_settings(self.settings_path, self.ui_settings)
            except Exception as exc:
                self.settings_error = f"无法创建程序参数文件：{exc}"

        self.sources: list[Source] = []
        self.row_runtime: dict[str, _RowRuntime] = {}
        self.dirty = False
        self.save_error_notified = False
        self.config_exists = False
        self.editing_index: int | None = None
        self.running = False
        self.last_report: RunReport | None = None
        self.event_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.worker_thread: threading.Thread | None = None
        self.stop_event = threading.Event()

        self.font_family = self._choose_font()
        self._configure_root()
        self._configure_styles()
        self._build_ui()
        self._bind_shortcuts()
        self._bind_settings_traces()
        self.load_config(initial=True)
        if self.settings_error:
            self._set_status(self.settings_error, "warning")
        elif not (self.ipv4_var.get() or self.ipv6_var.get()):
            self._set_status(
                "当前未选择任何 IP 类型，请勾选 IPv4 或 IPv6 后再合并。",
                "warning",
            )
        self.root.after(100, self._poll_events)

    # ------------------------------------------------------------------ 初始化

    def _choose_font(self) -> str:
        available = set(tkfont.families(self.root))
        for name in ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "Arial"):
            if name in available:
                return name
        return "TkDefaultFont"

    def _font(self, size: int, weight: str = "normal") -> tuple[str, int, str]:
        return (self.font_family, size, weight)

    def _configure_root(self) -> None:
        self.root.title(f"{APP_NAME} v{__version__}")
        self.root.configure(bg=BG)
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        width = min(1080, max(940, int(screen_width * 0.82)))
        height = min(740, max(640, screen_height - 110))
        x = max(0, (screen_width - width) // 2)
        y = max(0, (screen_height - height) // 2 - 12)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.minsize(940, 640)
        self.root.option_add("*Font", self._font(9))
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.report_callback_exception = self._report_callback_exception

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")

        style.configure(".", font=self._font(9), background=BG, foreground=TEXT)
        style.configure(
            "Primary.TButton",
            font=self._font(10, "bold"),
            background=ACCENT,
            foreground="#FFFFFF",
            padding=(18, 9),
            borderwidth=0,
            relief="flat",
            focuscolor=ACCENT,
        )
        style.map(
            "Primary.TButton",
            background=[
                ("disabled", "#AFC8EA"),
                ("pressed", "#1E40AF"),
                ("active", ACCENT_HOVER),
            ],
            foreground=[("disabled", "#F5F9FF")],
        )
        style.configure(
            "Stop.TButton",
            font=self._font(10, "bold"),
            background="#DC2626",
            foreground="#FFFFFF",
            padding=(18, 9),
            borderwidth=0,
            relief="flat",
            focuscolor="#DC2626",
        )
        style.map(
            "Stop.TButton",
            background=[
                ("disabled", "#F0A3A3"),
                ("pressed", "#991B1B"),
                ("active", "#B91C1C"),
            ],
            foreground=[("disabled", "#FFF5F5")],
        )
        style.configure(
            "Secondary.TButton",
            font=self._font(9),
            background="#EAF0F8",
            foreground=TEXT,
            padding=(12, 7),
            borderwidth=0,
            relief="flat",
            focuscolor="#EAF0F8",
        )
        style.map(
            "Secondary.TButton",
            background=[
                ("disabled", "#F1F4F8"),
                ("pressed", "#D8E5F4"),
                ("active", "#DFE9F6"),
            ],
            foreground=[("disabled", "#A5B2C2")],
        )
        style.configure(
            "Danger.TButton",
            font=self._font(9),
            background="#FEF2F2",
            foreground=RED,
            padding=(12, 6),
            borderwidth=0,
            relief="flat",
            focuscolor="#FEF2F2",
        )
        style.map(
            "Danger.TButton",
            background=[
                ("disabled", "#F7F8FA"),
                ("active", "#FEE2E2"),
                ("pressed", "#FECACA"),
            ],
            foreground=[("disabled", "#B9C2CE")],
        )
        style.configure(
            "Toolbar.TButton",
            font=self._font(9),
            background=SURFACE,
            foreground=TEXT,
            padding=(9, 6),
            borderwidth=0,
            relief="flat",
            focuscolor=SURFACE,
        )
        style.map(
            "Toolbar.TButton",
            background=[
                ("disabled", SURFACE),
                ("active", "#F1F6FD"),
                ("pressed", "#E6EFFA"),
            ],
            foreground=[("disabled", "#B3BFCE")],
        )
        style.configure(
            "White.TCheckbutton",
            background=SURFACE,
            foreground=TEXT,
            font=self._font(9),
            focuscolor=SURFACE,
        )
        style.map(
            "White.TCheckbutton",
            background=[("active", SURFACE)],
            foreground=[("disabled", "#A1ADBD")],
        )
        style.configure(
            "Sources.Treeview",
            background=SURFACE,
            fieldbackground=SURFACE,
            foreground=TEXT,
            rowheight=34,
            borderwidth=0,
            font=self._font(9),
        )
        style.map(
            "Sources.Treeview",
            background=[("selected", "#E5EEFC")],
            foreground=[("selected", "#13315C")],
        )
        style.configure(
            "Sources.Treeview.Heading",
            background="#F7F9FD",
            foreground=MUTED,
            font=self._font(9, "bold"),
            padding=(8, 8),
            relief="flat",
            borderwidth=0,
        )
        style.map(
            "Sources.Treeview.Heading",
            background=[("active", "#F1F5FB")],
        )
        style.configure(
            "Progress.Horizontal.TProgressbar",
            troughcolor="#DCE7F2",
            background=ACCENT,
            lightcolor=ACCENT,
            darkcolor=ACCENT,
            borderwidth=0,
            thickness=6,
        )

    def _build_ui(self) -> None:
        self._build_header()

        self.footer = tk.Frame(self.root, bg="#EAF1F8", height=28)
        self.footer.pack(side="bottom", fill="x")
        self.footer.pack_propagate(False)
        self._build_footer()

        self.body = tk.Frame(self.root, bg=BG)
        self.body.pack(fill="both", expand=True, padx=20, pady=(14, 10))

        self._build_stats()
        self._build_editor()
        self._build_action_bar()
        self._build_source_list()
        self._update_selection_buttons()

    def _build_header(self) -> None:
        header = tk.Frame(self.root, bg=NAVY, height=82)
        header.pack(fill="x")
        header.pack_propagate(False)

        left = tk.Frame(header, bg=NAVY)
        left.pack(side="left", padx=22, pady=15)
        logo = tk.Canvas(
            left,
            width=46,
            height=46,
            bg=NAVY,
            highlightthickness=0,
            bd=0,
        )
        logo.pack(side="left", padx=(0, 13))
        self._rounded_rect(logo, 1, 1, 45, 45, 11, ACCENT)
        logo.create_text(
            23,
            23,
            text="CF",
            fill="#FFFFFF",
            font=self._font(15, "bold"),
        )
        text_block = tk.Frame(left, bg=NAVY)
        text_block.pack(side="left")
        tk.Label(
            text_block,
            text=APP_NAME,
            bg=NAVY,
            fg="#FFFFFF",
            font=self._font(17, "bold"),
        ).pack(anchor="w")
        tk.Label(
            text_block,
            text="集中管理来源，一键合并为 优选合集.txt",
            bg=NAVY,
            fg=HEADER_SUBTLE,
            font=self._font(9),
        ).pack(anchor="w", pady=(3, 0))

        right = tk.Frame(header, bg=NAVY)
        right.pack(side="right", padx=22, pady=16)
        local_row = tk.Frame(right, bg=NAVY)
        local_row.pack(anchor="e")
        tk.Label(
            local_row,
            text="●",
            bg=NAVY,
            fg="#4ADE80",
            font=self._font(8),
        ).pack(side="left", padx=(0, 5))
        tk.Label(
            local_row,
            text="全程本地处理",
            bg=NAVY,
            fg="#D8E8F4",
            font=self._font(9, "bold"),
        ).pack(side="left")
        tk.Label(
            right,
            text=f"配置与结果不上传  ·  v{__version__}",
            bg=NAVY,
            fg=HEADER_SUBTLE,
            font=self._font(8),
        ).pack(anchor="e", pady=(5, 0))

        accent_line = tk.Frame(self.root, bg="#3B82F6", height=2)
        accent_line.pack(fill="x")

    def _build_footer(self) -> None:
        status_left = tk.Frame(self.footer, bg="#EAF1F8")
        status_left.pack(side="left", fill="x", expand=True, padx=12)
        self.status_dot = tk.Label(
            status_left,
            text="●",
            bg="#EAF1F8",
            fg=GREEN,
            font=self._font(8),
        )
        self.status_dot.pack(side="left", padx=(0, 6))
        self.status_var = tk.StringVar(value="准备就绪")
        self.status_label = tk.Label(
            status_left,
            textvariable=self.status_var,
            bg="#EAF1F8",
            fg="#53657C",
            font=self._font(8),
            anchor="w",
        )
        self.status_label.pack(side="left", fill="x", expand=True)

        right = tk.Frame(self.footer, bg="#EAF1F8")
        right.pack(side="right", padx=12)

        # 右下角常驻入口：点击打开 GitHub 项目主页
        self.footer_github = tk.Label(
            right,
            text="GitHub项目",
            bg="#EAF1F8",
            fg=ACCENT,
            font=self._font(8, "underline"),
            cursor="hand2",
        )
        self.footer_github.pack(side="right", padx=(12, 0))
        self.footer_github.bind("<Button-1>", self._open_project_page)

        tk.Label(
            right,
            text="·",
            bg="#EAF1F8",
            fg="#A9B8C9",
            font=self._font(8),
        ).pack(side="right", padx=(8, 0))
        self.footer_version = tk.Label(
            right,
            text="本地处理 · 不上传数据",
            bg="#EAF1F8",
            fg="#7A8DA2",
            font=self._font(8),
        )
        self.footer_version.pack(side="right")
        self.progress = ttk.Progressbar(
            right,
            style="Progress.Horizontal.TProgressbar",
            length=135,
            mode="determinate",
        )

    def _build_stats(self) -> None:
        stats = tk.Frame(self.body, bg=BG)
        stats.pack(fill="x", pady=(0, 10))
        cards = [
            ("来源总数", "0", ACCENT),
            ("已启用", "0", GREEN),
            ("本轮成功", "—", AMBER),
            ("合并行数", "—", "#7C3AED"),
        ]
        self.stat_values: dict[str, tk.Label] = {}
        for column, (label, initial, color) in enumerate(cards):
            stats.grid_columnconfigure(column, weight=1, uniform="stats")
            card = tk.Frame(
                stats,
                bg=SURFACE,
                highlightbackground=BORDER,
                highlightthickness=1,
                bd=0,
            )
            card.grid(
                row=0,
                column=column,
                sticky="nsew",
                padx=(0 if column == 0 else 5, 0 if column == 3 else 5),
            )
            stripe = tk.Frame(card, bg=color, width=3)
            stripe.pack(side="left", fill="y")
            content = tk.Frame(card, bg=SURFACE)
            content.pack(side="left", fill="both", expand=True, padx=12, pady=8)
            tk.Label(
                content,
                text=label,
                bg=SURFACE,
                fg=MUTED,
                font=self._font(8),
            ).pack(anchor="w")
            value = tk.Label(
                content,
                text=initial,
                bg=SURFACE,
                fg=NAVY,
                font=self._font(18, "bold"),
            )
            value.pack(anchor="w", pady=(1, 0))
            self.stat_values[label] = value

    def _build_editor(self) -> None:
        card = self._make_card(self.body)
        card.pack(fill="x", pady=(0, 10))

        inner = tk.Frame(card, bg=SURFACE)
        inner.pack(fill="x", padx=15, pady=11)

        heading_row = tk.Frame(inner, bg=SURFACE)
        heading_row.pack(fill="x", pady=(0, 8))
        tk.Label(
            heading_row,
            text="添加来源",
            bg=SURFACE,
            fg=TEXT,
            font=self._font(10, "bold"),
        ).pack(side="left")
        tk.Label(
            heading_row,
            text="支持 http / https，备注可留空",
            bg=SURFACE,
            fg=SUBTLE,
            font=self._font(8),
        ).pack(side="left", padx=(9, 0))

        row = tk.Frame(inner, bg=SURFACE)
        row.pack(fill="x")

        self.add_button = ttk.Button(
            row,
            text="添加到列表",
            style="Primary.TButton",
            command=self._submit_editor,
        )
        self.add_button.pack(side="right")
        self.clear_button = ttk.Button(
            row,
            text="清空",
            style="Secondary.TButton",
            command=self._clear_editor,
        )
        self.clear_button.pack(side="right", padx=(0, 8))
        self.cancel_edit_button = ttk.Button(
            row,
            text="取消修改",
            style="Toolbar.TButton",
            command=self._clear_editor,
        )

        tk.Label(
            row,
            text="名称",
            bg=SURFACE,
            fg=MUTED,
            font=self._font(9),
        ).pack(side="left", padx=(0, 6))
        self.name_var = tk.StringVar()
        self.name_entry = self._entry(row, self.name_var, width=19)
        self.name_entry.pack(side="left", ipady=7, padx=(0, 12))

        tk.Label(
            row,
            text="URL",
            bg=SURFACE,
            fg=MUTED,
            font=self._font(9),
        ).pack(side="left", padx=(0, 6))
        self.url_var = tk.StringVar()
        self.url_entry = self._entry(row, self.url_var)
        self.url_entry.pack(side="left", fill="x", expand=True, ipady=7)
        self.url_entry.bind("<Return>", lambda _event: self._submit_editor())
        self.name_entry.bind("<Return>", lambda _event: self._submit_editor())

    def _build_source_list(self) -> None:
        card = self._make_card(self.body)
        card.pack(fill="both", expand=True)

        header = tk.Frame(card, bg=SURFACE)
        header.pack(fill="x", padx=15, pady=(10, 7))
        title_row = tk.Frame(header, bg=SURFACE)
        title_row.pack(side="left")
        tk.Label(
            title_row,
            text="来源列表",
            bg=SURFACE,
            fg=TEXT,
            font=self._font(10, "bold"),
        ).pack(side="left")
        self.list_count_var = tk.StringVar(value="0 条来源")
        tk.Label(
            title_row,
            textvariable=self.list_count_var,
            bg=SURFACE,
            fg=SUBTLE,
            font=self._font(8),
        ).pack(side="left", padx=(9, 0))

        toolbar = tk.Frame(header, bg=SURFACE)
        toolbar.pack(side="right")
        self.edit_button = self._toolbar_button(
            toolbar, "编辑", self._edit_selected
        )
        self.toggle_button = self._toolbar_button(
            toolbar, "启用 / 停用", self._toggle_selected
        )
        self.up_button = self._toolbar_button(toolbar, "↑ 上移", self._move_up)
        self.down_button = self._toolbar_button(
            toolbar, "↓ 下移", self._move_down
        )
        self.delete_button = ttk.Button(
            toolbar,
            text="删除",
            style="Danger.TButton",
            command=self._delete_selected,
        )
        self.delete_button.pack(side="left", padx=(4, 0))
        self.toolbar_buttons = [
            self.edit_button,
            self.toggle_button,
            self.up_button,
            self.down_button,
            self.delete_button,
        ]

        table_wrap = tk.Frame(card, bg=SURFACE)
        table_wrap.pack(fill="both", expand=True, padx=15, pady=(0, 10))
        table_wrap.grid_rowconfigure(0, weight=1)
        table_wrap.grid_columnconfigure(0, weight=1)

        columns = ("seq", "enabled", "name", "url", "state", "lines")
        self.tree = ttk.Treeview(
            table_wrap,
            columns=columns,
            show="headings",
            style="Sources.Treeview",
            selectmode="extended",
        )
        headings = {
            "seq": "序号",
            "enabled": "启用",
            "name": "名称",
            "url": "URL",
            "state": "状态",
            "lines": "行数",
        }
        self.tree.heading("seq", text=headings["seq"])
        self.tree.heading("enabled", text=headings["enabled"])
        self.tree.heading("name", text=headings["name"], anchor="w")
        self.tree.heading("url", text=headings["url"], anchor="w")
        self.tree.heading("state", text=headings["state"])
        self.tree.heading("lines", text=headings["lines"])
        self.tree.column("seq", width=48, minwidth=44, stretch=False, anchor="center")
        self.tree.column(
            "enabled", width=70, minwidth=64, stretch=False, anchor="center"
        )
        self.tree.column("name", width=170, minwidth=120, stretch=False, anchor="w")
        self.tree.column("url", width=470, minwidth=220, stretch=True, anchor="w")
        self.tree.column(
            "state", width=108, minwidth=90, stretch=False, anchor="center"
        )
        self.tree.column("lines", width=66, minwidth=56, stretch=False, anchor="center")
        self.tree.tag_configure("disabled", foreground="#94A3B8")
        self.tree.tag_configure("success", foreground=GREEN)
        self.tree.tag_configure("warning", foreground=AMBER)
        self.tree.tag_configure("failure", foreground=RED)
        self.tree.tag_configure("working", foreground=ACCENT)
        self.tree.tag_configure("stopped", foreground=AMBER)
        self.tree.grid(row=0, column=0, sticky="nsew")

        vertical = ttk.Scrollbar(table_wrap, orient="vertical", command=self.tree.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(
            table_wrap, orient="horizontal", command=self.tree.xview
        )
        horizontal.grid(row=1, column=0, sticky="ew", pady=(3, 0))
        self.tree.configure(
            yscrollcommand=vertical.set,
            xscrollcommand=horizontal.set,
        )

        self.empty_overlay = tk.Label(
            self.tree,
            text="还没有来源\n在上方添加 URL，或从 URL.txt 重新加载",
            bg=SURFACE,
            fg=SUBTLE,
            font=self._font(10),
            justify="center",
        )
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Double-1>", self._on_tree_double_click)
        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<Button-3>", self._show_context_menu)

        self.context_menu = tk.Menu(self.root, tearoff=False)
        self.context_menu.add_command(label="编辑此来源", command=self._context_edit)
        self.context_menu.add_command(
            label="启用 / 停用", command=self._context_toggle
        )
        self.context_menu.add_command(label="复制 URL", command=self._context_copy)
        self.context_menu.add_separator()
        self.context_menu.add_command(
            label="查看运行详情", command=self._context_detail
        )

    def _build_action_bar(self) -> None:
        card = self._make_card(self.body)
        card.pack(side="bottom", fill="x", pady=(10, 0))
        inner = tk.Frame(card, bg=SURFACE)
        inner.pack(fill="x", padx=15, pady=10)

        config_row = tk.Frame(inner, bg=SURFACE)
        config_row.pack(fill="x")
        output_row = tk.Frame(inner, bg=SURFACE)
        output_row.pack(fill="x", pady=(8, 0))

        self.start_button = ttk.Button(
            output_row,
            text="开始合并  →",
            style="Primary.TButton",
            command=self._start_merge,
        )
        self.start_button.pack(side="right")

        filter_group = tk.Frame(output_row, bg=SURFACE)
        filter_group.pack(side="left")
        tk.Label(
            filter_group,
            text="输出筛选",
            bg=SURFACE,
            fg=MUTED,
            font=self._font(8),
        ).pack(side="left", padx=(0, 9))

        self.dedupe_var = tk.BooleanVar(value=self.ui_settings.deduplicate)
        self.dedupe_check = _CheckOption(
            filter_group,
            "整行精确去重",
            self.dedupe_var,
            self._font(9),
        )
        self.dedupe_check.pack(side="left", padx=(0, 13))

        self.ipv4_var = tk.BooleanVar(value=self.ui_settings.ipv4)
        self.ipv4_check = _CheckOption(
            filter_group,
            "IPv4",
            self.ipv4_var,
            self._font(9),
        )
        self.ipv4_check.pack(side="left", padx=(0, 11))

        self.ipv6_var = tk.BooleanVar(value=self.ui_settings.ipv6)
        self.ipv6_check = _CheckOption(
            filter_group,
            "IPv6",
            self.ipv6_var,
            self._font(9),
        )
        self.ipv6_check.pack(side="left", padx=(0, 11))

        self.github_var = tk.BooleanVar(value=self.ui_settings.github_accelerate)
        self.github_check = _CheckOption(
            filter_group,
            "GitHub 加速",
            self.github_var,
            self._font(9),
        )
        self.github_check.pack(side="left")
        self.filter_hint_label = tk.Label(
            filter_group,
            text="",
            bg=SURFACE,
            fg=AMBER,
            font=self._font(8),
        )
        self.filter_hint_label.pack(side="left", padx=(12, 0))

        self.open_dir_button = ttk.Button(
            config_row,
            text="打开输出目录",
            style="Secondary.TButton",
            command=self._open_output_dir,
        )
        self.open_dir_button.pack(side="right")

        left = tk.Frame(config_row, bg=SURFACE)
        left.pack(side="left", fill="x", expand=True)
        tk.Label(
            left,
            text="配置",
            bg=SURFACE,
            fg=MUTED,
            font=self._font(8),
        ).pack(side="left")
        self.config_name_label = tk.Label(
            left,
            text="URL.txt",
            bg=SURFACE,
            fg=TEXT,
            font=self._font(9, "bold"),
        )
        self.config_name_label.pack(side="left", padx=(5, 8))

        self.reload_button = ttk.Button(
            left,
            text="重新加载",
            style="Secondary.TButton",
            command=self._reload_clicked,
        )
        self.reload_button.pack(side="left")
        self.open_config_button = ttk.Button(
            left,
            text="打开配置",
            style="Toolbar.TButton",
            command=self._open_config,
        )
        self.open_config_button.pack(side="left", padx=(4, 0))

        self.dirty_label = tk.Label(
            left,
            text="",
            bg=SURFACE,
            fg=GREEN,
            font=self._font(8),
        )
        self.dirty_label.pack(side="left", padx=(10, 0))
        self.dirty_label.bind("<Button-1>", lambda _event: self._retry_auto_save())

    # ------------------------------------------------------------------ 通用控件

    def _make_card(self, parent: tk.Misc) -> tk.Frame:
        return tk.Frame(
            parent,
            bg=SURFACE,
            highlightbackground=BORDER,
            highlightthickness=1,
            bd=0,
        )

    def _entry(
        self,
        parent: tk.Misc,
        variable: tk.StringVar,
        width: int | None = None,
    ) -> tk.Entry:
        return tk.Entry(
            parent,
            textvariable=variable,
            width=width or 20,
            bg="#FBFDFF",
            fg=TEXT,
            disabledbackground="#F1F5F9",
            disabledforeground=SUBTLE,
            insertbackground=TEXT,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
            font=self._font(10),
        )

    def _toolbar_button(
        self,
        parent: tk.Misc,
        text: str,
        command: Callable[[], None],
    ) -> ttk.Button:
        button = ttk.Button(
            parent,
            text=text,
            style="Toolbar.TButton",
            command=command,
        )
        button.pack(side="left", padx=(4, 0))
        return button

    @staticmethod
    def _rounded_rect(
        canvas: tk.Canvas,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        radius: float,
        color: str,
    ) -> None:
        points = [
            x1 + radius,
            y1,
            x2 - radius,
            y1,
            x2,
            y1,
            x2,
            y1 + radius,
            x2,
            y2 - radius,
            x2,
            y2,
            x2 - radius,
            y2,
            x1 + radius,
            y2,
            x1,
            y2,
            x1,
            y2 - radius,
            x1,
            y1 + radius,
            x1,
            y1,
        ]
        canvas.create_polygon(
            points,
            smooth=True,
            splinesteps=36,
            fill=color,
            outline="",
        )

    # ------------------------------------------------------------------ 配置

    def load_config(self, initial: bool = False, notify: bool = True) -> bool:
        try:
            self.config_path = get_config_path(self.app_dir)
            result = load_config(self.config_path)
        except ConfigError as exc:
            messagebox.showerror("配置读取失败", str(exc), parent=self.root)
            self._set_status(f"配置读取失败：{exc}", "error")
            return False

        self.sources = result.sources
        self.config_exists = result.exists
        self.row_runtime.clear()
        self.last_report = None
        self._clear_editor()
        self.dirty = False
        self.save_error_notified = False
        self.editing_index = None
        self._refresh_table()
        self._refresh_stats()
        self._update_dirty_badge()
        self._update_config_name()
        self._refresh_result_stats(None)

        if not result.exists:
            self._set_status(
                "未找到 URL.txt，可添加来源后保存，程序会自动创建配置。",
                "warning",
            )
        elif initial:
            self._set_status(
                f"已加载 {len(self.sources)} 条来源，配置就绪。",
                "success",
            )
        else:
            self._set_status(
                f"已重新加载 {len(self.sources)} 条来源。",
                "success",
            )

        issues = self._format_config_issues(result)
        if notify and issues:
            self._show_details_dialog(
                "配置读取提示",
                f"已加载 {len(result.sources)} 条来源，"
                f"{len(result.errors)} 行无效，{len(result.warnings)} 条提醒。",
                issues,
            )
        return True

    @staticmethod
    def _format_config_issues(result: ConfigLoadResult) -> str:
        blocks: list[str] = []
        if result.errors:
            blocks.append("【无效行，已跳过】")
            for issue in result.errors:
                prefix = f"第 {issue.line_number} 行" if issue.line_number else "文件"
                blocks.append(f"{prefix}：{issue.message}")
                if issue.line_text:
                    blocks.append(f"  {issue.line_text}")
            blocks.append("")
        if result.warnings:
            blocks.append("【提醒】")
            blocks.extend(result.warnings)
            blocks.append("")
        if result.encoding != "utf-8":
            blocks.append(f"文件编码：{result.encoding}（保存时会转为 UTF-8）")
        return "\n".join(blocks).strip()

    def _update_config_name(self) -> None:
        self.config_name_label.configure(text=self.config_path.name)

    def _persist_sources(self) -> bool:
        """将当前来源写入 URL.txt，返回是否成功。"""
        if self.running:
            return False
        try:
            save_config(self.config_path, self.sources)
        except ConfigError as exc:
            self.dirty = True
            self._update_dirty_badge()
            self._set_status(
                f"自动保存失败：{exc} 修复后可点击状态文字或按 Ctrl+S 重试。",
                "error",
            )
            if not self.save_error_notified:
                messagebox.showerror(
                    "自动保存失败",
                    f"{exc}\n\n修改仍保留在当前列表中。\n"
                    "修复目录权限后，可点击左下角状态文字或按 Ctrl+S 重试。",
                    parent=self.root,
                )
                self.save_error_notified = True
            return False

        self.config_exists = True
        self.dirty = False
        self.save_error_notified = False
        self._update_dirty_badge()
        self._update_config_name()
        return True

    def _save_current(self, show_message: bool = True) -> bool:
        if not self._persist_sources():
            return False
        if show_message:
            self._set_status(
                f"URL.txt 已更新（{len(self.sources)} 条来源）。",
                "success",
            )
        return True

    def _auto_save_sources(self, action_text: str) -> bool:
        """来源改动后立即写入 URL.txt。"""
        self.dirty = True
        if not self._persist_sources():
            return False
        self._set_status(f"{action_text}，已自动更新 URL.txt。", "success")
        return True

    def _save_clicked(self) -> None:
        self._save_current(show_message=True)

    def _retry_auto_save(self) -> None:
        if not self.dirty or self.running:
            return
        self._save_current(show_message=True)

    def _reload_clicked(self) -> None:
        if self.running:
            return
        if self.dirty:
            answer = messagebox.askyesno(
                "URL.txt 尚未更新",
                "当前来源列表的自动保存失败，重新加载会丢弃这些修改，是否继续？",
                parent=self.root,
            )
            if not answer:
                return
        self.load_config()

    def _open_config(self) -> None:
        if self.running:
            return
        if not self.config_exists or self.dirty:
            if not self._save_current(show_message=False):
                return
        try:
            open_local_path(self.config_path)
            self._set_status(
                f"已打开 {self.config_path.name}。外部修改后请点击“重新加载”。",
                "info",
            )
        except OSError as exc:
            messagebox.showerror("无法打开配置", str(exc), parent=self.root)

    def _update_dirty_badge(self) -> None:
        if self.dirty:
            self.dirty_label.configure(
                text="● 自动保存失败，点击重试",
                fg=RED,
                cursor="hand2",
            )
        elif self.config_exists:
            self.dirty_label.configure(
                text="● 修改已自动保存",
                fg=GREEN,
                cursor="",
            )
        else:
            self.dirty_label.configure(
                text="● 尚未创建配置",
                fg=SUBTLE,
                cursor="",
            )

    # ------------------------------------------------------------------ 编辑来源

    def _submit_editor(self) -> None:
        if self.running:
            return
        url = self.url_var.get().strip()
        name = self.name_var.get().strip()
        problem = validate_url(url)
        if problem:
            messagebox.showwarning("URL 格式不正确", problem, parent=self.root)
            self.url_entry.focus_set()
            return

        duplicate_index = next(
            (
                index
                for index, source in enumerate(self.sources)
                if source.url == url and index != self.editing_index
            ),
            None,
        )
        if duplicate_index is not None:
            messagebox.showwarning(
                "URL 已存在",
                f"该 URL 已在列表第 {duplicate_index + 1} 条中，不能重复添加。",
                parent=self.root,
            )
            return

        if self.editing_index is not None:
            index = self.editing_index
            old_url = self.sources[index].url
            if old_url != url:
                self.row_runtime.pop(old_url, None)
            self.sources[index].url = url
            self.sources[index].name = name
            self._refresh_table(select_indices=[index])
            action_text = f"已更新第 {index + 1} 条来源"
        else:
            self.sources.append(Source(url=url, name=name, enabled=True))
            new_index = len(self.sources) - 1
            self._refresh_table(select_indices=[new_index])
            self.tree.see(str(new_index))
            action_text = f"已添加来源“{self.sources[-1].display_name(len(self.sources))}”"

        self._refresh_stats()
        self._auto_save_sources(action_text)
        self._clear_editor()

    def _clear_editor(self) -> None:
        self.editing_index = None
        self.name_var.set("")
        self.url_var.set("")
        self.add_button.configure(text="添加到列表")
        if self.cancel_edit_button.winfo_manager():
            self.cancel_edit_button.pack_forget()
        if hasattr(self, "url_entry") and not self.running:
            self.url_entry.focus_set()

    def _begin_edit(self, index: int) -> None:
        if self.running or not 0 <= index < len(self.sources):
            return
        source = self.sources[index]
        self.editing_index = index
        self.name_var.set(source.name)
        self.url_var.set(source.url)
        self.add_button.configure(text="保存修改")
        if not self.cancel_edit_button.winfo_manager():
            self.cancel_edit_button.pack(side="right", padx=(0, 8), before=self.clear_button)
        self.tree.selection_set(str(index))
        self.tree.see(str(index))
        self.url_entry.focus_set()
        self._set_status(f"正在编辑第 {index + 1} 条来源。", "info")

    def _edit_selected(self) -> None:
        selected = self._selected_indices()
        if len(selected) == 1:
            self._begin_edit(selected[0])

    def _delete_selected(self) -> None:
        if self.running:
            return
        selected = self._selected_indices()
        if not selected:
            return
        names = "、".join(
            f"“{self.sources[index].display_name(index + 1)}”"
            for index in selected[:4]
        )
        if len(selected) > 4:
            names += f" 等 {len(selected)} 条"
        if not messagebox.askyesno(
            "删除来源",
            f"确定删除 {names}？\n删除后会自动更新 URL.txt。",
            parent=self.root,
        ):
            return

        for index in reversed(selected):
            removed = self.sources.pop(index)
            self.row_runtime.pop(removed.url, None)
        self._clear_editor()
        self._refresh_table()
        self._refresh_stats()
        self._auto_save_sources(f"已删除 {len(selected)} 条来源")

    def _toggle_selected(self) -> None:
        selected = self._selected_indices()
        self._toggle_indices(selected)

    def _toggle_indices(self, indices: list[int]) -> None:
        if self.running or not indices:
            return
        target = not all(self.sources[index].enabled for index in indices)
        for index in indices:
            self.sources[index].enabled = target
        self._refresh_table(select_indices=indices)
        self._refresh_stats()
        action = "启用" if target else "停用"
        self._auto_save_sources(f"已{action} {len(indices)} 条来源")

    def _move_up(self) -> None:
        selected = self._selected_indices()
        if self.running or len(selected) != 1 or selected[0] == 0:
            return
        if self.editing_index is not None:
            self._clear_editor()
        index = selected[0]
        self.sources[index - 1], self.sources[index] = (
            self.sources[index],
            self.sources[index - 1],
        )
        self._refresh_table(select_indices=[index - 1])
        self._auto_save_sources("已上移来源")

    def _move_down(self) -> None:
        selected = self._selected_indices()
        if self.running or len(selected) != 1 or selected[0] >= len(self.sources) - 1:
            return
        if self.editing_index is not None:
            self._clear_editor()
        index = selected[0]
        self.sources[index + 1], self.sources[index] = (
            self.sources[index],
            self.sources[index + 1],
        )
        self._refresh_table(select_indices=[index + 1])
        self._auto_save_sources("已下移来源")

    # ------------------------------------------------------------------ 表格

    def _selected_indices(self) -> list[int]:
        result: list[int] = []
        for iid in self.tree.selection():
            try:
                index = int(iid)
            except ValueError:
                continue
            if 0 <= index < len(self.sources):
                result.append(index)
        return sorted(result)

    def _on_tree_select(self, _event: tk.Event | None = None) -> None:
        self._update_selection_buttons()

    def _update_selection_buttons(self) -> None:
        selected = self._selected_indices()
        has_selection = bool(selected)
        single = len(selected) == 1
        index = selected[0] if single else None
        states = {
            self.edit_button: single,
            self.toggle_button: has_selection,
            self.delete_button: has_selection,
            self.up_button: index is not None and index > 0,
            self.down_button: index is not None and index < len(self.sources) - 1,
        }
        for button, enabled in states.items():
            should_enable = enabled and not self.running
            button.configure(state="normal" if should_enable else "disabled")

    def _row_values(self, index: int) -> tuple[str, str, str, str, str, str]:
        source = self.sources[index]
        runtime = self.row_runtime.get(source.url)
        if not source.enabled:
            state = "已停用"
            line_count = "—"
        else:
            state = runtime.state if runtime else "待合并"
            line_count = (
                str(runtime.lines)
                if runtime and runtime.lines is not None
                else "—"
            )
        return (
            f"{index + 1:02d}",
            "● 启用" if source.enabled else "○ 停用",
            source.display_name(index + 1),
            source.url,
            state,
            line_count,
        )

    def _row_tags(self, index: int) -> tuple[str, ...]:
        source = self.sources[index]
        if not source.enabled:
            return ("disabled",)
        runtime = self.row_runtime.get(source.url)
        if not runtime:
            return ()
        if runtime.state == "失败":
            return ("failure",)
        if runtime.state.startswith("成功"):
            return (
                ("warning",)
                if "警告" in runtime.state or "无匹配" in runtime.state
                else ("success",)
            )
        if runtime.state in {"等待", "下载中"}:
            return ("working",)
        if runtime.state == "已停止":
            return ("stopped",)
        return ()

    def _refresh_table(self, select_indices: list[int] | None = None) -> None:
        selected = set(select_indices or [])
        try:
            scroll_top = self.tree.yview()[0]
        except tk.TclError:
            scroll_top = 0.0
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        for index in range(len(self.sources)):
            self.tree.insert(
                "",
                "end",
                iid=str(index),
                values=self._row_values(index),
                tags=self._row_tags(index),
            )
        for index in sorted(selected):
            if 0 <= index < len(self.sources):
                self.tree.selection_add(str(index))
        if self.sources:
            self.empty_overlay.place_forget()
        else:
            self.empty_overlay.place(relx=0.5, rely=0.55, anchor="center")
        try:
            self.tree.yview_moveto(scroll_top)
        except tk.TclError:
            pass
        enabled_count = sum(1 for source in self.sources if source.enabled)
        self.list_count_var.set(
            f"{len(self.sources)} 条来源 · {enabled_count} 条启用"
        )
        self._update_selection_buttons()

    def _update_row_by_url(self, url: str) -> None:
        for index, source in enumerate(self.sources):
            if source.url == url:
                self.tree.item(
                    str(index),
                    values=self._row_values(index),
                    tags=self._row_tags(index),
                )
                break

    def _on_tree_click(self, event: tk.Event) -> None:
        if self.running:
            return
        region = self.tree.identify_region(event.x, event.y)
        column = self.tree.identify_column(event.x)
        row = self.tree.identify_row(event.y)
        if region == "cell" and column == "#2" and row:
            self.tree.selection_set(row)
            self._toggle_indices([int(row)])

    def _on_tree_double_click(self, event: tk.Event) -> str | None:
        if self.running:
            return None
        column = self.tree.identify_column(event.x)
        row = self.tree.identify_row(event.y)
        if column == "#2":
            return "break"
        if row:
            self._begin_edit(int(row))
            return "break"
        return None

    def _show_context_menu(self, event: tk.Event) -> None:
        row = self.tree.identify_row(event.y)
        if not row:
            return
        self.tree.selection_set(row)
        self.context_row = int(row)
        self._update_context_menu()
        try:
            self.context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.context_menu.grab_release()

    def _update_context_menu(self) -> None:
        index = getattr(self, "context_row", None)
        if index is None or not 0 <= index < len(self.sources):
            return
        runtime = self.row_runtime.get(self.sources[index].url)
        detail_state = "normal" if runtime and runtime.detail else "disabled"
        self.context_menu.entryconfigure("编辑此来源", state="disabled" if self.running else "normal")
        self.context_menu.entryconfigure(
            "启用 / 停用", state="disabled" if self.running else "normal"
        )
        self.context_menu.entryconfigure("复制 URL", state="normal")
        self.context_menu.entryconfigure("查看运行详情", state=detail_state)

    def _context_edit(self) -> None:
        self._begin_edit(getattr(self, "context_row", -1))

    def _context_toggle(self) -> None:
        self._toggle_indices([getattr(self, "context_row", -1)])

    def _context_copy(self) -> None:
        index = getattr(self, "context_row", -1)
        if 0 <= index < len(self.sources):
            self.root.clipboard_clear()
            self.root.clipboard_append(self.sources[index].url)
            self._set_status("URL 已复制到剪贴板。", "info")

    def _context_detail(self) -> None:
        index = getattr(self, "context_row", -1)
        if 0 <= index < len(self.sources):
            runtime = self.row_runtime.get(self.sources[index].url)
            if runtime and runtime.detail:
                self._show_details_dialog(
                    "来源运行详情",
                    f"{self.sources[index].display_name(index + 1)}",
                    runtime.detail,
                )

    # ------------------------------------------------------------------ 统计和状态

    def _refresh_stats(self) -> None:
        total = len(self.sources)
        enabled = sum(1 for source in self.sources if source.enabled)
        self.stat_values["来源总数"].configure(text=str(total))
        self.stat_values["已启用"].configure(text=str(enabled))

    def _refresh_result_stats(self, report: RunReport | None) -> None:
        if report and report.written:
            self.stat_values["本轮成功"].configure(
                text=f"{report.success_count}/{report.active_sources}"
            )
            self.stat_values["合并行数"].configure(text=f"{report.total_lines:,}")
        elif report:
            self.stat_values["本轮成功"].configure(
                text=f"{report.success_count}/{report.active_sources}"
            )
            self.stat_values["合并行数"].configure(text="0")
        else:
            self.stat_values["本轮成功"].configure(text="—")
            self.stat_values["合并行数"].configure(text="—")

    def _set_status(self, text: str, kind: str = "info") -> None:
        colors = {
            "info": ("#53657C", ACCENT),
            "success": ("#446156", GREEN),
            "warning": ("#7A5A28", "#D97706"),
            "error": ("#8C3730", RED),
        }
        foreground, dot = colors.get(kind, colors["info"])
        self.status_var.set(text)
        self.status_label.configure(fg=foreground)
        self.status_dot.configure(fg=dot)

    # ------------------------------------------------------------------ 异步合并

    def _start_merge(self) -> None:
        if self.running:
            return
        if not self.sources:
            messagebox.showwarning(
                "没有来源",
                "请先添加至少一个 URL，或点击“重新加载”读取 URL.txt。",
                parent=self.root,
            )
            return
        if not any(source.enabled for source in self.sources):
            messagebox.showwarning(
                "没有启用来源",
                "当前所有来源均已停用，请至少启用一条后再合并。",
                parent=self.root,
            )
            return

        ip_versions = self._current_ip_versions()
        if not ip_versions:
            messagebox.showwarning(
                "未选择 IP 类型",
                "请在“输出筛选”中至少勾选 IPv4 或 IPv6。\n"
                "未勾选的 IP 类型不会写入结果。",
                parent=self.root,
            )
            return
        self._flush_settings()

        if self.dirty:
            answer = messagebox.askyesnocancel(
                "URL.txt 尚未更新",
                "来源列表的自动保存尚未成功。\n是否重试写入 URL.txt，再开始合并？",
                parent=self.root,
            )
            if answer is None:
                return
            if answer and not self._save_current(show_message=False):
                return

        if self.output_path.exists():
            if not messagebox.askyesno(
                "覆盖已有结果",
                f"输出目录中已存在“{self.output_path.name}”。\n"
                "本次合并成功后将会覆盖它，是否继续？",
                parent=self.root,
            ):
                return

        active_count = sum(1 for source in self.sources if source.enabled)
        self.row_runtime.clear()
        for source in self.sources:
            if source.enabled:
                self.row_runtime[source.url] = _RowRuntime(state="等待")
        self._refresh_table()
        self._refresh_result_stats(None)
        self.running = True
        self._set_controls_running(True)
        self._set_status(f"正在准备下载 {active_count} 条来源…", "info")
        self.progress.configure(maximum=max(1, active_count), value=0)
        if not self.progress.winfo_manager():
            self.progress.pack(
                side="right",
                padx=(0, 12),
                before=self.footer_version,
            )

        snapshot = [
            Source(url=source.url, name=source.name, enabled=source.enabled)
            for source in self.sources
        ]
        deduplicate = self.dedupe_var.get()
        github_accelerate = self.github_var.get()
        self.current_ip_versions = ip_versions
        self.last_github_accelerate = github_accelerate
        stop_event = threading.Event()
        self.stop_event = stop_event
        output_path = self.output_path
        self.worker_thread = threading.Thread(
            target=self._merge_worker,
            args=(
                snapshot,
                output_path,
                deduplicate,
                ip_versions,
                github_accelerate,
                stop_event,
            ),
            daemon=True,
            name="cf-merge-worker",
        )
        self.worker_thread.start()

    def _stop_merge(self) -> None:
        if not self.running or self.stop_event.is_set():
            return
        self.stop_event.set()
        self.start_button.configure(text="正在停止…", state="disabled")
        self.root.configure(cursor="watch")
        self._set_status(
            "正在停止合并，本次不会写入或覆盖结果文件…",
            "warning",
        )

    def _merge_worker(
        self,
        sources: list[Source],
        output_path: Path,
        deduplicate: bool,
        ip_versions: frozenset[int],
        github_accelerate: bool,
        stop_event: threading.Event,
    ) -> None:
        try:
            service = MergeService(
                downloader=DownloadClient(
                    github_accelerate=github_accelerate,
                    cancel_event=stop_event,
                ),
                max_workers=4,
            )
            report = service.run(
                sources,
                output_path,
                deduplicate=deduplicate,
                allowed_versions=ip_versions,
                cancel_event=stop_event,
                on_progress=lambda event: self.event_queue.put(
                    ("progress", event)
                ),
            )
        except Exception as exc:  # 防止线程异常静默退出
            self.event_queue.put(("fatal", f"合并过程发生异常：{exc}"))
        else:
            self.event_queue.put(("report", report))

    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self.event_queue.get_nowait()
                if kind == "progress":
                    self._handle_progress(payload)  # type: ignore[arg-type]
                elif kind == "report":
                    self._handle_report(payload)  # type: ignore[arg-type]
                elif kind == "fatal":
                    self._handle_fatal(str(payload))
        except queue.Empty:
            pass
        finally:
            self.root.after(100, self._poll_events)

    def _handle_progress(self, event: ProgressEvent) -> None:
        if not isinstance(event, ProgressEvent):
            return
        if event.kind == "started":
            runtime = self.row_runtime.setdefault(
                event.source.url, _RowRuntime()
            )
            runtime.state = "下载中"
            self.row_runtime[event.source.url] = runtime
            self._update_row_by_url(event.source.url)
            self._set_status(
                f"正在下载：{event.source.display_name(event.index + 1)}",
                "info",
            )
            return

        if event.kind == "finished" and event.outcome is not None:
            outcome = event.outcome
            runtime = self.row_runtime.setdefault(event.source.url, _RowRuntime())
            if outcome.success:
                filtered = filter_ip_lines(outcome.lines, self.current_ip_versions)
                matching_count = len(filtered.lines)
                runtime.lines = matching_count
                if outcome.warning:
                    runtime.state = "成功 · 警告"
                elif outcome.lines and matching_count == 0:
                    runtime.state = "成功 · 无匹配"
                else:
                    runtime.state = "成功"
                status_text = "成功"
                if outcome.warning:
                    status_text += "（有警告）"
                elif outcome.lines and matching_count == 0:
                    status_text += "（筛选后无匹配）"
                details = [
                    f"状态：{status_text}",
                    f"来源：{event.source.display_name(event.index + 1)}",
                    f"URL：{event.source.url}",
                    f"原始行数：{len(outcome.lines)}",
                    f"符合 {ip_filter_label(self.current_ip_versions)}：{matching_count}",
                    f"筛选排除：{filtered.excluded}"
                    + (f"（其中非 IP 行 {filtered.non_ip_excluded}）" if filtered.non_ip_excluded else ""),
                    f"下载大小：{outcome.byte_count:,} 字节",
                    f"尝试次数：{outcome.attempts}",
                    f"耗时：{outcome.duration:.2f} 秒",
                ]
                if outcome.download_url and outcome.download_url != event.source.url:
                    details.append(f"实际下载地址：{outcome.download_url}")
                if outcome.warning:
                    details.append(f"警告：{outcome.warning}")
                runtime.detail = "\n".join(details)
            else:
                runtime.state = "失败"
                runtime.lines = None
                failure_details = [
                    "状态：失败",
                    f"来源：{event.source.display_name(event.index + 1)}",
                    f"URL：{event.source.url}",
                ]
                if outcome.download_url and outcome.download_url != event.source.url:
                    failure_details.append(f"实际下载地址：{outcome.download_url}")
                failure_details.extend(
                    [
                        f"错误：{outcome.error}",
                        f"尝试次数：{outcome.attempts}",
                        f"耗时：{outcome.duration:.2f} 秒",
                    ]
                )
                runtime.detail = "\n".join(failure_details)
            self.row_runtime[event.source.url] = runtime
            self._update_row_by_url(event.source.url)
            completed = sum(
                1
                for item in self.row_runtime.values()
                if item.state in {
                    "成功",
                    "成功 · 警告",
                    "成功 · 无匹配",
                    "失败",
                }
            )
            maximum = int(float(self.progress.cget("maximum")))
            self.progress.configure(value=min(completed, maximum))
            self._set_status(
                f"已完成 {completed}/{maximum}，正在处理其他来源…",
                "info",
            )
            return

        if event.kind == "writing":
            self._set_status("下载完成，正在按来源顺序合并并写入 优选合集.txt…", "info")

    def _handle_fatal(self, message: str) -> None:
        if not self.running and not self.progress.winfo_manager():
            return
        if self.stop_event.is_set():
            self.running = False
            self._set_controls_running(False)
            self.progress.configure(value=0)
            self.progress.pack_forget()
            self._mark_pending_rows_stopped()
            self._refresh_table()
            self._set_status(
                "已停止合并，未写入或覆盖 优选合集.txt。",
                "warning",
            )
            return
        self.running = False
        self._set_controls_running(False)
        self.progress.configure(value=0)
        self.progress.pack_forget()
        self._set_status(message, "error")
        self._refresh_result_stats(None)
        messagebox.showerror("合并失败", message, parent=self.root)

    def _mark_pending_rows_stopped(self) -> None:
        for runtime in self.row_runtime.values():
            if runtime.state in {"等待", "下载中"}:
                runtime.state = "已停止"
                runtime.lines = None

    def _handle_cancelled(self, report: RunReport) -> None:
        self.running = False
        self.last_report = report
        self._set_controls_running(False)
        self.progress.configure(value=0)
        self.progress.pack_forget()
        self._mark_pending_rows_stopped()
        self._refresh_table()
        self.stat_values["本轮成功"].configure(
            text=f"{report.success_count}/{report.active_sources}"
        )
        self.stat_values["合并行数"].configure(text="—")
        self._set_status(
            "已停止合并，未写入或覆盖 优选合集.txt。",
            "warning",
        )

    def _handle_report(self, report: RunReport) -> None:
        if not isinstance(report, RunReport):
            return
        if report.cancelled:
            self._handle_cancelled(report)
            return
        self.running = False
        self.last_report = report
        self._set_controls_running(False)
        self.progress.configure(value=report.active_sources)
        self.progress.pack_forget()
        self._refresh_result_stats(report)

        for index, outcome in enumerate(report.outcomes):
            selected_count = (
                report.source_line_counts[index]
                if index < len(report.source_line_counts)
                else len(outcome.lines)
            )
            runtime = self.row_runtime.setdefault(outcome.source.url, _RowRuntime())
            if outcome.success:
                if outcome.warning:
                    runtime.state = "成功 · 警告"
                elif outcome.lines and selected_count == 0:
                    runtime.state = "成功 · 无匹配"
                else:
                    runtime.state = "成功"
                runtime.lines = selected_count
            else:
                runtime.state = "失败"
                runtime.lines = None
            self.row_runtime[outcome.source.url] = runtime
        self._refresh_table()

        filter_note = ""
        if report.allowed_versions is not None:
            filter_note = f"（{ip_filter_label(report.allowed_versions)}）"
        if report.fatal_error:
            self._set_status(report.fatal_error, "error")
        elif report.partial:
            self._set_status(
                f"部分完成{filter_note}：成功 {report.success_count}，"
                f"失败 {report.failed_count}，共合并 {report.total_lines:,} 行。",
                "warning",
            )
        else:
            self._set_status(
                f"合并完成{filter_note}：{report.success_count} 条来源，"
                f"共 {report.total_lines:,} 行。",
                "success",
            )
        self.root.after(80, lambda: self._show_run_result(report))

    @staticmethod
    def _format_report_details(report: RunReport) -> str:
        blocks: list[str] = []
        if report.fatal_error:
            blocks.extend([f"【结果】{report.fatal_error}", ""])
        if report.allowed_versions is not None:
            blocks.append(f"输出 IP 类型：{ip_filter_label(report.allowed_versions)}")
            blocks.append(f"筛选排除：{report.filtered_out:,} 行")
            if report.non_ip_filtered:
                blocks.append(f"其中非 IP 格式：{report.non_ip_filtered:,} 行")
            blocks.append("")
        for index, outcome in enumerate(report.outcomes, start=1):
            name = outcome.source.display_name(index)
            selected_count = (
                report.source_line_counts[index - 1]
                if index - 1 < len(report.source_line_counts)
                else len(outcome.lines)
            )
            if outcome.success:
                if outcome.warning:
                    status = "成功（有警告）"
                elif outcome.lines and selected_count == 0:
                    status = "成功（筛选后无匹配）"
                else:
                    status = "成功"
                blocks.append(f"【{status}】{name}")
                blocks.append(f"URL：{outcome.source.url}")
                if outcome.download_url and outcome.download_url != outcome.source.url:
                    blocks.append(f"实际下载地址：{outcome.download_url}")
                blocks.append(f"原始行数：{len(outcome.lines)}")
                if report.allowed_versions is not None:
                    blocks.append(
                        f"符合 {ip_filter_label(report.allowed_versions)}：{selected_count}"
                    )
                else:
                    blocks.append(f"有效行数：{selected_count}")
                if outcome.warning:
                    blocks.append(f"警告：{outcome.warning}")
            else:
                blocks.append(f"【失败】{name}")
                blocks.append(f"URL：{outcome.source.url}")
                if outcome.download_url and outcome.download_url != outcome.source.url:
                    blocks.append(f"实际下载地址：{outcome.download_url}")
                blocks.append(f"错误：{outcome.error}")
            blocks.append(
                f"尝试：{outcome.attempts} 次 · 耗时：{outcome.duration:.2f} 秒"
                + (f" · 下载：{outcome.byte_count:,} 字节" if outcome.success else "")
            )
            blocks.append("")
        if report.written:
            blocks.append(
                f"最终合并：{report.total_lines:,} 行"
                + (
                    f" · 去除完全重复 {report.duplicates_removed:,} 行"
                    if report.duplicates_removed
                    else ""
                )
            )
        if report.written and report.output_path:
            blocks.append(f"输出文件：{report.output_path}")
        return "\n".join(blocks).strip()

    def _show_run_result(self, report: RunReport) -> None:
        if report.written:
            title = "合并完成（部分成功）" if report.partial else "合并完成"
            summary = (
                f"成功 {report.success_count} / {report.active_sources} 个来源，"
                f"合并 {report.total_lines:,} 行"
            )
            if report.allowed_versions is not None:
                summary += f"\n输出类型：{ip_filter_label(report.allowed_versions)}"
            if self.last_github_accelerate:
                summary += "\nGitHub 加速：已启用"
            if report.filtered_out:
                summary += f"，筛选排除 {report.filtered_out:,} 行"
                if report.non_ip_filtered:
                    summary += f"（其中非 IP 格式 {report.non_ip_filtered:,} 行）"
            if report.duplicates_removed:
                summary += f"，去除完全重复 {report.duplicates_removed:,} 行"
            if report.failed_count:
                summary += f"，失败 {report.failed_count} 个"
            summary += f"。\n输出：{report.output_path}"
            actions: list[tuple[str, Callable[[], None]]] = [
                ("打开结果文件", self._open_result_file),
                ("打开所在目录", self._open_output_dir),
            ]
        else:
            title = "合并未完成"
            summary = report.fatal_error or "未生成结果文件。"
            actions = [("打开输出目录", self._open_output_dir)]

        details = self._format_report_details(report)
        self._show_details_dialog(title, summary, details, actions=actions)

    def _set_controls_running(self, running: bool) -> None:
        self.running = running
        normal = "disabled" if running else "normal"
        for widget in (
            self.add_button,
            self.clear_button,
            self.reload_button,
            self.open_config_button,
            self.open_dir_button,
        ):
            widget.configure(state=normal)
        for check in (
            self.dedupe_check,
            self.ipv4_check,
            self.ipv6_check,
            self.github_check,
        ):
            check.set_enabled(not running)
        for entry in (self.name_entry, self.url_entry):
            entry.configure(state=normal)

        if running:
            self.start_button.configure(
                text="停止合并",
                style="Stop.TButton",
                command=self._stop_merge,
                state="normal",
            )
        else:
            self.start_button.configure(
                text="开始合并  →",
                style="Primary.TButton",
                command=self._start_merge,
                state="normal",
            )
        if running and self.cancel_edit_button.winfo_manager():
            self.cancel_edit_button.configure(state="disabled")
        elif not running:
            self.cancel_edit_button.configure(state="normal")
        if running:
            for button in self.toolbar_buttons:
                button.configure(state="disabled")
        else:
            self._update_selection_buttons()
        self.root.configure(cursor="")

    def _open_result_file(self) -> None:
        if not self.output_path.exists():
            messagebox.showwarning(
                "结果文件不存在",
                f"暂未找到 {self.output_path.name}，请先执行合并。",
                parent=self.root,
            )
            return
        try:
            open_local_path(self.output_path)
        except OSError as exc:
            messagebox.showerror("无法打开结果文件", str(exc), parent=self.root)

    def _open_output_dir(self) -> None:
        try:
            self.app_dir.mkdir(parents=True, exist_ok=True)
            open_local_path(self.app_dir)
        except OSError as exc:
            messagebox.showerror("无法打开输出目录", str(exc), parent=self.root)

    def _open_project_page(self, _event: object = None) -> None:
        """打开 GitHub 项目主页（右下角链接的点击处理）。"""
        try:
            webbrowser.open(PROJECT_URL)
        except Exception as exc:  # 极端情况下无可用浏览器
            messagebox.showerror(
                "无法打开链接",
                f"请手动在浏览器中访问 {PROJECT_URL}（{exc}）",
                parent=self.root,
            )

    # ------------------------------------------------------------------ 弹窗

    def _show_details_dialog(
        self,
        title: str,
        summary: str,
        details: str,
        actions: list[tuple[str, Callable[[], None]]] | None = None,
    ) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.configure(bg=BG)
        dialog.transient(self.root)
        dialog.resizable(True, True)

        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        width = min(760, screen_w - 80)
        height = min(520, screen_h - 120)
        x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - width) // 2)
        y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - height) // 2)
        dialog.geometry(f"{width}x{height}+{x}+{y}")
        dialog.minsize(580, 360)

        dialog_header = tk.Frame(dialog, bg=NAVY, height=58)
        dialog_header.pack(fill="x")
        dialog_header.pack_propagate(False)
        tk.Label(
            dialog_header,
            text=title,
            bg=NAVY,
            fg="#FFFFFF",
            font=self._font(13, "bold"),
        ).pack(side="left", padx=18, pady=17)
        tk.Label(
            dialog_header,
            text=APP_NAME,
            bg=NAVY,
            fg=HEADER_SUBTLE,
            font=self._font(8),
        ).pack(side="right", padx=18)

        summary_frame = tk.Frame(dialog, bg=SURFACE)
        summary_frame.pack(fill="x", padx=16, pady=(12, 0))
        tk.Label(
            summary_frame,
            text=summary,
            bg=SURFACE,
            fg=TEXT,
            font=self._font(9),
            justify="left",
            anchor="w",
            wraplength=width - 60,
        ).pack(fill="x", padx=12, pady=10)

        text_frame = tk.Frame(dialog, bg=SURFACE)
        text_frame.pack(fill="both", expand=True, padx=16, pady=(8, 0))
        text_frame.grid_rowconfigure(0, weight=1)
        text_frame.grid_columnconfigure(0, weight=1)
        details_text = tk.Text(
            text_frame,
            wrap="word",
            height=10,
            width=50,
            bg="#F8FAFD",
            fg="#34495E",
            relief="flat",
            bd=0,
            padx=12,
            pady=10,
            font=self._font(9),
            insertbackground=TEXT,
            highlightthickness=1,
            highlightbackground=BORDER,
        )
        details_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(
            text_frame, orient="vertical", command=details_text.yview
        )
        scrollbar.grid(row=0, column=1, sticky="ns")
        details_text.configure(yscrollcommand=scrollbar.set)
        details_text.insert("1.0", details or "（无更多信息）")
        details_text.configure(state="disabled")

        footer = tk.Frame(dialog, bg=BG)
        footer.pack(
            side="bottom",
            fill="x",
            padx=16,
            pady=12,
            before=text_frame,
        )
        close_button = ttk.Button(
            footer,
            text="关闭",
            style="Secondary.TButton",
            command=dialog.destroy,
        )
        close_button.pack(side="right")
        for label, callback in reversed(actions or []):
            def run_action(
                callback: Callable[[], None] = callback,
                window: tk.Toplevel = dialog,
            ) -> None:
                window.destroy()
                self.root.after(50, callback)

            button = ttk.Button(
                footer,
                text=label,
                style="Primary.TButton" if label.startswith("打开结果") else "Secondary.TButton",
                command=run_action,
            )
            button.pack(side="right", padx=(0, 8))
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.update_idletasks()
        dialog.grab_set()
        close_button.focus_set()

    def _report_callback_exception(self, exc_type: type, exc: BaseException, tb: object) -> None:
        messagebox.showerror(
            "程序遇到问题",
            f"操作未能完成：{exc}\n\n已保存的配置和结果文件不会被破坏。",
            parent=self.root,
        )
        self._set_status(f"操作异常：{exc}", "error")

    # ------------------------------------------------------------------ 程序参数

    def _bind_settings_traces(self) -> None:
        for variable in (
            self.dedupe_var,
            self.ipv4_var,
            self.ipv6_var,
            self.github_var,
        ):
            variable.trace_add("write", self._on_settings_changed)
        self._update_filter_hint()

    def _current_settings(self) -> UiSettings:
        return UiSettings(
            deduplicate=self.dedupe_var.get(),
            ipv4=self.ipv4_var.get(),
            ipv6=self.ipv6_var.get(),
            github_accelerate=self.github_var.get(),
        )

    def _current_ip_versions(self) -> frozenset[int]:
        versions: set[int] = set()
        if self.ipv4_var.get():
            versions.add(4)
        if self.ipv6_var.get():
            versions.add(6)
        return frozenset(versions)

    def _on_settings_changed(self, *_args: object) -> None:
        if self.running:
            return
        self._update_filter_hint()
        self._schedule_settings_save()
        if not (self.ipv4_var.get() or self.ipv6_var.get()):
            self._set_status(
                "未选择任何 IP 类型，需勾选 IPv4 或 IPv6 才能合并。",
                "warning",
            )
        else:
            self._set_status("输出选项已更新，程序参数将自动保存。", "info")

    def _update_filter_hint(self) -> None:
        if self.ipv4_var.get() or self.ipv6_var.get():
            self.filter_hint_label.configure(text="")
        else:
            self.filter_hint_label.configure(text="至少勾选一种 IP")

    def _schedule_settings_save(self) -> None:
        if self.settings_save_job is not None:
            try:
                self.root.after_cancel(self.settings_save_job)
            except tk.TclError:
                pass
        self.settings_save_job = self.root.after(180, self._save_ui_settings)

    def _save_ui_settings(self, show_status: bool = True) -> bool:
        self.settings_save_job = None
        settings = self._current_settings()
        try:
            save_settings(self.settings_path, settings)
        except Exception as exc:
            self.settings_error = str(exc)
            if show_status:
                self._set_status(f"程序参数保存失败：{exc}", "error")
            return False

        self.ui_settings = settings
        self.settings_error = None
        if show_status and not self.running:
            if not (settings.ipv4 or settings.ipv6):
                self._set_status(
                    "程序参数已保存，但尚未选择 IP 类型，无法合并。",
                    "warning",
                )
            else:
                self._set_status("程序参数已自动保存。", "success")
        return True

    def _flush_settings(self) -> None:
        pending = self.settings_save_job is not None
        if pending:
            try:
                self.root.after_cancel(self.settings_save_job)
            except tk.TclError:
                pass
            self.settings_save_job = None
        if pending or self._current_settings() != self.ui_settings:
            self._save_ui_settings(show_status=False)

    # ------------------------------------------------------------------ 快捷键和退出

    def _bind_shortcuts(self) -> None:
        self.root.bind_all("<Control-s>", lambda _event: self._save_clicked())
        self.root.bind_all("<F5>", lambda _event: self._reload_clicked())
        self.root.bind_all("<Escape>", self._escape_editor)

    def _escape_editor(self, _event: tk.Event) -> None:
        if self.editing_index is not None and not self.running:
            self._clear_editor()

    def _on_close(self) -> None:
        if self.running:
            if not messagebox.askyesno(
                "正在合并",
                "合并任务尚未完成。现在退出会中止任务，但不会破坏已有结果文件。\n仍要退出吗？",
                parent=self.root,
            ):
                return
            self.stop_event.set()
            self._flush_settings()
            self.root.destroy()
            return

        if self.dirty:
            answer = messagebox.askyesnocancel(
                "URL.txt 尚未更新",
                "来源列表的自动保存尚未成功，是否重试保存后退出？",
                parent=self.root,
            )
            if answer is None:
                return
            if answer and not self._save_current(show_message=False):
                return

        self._flush_settings()
        self.root.destroy()


def run_app() -> None:
    root = tk.Tk()
    icon_path = get_resource_path("assets/app.ico")
    if icon_path.exists():
        try:
            root.iconbitmap(default=str(icon_path))
        except tk.TclError:
            pass
    MergerApp(root)
    root.mainloop()
