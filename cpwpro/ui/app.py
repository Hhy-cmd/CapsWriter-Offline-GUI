# -*- coding: utf-8 -*-
"""
CPW-Pro AI 语音转写工作站（恢复版）

说明：
- 本程序仅作为 CapsWriter-Offline 的 GUI 调度壳。
- 胶水层见 `cpwpro.worker`；下载·抽轨·转写/VAD 线程编排见 `cpwpro.transcribe`。
- 链接清洗与 ANSI 剔除见 `cpwpro.textutil`；CTk 外观与示波配色见 `cpwpro.theme`。
"""

from __future__ import annotations

import os
import re
import sys
import subprocess
import threading
import time
import json
import tkinter as tk
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Optional, Sequence

import customtkinter as ctk

try:
    from tkinterdnd2 import COPY as DND_COPY, DND_FILES
    from tkinterdnd2.TkinterDnD import _require as _tkdnd_require
except ImportError:
    DND_COPY = "copy"
    DND_FILES = "DND_Files"
    _tkdnd_require = None  # type: ignore[misc,assignment]

from cpwpro.support.config_manager import (
    AppConfigManager,
    PromptLibraryManager,
    DEFAULT_CONFIG as _CFG_DEFAULT,
    DEFAULT_PROMPTS as _PROMPTS_DEFAULT,
)
from cpwpro.paths import project_root
from cpwpro.textutil import normalize_url, strip_ansi
from cpwpro.theme import apply_ctk_defaults, scope_canvas_bg_and_stroke as _scope_canvas_bg_and_stroke
from cpwpro.progress import (
    CORE_TRANSCRIBE_LOG_THROTTLE_SEC,
    DOWNLOAD_LOG_THROTTLE_SEC,
    fraction_from_send_progress,
    fraction_from_transcribe_sec,
    looks_like_core_progress_line,
    parse_core_send_sec_pair,
    parse_core_transcribe_sec,
    parse_download_bar_fraction,
    sniff_after_timestamp_prefix,
    transcribe_bar_segment,
)
from cpwpro.transcribe import (
    SimplePipelineHooks,
    TranscribePipelineHooks,
    run_download_then_extract,
    run_extract_all,
    run_transcribe_all,
    run_transcribe_all_vad,
)
from cpwpro.support.llm_client import stream_chat, build_messages
from cpwpro.support.media_utils import AudioEngine, parse_srt
from cpwpro.support.timestamp_quality import analyze_timestamp_quality, format_quality_report

from cpwpro.tray_support import TrayController, tray_disabled_by_env, tray_hide_disabled_by_env

# Windows 拖拽与「选择文件」共用：允许的后缀
_DROP_MEDIA_SUFFIXES = frozenset(
    x.lower()
    for x in (".mp3", ".mp4", ".wav", ".m4a", ".mkv", ".flac", ".aac", ".ogg", ".webm", ".mov")
)

_BASE = project_root()
BASE_DIR = str(_BASE)

_APP_CONFIG_PATH = _BASE / "config.json"
_PROMPTS_PATH = _BASE / "prompts.json"

_PROVIDER_PRESETS: dict[str, tuple[str, str]] = {
    "自定义": ("", ""),
    "DeepSeek 官方": ("https://api.deepseek.com/v1", "deepseek-chat"),
    "Kimi (月之暗面)": ("https://api.moonshot.cn/v1", "moonshot-v1-8k"),
    "OpenAI": ("https://api.openai.com/v1", "gpt-4o-mini"),
    "Ollama (本地)": ("http://127.0.0.1:11434/v1", "qwen2.5:7b"),
}

_SCOPE_MS = 50
_SCOPE_H = 28
_SCOPE_WINDOW_SEC = 0.07


class App(ctk.CTk):
    WINDOW_TITLE = "CPW-Pro · AI 语音转写工作站"
    WINDOW_W = 1200
    WINDOW_H = 760
    MIN_W = 980
    MIN_H = 640
    LEFT_W = 420

    def __init__(self):
        super().__init__()
        self.title(self.WINDOW_TITLE)
        self.geometry(f"{self.WINDOW_W}x{self.WINDOW_H}")
        self.minsize(self.MIN_W, self.MIN_H)

        self._tkdnd_loaded = False
        self._tkdnd_failure_msg: Optional[str] = None
        # tkdnd 在 Py3.14 + CustomTkinter 上与「首轮 update/绘制」并存会触发原生访问冲突（ACCESS_VIOLATION），
        # 故延后到界面完成首次映射后再加载；也可用环境变量彻底关闭：CPW_DISABLE_TKDND=1

        self._cfg_mgr = AppConfigManager(_APP_CONFIG_PATH)
        self._prompt_mgr = PromptLibraryManager(_PROMPTS_PATH)
        self._cfg = self._cfg_mgr.load()
        self._prompts = self._prompt_mgr.load_all()

        out_dir = str(self._cfg.get("output_dir", _CFG_DEFAULT["output_dir"]))
        if not os.path.isabs(out_dir):
            out_dir = os.path.join(BASE_DIR, out_dir)
        self._cfg["output_dir"] = out_dir

        self.output_dir = tk.StringVar(value=out_dir)
        self.main_template_var = tk.StringVar(value=self._cfg.get("template_name", "精炼复习笔记"))
        self.auto_clean = ctk.BooleanVar(value=True)
        self.auto_transcribe = ctk.BooleanVar(value=True)
        self.fast_download = ctk.BooleanVar(value=True)
        self.vad_transcribe = ctk.BooleanVar(value=False)

        self.selected_files: list[str] = []
        self.ready_audio_files: list[str] = []
        self._cleanup_queue: list[str] = []
        self._server_proc: Optional[subprocess.Popen] = None
        self._task_busy = False

        self.audio_engine = AudioEngine(log_fn=lambda m: self.log(m))
        self._subtitle_data: list[dict] = []
        self._subtitle_start_times: list[float] = []
        self._current_srt_path = ""
        self._subtitle_unsaved = False
        self._active_subtitle_abs_idx = -1
        self._subtitle_row_widgets: list[ctk.CTkFrame] = []
        self._subtitle_text_widgets: list[ctk.CTkTextbox] = []
        self._subtitle_window_start = 0  # 历史字段；分页以 _sub_page + _SUB_PAGE_SIZE 为准
        self._sub_page = 0
        self._SUB_PAGE_SIZE = 11
        self._last_scroll_ts = 0.0
        self._slider_dragging = False
        self._seek_after_id: Optional[str] = None
        self._pending_seek_sec = 0.0
        self._total_duration = 0.0
        self.is_editing = False
        self._playback_hotkey_ts = 0.0
        self._pulse_fallback_after_id: Optional[str] = None
        self._progress_last_granular_t = 0.0
        self._prog_log_tick = 0.0
        self._prog_core_tick = 0.0
        self._transcribe_batch_idx = 0
        self._transcribe_batch_total = 1
        self._transcribe_audio_total_sec: Optional[float] = None
        self._transcribe_last_sec_line = -1.0

        self._scope_after_id: Optional[str] = None
        self._scope_layout_w: int = 0

        self._settings_win: Optional[ctk.CTkToplevel] = None
        self._notes_win: Optional[_NoteWindow] = None
        self._finalizing_exit = False
        self._editing_prompt_name = ""
        self._fetched_models: list[str] = []
        self._fetched_models_base_url = ""

        self._build_header()
        self._build_main_area()

        self._tray = TrayController(self, _BASE)
        self.protocol("WM_DELETE_WINDOW", self._on_app_close)
        self.after(220, self._apply_taskbar_icon)
        # 首帧 idle 创建托盘（幂等），避免用户很快点 × 时尚未启动导致误走退出 / 幽灵线程。
        self.after_idle(lambda: self._tray.start(self.log))
        self.bind("<Control-s>", lambda e: self.save_subtitle_to_file())
        self._install_playback_hotkeys()
        self.after(100, self.update_playback_ui)

        self.log("[Info] CPW-Pro 初始化完成。")
        self.after(1200, self._deferred_init_tkdnd)

    # ---------- 播放 / 字幕编辑快捷键 ----------
    def _is_url_entry_widget(self, w) -> bool:
        try:
            ent = getattr(self, "url_entry", None)
            return ent is not None and w is getattr(ent, "_entry", None)
        except Exception:
            return False

    def _install_playback_hotkeys(self) -> None:
        """播放/暂停与字幕编辑内「结束编辑并播放」——使用 F9，避免 Ctrl+Space 被 Win/输入法占用；Ctrl+F9 为备用。"""
        self.bind_all("<F9>", self._playback_hotkey_event)
        self.bind_all("<Control-F9>", self._playback_hotkey_event)

    def _subtitle_target_from_tk_widget(self, w) -> tuple[int | None, Optional[ctk.CTkTextbox]]:
        if not w:
            return None, None
        for loc, tb in enumerate(self._subtitle_text_widgets):
            inner = getattr(tb, "_textbox", None)
            if w is inner or w is tb:
                base = getattr(self, "_sub_page", 0) * self._SUB_PAGE_SIZE
                return base + loc, tb
        return None, None

    def _playback_hotkey_event(self, event: tk.Event) -> str:
        now = time.time()
        if now - self._playback_hotkey_ts < 0.22:
            return "break"
        self._playback_hotkey_ts = now

        w = getattr(event, "widget", None)
        if self._is_url_entry_widget(w):
            return ""  # 不在链接输入框里抢 F9（避免误触）
        abs_idx, tb = self._subtitle_target_from_tk_widget(w)
        if abs_idx is not None and tb is not None:
            self._on_subtitle_play_hotkey(event, abs_idx, tb)
            return "break"
        if self._total_duration <= 0:
            return "break"
        self._toggle_playback()
        return "break"

    # ---------- persistence ----------
    def _save_runtime_config(self) -> None:
        try:
            self._cfg = self._cfg_mgr.save(self._cfg)
        except Exception as exc:
            self.log(f"[Warn] 配置保存失败：{exc}")

    def _reload_prompt_library(self) -> None:
        try:
            self._prompts = self._prompt_mgr.load_all()
        except Exception as exc:
            self.log(f"[Warn] 模板库读取失败：{exc}")
            self._prompts = dict(_PROMPTS_DEFAULT)

    # ---------- ui skeleton ----------
    def _build_header(self):
        bar = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        bar.pack(fill="x")
        inner = ctk.CTkFrame(bar, fg_color="transparent", corner_radius=0)
        inner.pack(fill="x", padx=20, pady=(14, 10))
        ctk.CTkLabel(inner, text="CPW-Pro", font=ctk.CTkFont(size=20, weight="bold")).pack(side="left")
        ctk.CTkLabel(inner, text="AI 语音转写工作站", text_color=("gray40", "gray60"), font=ctk.CTkFont(size=14)).pack(side="left", padx=(8, 0))
        ctk.CTkLabel(inner, text="by CapsWriter-Offline", text_color=("gray55", "gray55"), font=ctk.CTkFont(size=11)).pack(side="right")
        ctk.CTkFrame(bar, height=1, fg_color=("gray82", "gray28"), corner_radius=0).pack(fill="x")

    def _build_main_area(self):
        root = ctk.CTkFrame(self, fg_color="transparent")
        root.pack(fill="both", expand=True)

        left = ctk.CTkFrame(root, width=self.LEFT_W, fg_color=("gray94", "gray13"), corner_radius=0)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        self._build_left_panel(left)

        ctk.CTkFrame(root, width=1, fg_color=("gray80", "gray28"), corner_radius=0).pack(side="left", fill="y")

        right = ctk.CTkFrame(root, fg_color="transparent", corner_radius=0)
        right.pack(side="left", fill="both", expand=True)
        self._build_right_panel(right)

    def _build_left_panel(self, parent):
        self._build_left_input(parent)
        self._build_left_log(parent)
        self._build_left_actions(parent)

    def _build_left_input(self, parent):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(fill="x", padx=16, pady=(16, 0))

        ctk.CTkLabel(frame, text="输入源", font=ctk.CTkFont(size=11, weight="bold"), text_color=("gray40", "gray60")).pack(anchor="w", pady=(0, 6))

        row_url = ctk.CTkFrame(frame, fg_color="transparent")
        row_url.pack(fill="x", pady=(0, 8))
        self.url_entry = ctk.CTkEntry(row_url, placeholder_text="B站链接 / BV号 / YouTube URL", height=34, corner_radius=8)
        self.url_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.btn_download = ctk.CTkButton(row_url, text="解析下载", width=84, height=34, corner_radius=8, command=self._on_download_click)
        self.btn_download.pack(side="left")

        self.drop_zone = ctk.CTkFrame(frame, height=90, border_width=2, border_color=("gray65", "gray38"), corner_radius=10, fg_color=("gray92", "gray18"))
        self.drop_zone.pack(fill="x", pady=(0, 10))
        self.drop_zone.pack_propagate(False)
        self._render_drop_zone_idle()

        row_dir = ctk.CTkFrame(frame, fg_color="transparent")
        row_dir.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(row_dir, text="输出目录", width=62, anchor="w", font=ctk.CTkFont(size=11), text_color=("gray45", "gray60")).pack(side="left")
        self.out_dir_entry = ctk.CTkEntry(row_dir, textvariable=self.output_dir, state="readonly", height=28, corner_radius=7, font=ctk.CTkFont(size=11))
        self.out_dir_entry.pack(side="left", fill="x", expand=True, padx=(4, 6))
        ctk.CTkButton(row_dir, text="浏览", width=48, height=28, corner_radius=7, font=ctk.CTkFont(size=11), command=self._on_browse_output).pack(side="left")

        row_sw = ctk.CTkFrame(frame, fg_color="transparent")
        row_sw.pack(fill="x")
        ctk.CTkSwitch(row_sw, text="完成后自动清理临时文件", variable=self.auto_clean, onvalue=True, offvalue=False, font=ctk.CTkFont(size=11)).pack(side="left")
        ctk.CTkSwitch(
            row_sw, text="极速下载模式", variable=self.fast_download,
            onvalue=True, offvalue=False, font=ctk.CTkFont(size=11)
        ).pack(side="left", padx=(10, 0))
        ctk.CTkSwitch(
            row_sw, text="VAD切片转写", variable=self.vad_transcribe,
            onvalue=True, offvalue=False, font=ctk.CTkFont(size=11)
        ).pack(side="left", padx=(10, 0))

    def _build_left_log(self, parent):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(fill="both", expand=True, padx=16, pady=(14, 0))
        hdr = ctk.CTkFrame(frame, fg_color="transparent")
        hdr.pack(fill="x", pady=(0, 5))
        ctk.CTkLabel(hdr, text="运行状态", font=ctk.CTkFont(size=11, weight="bold"), text_color=("gray40", "gray60")).pack(side="left")
        ctk.CTkButton(hdr, text="清空", width=42, height=22, corner_radius=6, fg_color="transparent", border_width=1, command=self._clear_log).pack(side="right")
        self.progress_bar = ctk.CTkProgressBar(frame, mode="determinate", height=5)
        self.progress_bar.pack(fill="x", pady=(0, 6))
        self.progress_bar.set(0)
        self.log_box = ctk.CTkTextbox(frame, state="disabled", font=ctk.CTkFont(family="Consolas", size=11), wrap="word", corner_radius=8)
        self.log_box.pack(fill="both", expand=True)

    def _build_left_actions(self, parent):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(fill="x", padx=16, pady=(10, 18))
        ctk.CTkFrame(frame, height=1, fg_color=("gray80", "gray30"), corner_radius=0).pack(fill="x", pady=(0, 10))

        # 主操作按钮 - 使用强调色
        self.start_btn = ctk.CTkButton(
            frame, text="▶  开始 AI 转写",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=48, corner_radius=10,
            fg_color=("#2563eb", "#3b82f6"),
            hover_color=("#1d4ed8", "#2563eb"),
            text_color=("white", "white"),
            command=self._on_start_transcribe
        )
        self.start_btn.pack(fill="x", pady=(0, 8))

        # 次级操作区 - 两列布局
        row2 = ctk.CTkFrame(frame, fg_color="transparent")
        row2.pack(fill="x")

        # 左侧：生成AI总结（淡色强调）+ 模板选择
        notes_left = ctk.CTkFrame(row2, fg_color="transparent")
        notes_left.pack(side="left", fill="x", expand=True, padx=(0, 6))

        # 生成AI总结按钮 - 淡蓝配色强调
        self.btn_notes = ctk.CTkButton(
            notes_left, text="🤖  生成 AI 总结",
            font=ctk.CTkFont(size=12, weight="bold"),
            height=40, corner_radius=10,
            fg_color=("#e0f2fe", "#1e3a5f"),
            hover_color=("#bae6fd", "#172554"),
            text_color=("#0369a1", "#7dd3fc"),
            command=self._on_generate_notes,
        )
        self.btn_notes.pack(fill="x", pady=(0, 4))

        # 模板选择行（嵌套在同一区域下方）
        tpl_row = ctk.CTkFrame(notes_left, fg_color="transparent")
        tpl_row.pack(fill="x")
        ctk.CTkLabel(tpl_row, text="模板：", font=ctk.CTkFont(size=11), text_color=("gray45", "gray60")).pack(side="left")
        self.btn_notes_arrow = ctk.CTkButton(
            tpl_row, text="--  ▾", height=30, corner_radius=6,
            font=ctk.CTkFont(size=11),
            fg_color=("gray88", "gray22"),
            text_color=("gray20", "gray86"),
            hover_color=("gray78", "gray32"),
            command=self._show_main_template_menu,
        )
        self.btn_notes_arrow.pack(side="left", fill="x", expand=True, padx=(4, 0))

        # 模型提示（并入同一行，节省空间）
        self.model_hint_label = ctk.CTkLabel(
            tpl_row,
            text="模型：--",
            font=ctk.CTkFont(size=10),
            text_color=("gray55", "gray55"),
        )
        self.model_hint_label.pack(side="right", padx=(8, 0))

        self._refresh_main_template_menu(self._cfg.get("template_name", ""))

        # 右侧：配置按钮
        self.btn_settings = ctk.CTkButton(
            row2, text="⚙ 配置",
            width=80, height=74, corner_radius=10,
            fg_color=("gray82", "gray26"),
            hover_color=("gray72", "gray34"),
            command=self._open_settings_dialog
        )
        self.btn_settings.pack(side="left")

    def _build_right_panel(self, parent):
        self._build_media_control(parent)
        ctk.CTkFrame(parent, height=1, fg_color=("gray82", "gray26"), corner_radius=0).pack(fill="x")
        self._build_subtitle_editor(parent)

    def _build_media_control(self, parent):
        bar = ctk.CTkFrame(parent, corner_radius=0, fg_color=("gray90", "gray15"), height=112)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        top = ctk.CTkFrame(bar, fg_color="transparent")
        top.pack(fill="x", padx=18, pady=(10, 2))
        self.media_label = ctk.CTkLabel(top, text="当前文件：无", font=ctk.CTkFont(size=12), text_color=("gray38", "gray62"), anchor="w")
        self.media_label.pack(side="left", fill="x", expand=True)
        _ctrl = dict(width=36, height=30, corner_radius=8, fg_color=("gray80", "gray28"), text_color=("gray10", "gray90"), hover_color=("gray68", "gray38"), font=ctk.CTkFont(size=13))
        ctk.CTkButton(top, text="📁 加载测试文件", width=120, height=30, corner_radius=8, fg_color=("gray78", "gray32"), hover_color=("gray66", "gray42"), font=ctk.CTkFont(size=11), command=self._on_load_test_srt).pack(side="right", padx=(8, 0))
        self.pause_btn = ctk.CTkButton(top, text="⏸", command=self._on_pause, **_ctrl)
        self.pause_btn.pack(side="right", padx=(4, 0))
        self.play_btn = ctk.CTkButton(top, text="▶", command=self._on_play, **_ctrl)
        self.play_btn.pack(side="right", padx=(4, 0))
        _sc_bg, _, _ = _scope_canvas_bg_and_stroke()
        self.scope_canvas = tk.Canvas(
            bar,
            height=_SCOPE_H,
            bg=_sc_bg,
            highlightthickness=0,
            bd=0,
            borderwidth=0,
        )
        self.scope_canvas.pack(fill="x", padx=18, pady=(0, 3))
        self.scope_canvas.bind("<Configure>", self._on_scope_configure, add="+")

        self.play_slider = ctk.CTkSlider(bar, from_=0, to=1, height=12, command=self._on_slider_drag)
        self.play_slider.set(0)
        self.play_slider.pack(fill="x", padx=18, pady=(0, 10))

    def _build_subtitle_editor(self, parent):
        hdr = ctk.CTkFrame(parent, fg_color="transparent")
        hdr.pack(fill="x", padx=18, pady=(12, 6))
        ctk.CTkLabel(hdr, text="字幕编辑器", font=ctk.CTkFont(size=13, weight="bold")).pack(side="left")
        ctk.CTkLabel(
            hdr,
            text="（时间戳跳转 · Ctrl+S 保存字幕文件 · F9 结束编辑并播放/暂停）",
            font=ctk.CTkFont(size=11),
            text_color=("gray50", "gray55"),
        ).pack(side="left", padx=(8, 0))
        self.subtitle_save_btn = ctk.CTkButton(
            hdr, text="✓ 已保存", width=100, height=28, corner_radius=8, command=self.save_subtitle_to_file
        )
        self.subtitle_save_btn.pack(side="right")

        outer = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0)
        outer.pack(fill="both", expand=True)

        self.subtitle_nav_frame = ctk.CTkFrame(
            outer, corner_radius=0, fg_color=("gray90", "gray15"), height=40
        )
        self.subtitle_nav_frame.pack(fill="x")
        self.subtitle_nav_frame.pack_propagate(False)
        self.subtitle_scroll = ctk.CTkScrollableFrame(outer, corner_radius=0, fg_color="transparent")
        self.subtitle_scroll.pack(fill="both", expand=True)

        demo = [
            {"time_str": "00:00:08", "start_sec": 8.0, "end_sec": 12.0, "text": "欢迎使用 CPW-Pro 工作站。"},
            {"time_str": "00:00:21", "start_sec": 21.0, "end_sec": 24.0, "text": "点击时间戳可以跳转播放。"},
            {"time_str": "00:00:38", "start_sec": 38.0, "end_sec": 44.0, "text": "文本框可编辑，离焦自动写回内存。"},
        ]
        self._render_subtitles(demo)

    # ---------- logs ----------
    def log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        clean = strip_ansi(msg)
        self.after(0, self._append_log, f"[{ts}]  {clean}\n")

    def _append_log(self, line: str):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", line)
        self.log_box.configure(state="disabled")
        self.log_box.see("end")
        sniff = sniff_after_timestamp_prefix(line)
        self._maybe_progress_from_download_log(sniff)
        self._maybe_progress_from_core_log(sniff)

    def _sync_transcribe_slot_main(self, idx: int, total: int) -> None:
        self._transcribe_batch_idx = idx
        self._transcribe_batch_total = max(1, total)
        self._transcribe_audio_total_sec = None
        self._transcribe_last_sec_line = -1.0

    def _maybe_progress_from_core_log(self, line: str) -> None:
        if not self._task_busy:
            return
        if not looks_like_core_progress_line(line):
            return
        lo, hi = transcribe_bar_segment(self._transcribe_batch_idx, self._transcribe_batch_total)

        pair = parse_core_send_sec_pair(line)
        if pair:
            cur_sec, tot_sec = pair
            self._transcribe_audio_total_sec = tot_sec
            frac = fraction_from_send_progress(cur_sec, tot_sec, lo, hi)
            self._touch_granular_progress()
            self.progress_bar.set(frac)
            return

        sec = parse_core_transcribe_sec(line)
        if sec is None:
            return
        now = time.monotonic()
        if now - self._prog_core_tick < CORE_TRANSCRIBE_LOG_THROTTLE_SEC:
            return
        self._prog_core_tick = now

        frac, new_last = fraction_from_transcribe_sec(
            sec,
            self._transcribe_audio_total_sec,
            lo,
            hi,
            self._transcribe_last_sec_line,
            float(self.progress_bar.get()),
        )
        if frac is None:
            return
        self._transcribe_last_sec_line = new_last
        self._touch_granular_progress()
        self.progress_bar.set(frac)

    def _disarm_pulse_fallback(self) -> None:
        pid = getattr(self, "_pulse_fallback_after_id", None)
        if pid is not None:
            try:
                self.after_cancel(pid)
            except Exception:
                pass
            self._pulse_fallback_after_id = None

    def _pulse_stop_go_determinate(self) -> None:
        pb = self.progress_bar
        try:
            pb.stop()
            if pb.cget("mode") != "determinate":
                pb.configure(mode="determinate")
        except Exception:
            pass

    def _touch_granular_progress(self) -> None:
        """确定型进度：退出条形扫描动画，并重置「静默超时则进入脉冲」计时。"""
        self._progress_last_granular_t = time.monotonic()
        self._pulse_stop_go_determinate()
        self._disarm_pulse_fallback()
        if self._task_busy:
            self._pulse_fallback_after_id = self.after(520, self._maybe_enter_pulse_mode)

    def _maybe_enter_pulse_mode(self) -> None:
        """长时间无粒度更新（ffmpeg / CapsWriter）：进度条 indeterminate 来回扫。"""
        self._pulse_fallback_after_id = None
        if not self._task_busy:
            return
        if time.monotonic() - self._progress_last_granular_t < 0.45:
            self._pulse_fallback_after_id = self.after(520, self._maybe_enter_pulse_mode)
            return
        pb = self.progress_bar
        try:
            pb.stop()
            pb.configure(mode="indeterminate", indeterminate_speed=1.1)
            pb.start()
        except Exception:
            pass

    def _maybe_progress_from_download_log(self, line: str) -> None:
        if not self._task_busy:
            return
        frac = parse_download_bar_fraction(line)
        if frac is None:
            return
        now = time.monotonic()
        if now - self._prog_log_tick < DOWNLOAD_LOG_THROTTLE_SEC:
            return
        self._prog_log_tick = now
        self._touch_granular_progress()
        self.progress_bar.set(frac)

    def _clear_log(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    def _is_primary_task_busy(self) -> bool:
        return self._task_busy

    def _deferred_init_tkdnd(self) -> None:
        """首帧绘制完成后再加载 tkdnd，避免 CPW 首轮 update 与 TkDND Tcl 补丁在 Py3.14 上原生崩溃。"""
        if os.environ.get("CPW_DISABLE_TKDND", "").strip().lower() in ("1", "true", "yes", "on"):
            self.log("[提示] 已通过环境变量 CPW_DISABLE_TKDND 关闭拖放扩展。")
            return
        if self._tkdnd_loaded:
            return
        if _tkdnd_require is None:
            self.log("[提示] 拖放：请 pip install tkinterdnd2，或使用虚线区内「点击选择文件」。")
            return
        try:
            _tkdnd_require(self)
            self._tkdnd_loaded = True
            self._tkdnd_failure_msg = None
            self._ensure_drop_zone_tkdnd()
            self.log("[Info] 已向虚线框注册拖放（若拖到文字上无效，请拖到边框空白处）。")
        except Exception as exc:
            self._tkdnd_failure_msg = str(exc)
            self.log(f"[提示] tkdnd 不可用：{exc}。请使用「点击选择文件」。")

    def _ensure_drop_zone_tkdnd(self) -> None:
        """仅绑定拖放区最外层 CTkFrame。勿对每位子控件 register，会破坏 CustomTkinter 画布与整窗绘制。"""
        if not self._tkdnd_loaded:
            return
        dz = getattr(self, "drop_zone", None)
        if dz is None or not dz.winfo_exists():
            return
        try:
            try:
                dz.drop_target_unregister()
            except tk.TclError:
                pass
            dz.drop_target_register(DND_FILES)
            dz.dnd_bind("<<Drop>>", self._on_tkdnd_drop_files)
        except Exception as exc:
            self.log(f"[Warn] 拖放区 tkdnd 绑定失败：{exc}")

    def _on_tkdnd_drop_files(self, event):
        try:
            raw = (getattr(event, "data", None) or "").strip()
            paths: list[str] = []
            if raw:
                paths = [os.path.normpath(p) for p in self.tk.splitlist(raw)]
            if paths:
                seq = list(paths)
                self.after(0, lambda s=seq: self._submit_local_media_files(s))
        except Exception as exc:
            self.after(0, lambda err=str(exc): self.log(f"[Warn] 解析拖放路径失败：{err}"))
        return DND_COPY

    def _submit_local_media_files(self, files: Sequence[str]) -> None:
        """与「选择文件」相同：过滤格式后转码线程。"""
        if not files:
            return
        if self._is_primary_task_busy():
            self.log("[Warn] 当前有任务进行中，请结束后再添加文件。")
            return
        uniq: list[str] = []
        seen: set[str] = set()
        for raw in files:
            p = os.path.normpath(str(raw).strip().strip("\0"))
            if not p or not os.path.isfile(p):
                continue
            suf = Path(p).suffix.lower()
            if suf not in _DROP_MEDIA_SUFFIXES:
                continue
            ap = os.path.abspath(p)
            if ap not in seen:
                seen.add(ap)
                uniq.append(ap)
        if not uniq:
            self.log("[Warn] 无支持的音视频文件（需 mp3/mp4/wav/m4a/mkv 等）。")
            return
        self.selected_files = uniq
        self.ready_audio_files = []
        self._cleanup_queue = []
        self._render_drop_zone_loaded(self.selected_files)
        self._set_busy("正在转换音频格式…")
        threading.Thread(target=self._thread_extract_all, args=(self.selected_files, self.output_dir.get()), daemon=True).start()

    # ---------- drop zone ----------
    def _bind_drop_zone_click(self):
        fn = lambda e: self._on_select_files()

        def bind_rec(w: tk.Misc) -> None:
            try:
                w.bind("<Button-1>", fn)
            except Exception:
                return
            for c in w.winfo_children():
                bind_rec(c)

        bind_rec(self.drop_zone)
        self.after(80, self._ensure_drop_zone_tkdnd)

    def _render_drop_zone_idle(self):
        for w in self.drop_zone.winfo_children():
            w.destroy()
        self.drop_zone.configure(fg_color=("gray92", "gray18"), border_color=("gray65", "gray38"))
        center = ctk.CTkFrame(self.drop_zone, fg_color="transparent")
        center.place(relx=0.5, rely=0.5, anchor="center")
        ctk.CTkLabel(center, text="📂 点击选择或拖拽音视频文件", font=ctk.CTkFont(size=12), text_color=("gray45", "gray60")).pack()
        ctk.CTkLabel(center, text="mp3/mp4/wav/m4a/mkv/aac/webm", font=ctk.CTkFont(size=10), text_color=("gray60", "gray50")).pack(pady=(3, 0))
        self._bind_drop_zone_click()

    def _render_drop_zone_busy(self, msg: str):
        for w in self.drop_zone.winfo_children():
            w.destroy()
        self.drop_zone.configure(fg_color=("#e8f0fe", "#1a1e2e"), border_color=("#4285f4", "#5a8dee"))
        center = ctk.CTkFrame(self.drop_zone, fg_color="transparent")
        center.place(relx=0.5, rely=0.5, anchor="center")
        ctk.CTkLabel(center, text=f"⏳ {msg}", font=ctk.CTkFont(size=12, weight="bold"), text_color=("#1a56db", "#7eadf5")).pack()
        self._bind_drop_zone_click()

    def _render_drop_zone_loaded(self, files: list[str]):
        for w in self.drop_zone.winfo_children():
            w.destroy()
        self.drop_zone.configure(fg_color=("#e6f4ea", "#1a2e1e"), border_color=("#34a853", "#4caf65"))
        row = ctk.CTkFrame(self.drop_zone, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=(8, 2))
        ctk.CTkLabel(row, text=f"✓ 已加载 {len(files)} 个文件", font=ctk.CTkFont(size=12, weight="bold"), text_color=("#1e7e34", "#6fcf85")).pack(side="left")
        ctk.CTkButton(row, text="重新选择", width=68, height=22, corner_radius=6, fg_color="transparent", border_width=1, border_color=("#34a853", "#4caf65"), command=self._on_select_files).pack(side="right")
        for path in files[:2]:
            ctk.CTkLabel(self.drop_zone, text=f" ▸ {os.path.basename(path)}", font=ctk.CTkFont(size=11), text_color=("#2d6a3f", "#88c99a"), anchor="w").pack(anchor="w", padx=10)
        self._bind_drop_zone_click()

    # ---------- busy state ----------
    def _set_busy(self, reason: str):
        self._task_busy = True
        self._set_progress(max(0.02, float(self.progress_bar.get())), reason)

    def _set_progress(self, frac: float, reason: str):
        self._task_busy = True

        def _do():
            self._touch_granular_progress()
            v = max(0.0, min(1.0, float(frac)))
            self.btn_download.configure(state="disabled")
            self.start_btn.configure(state="disabled", text=f"⏳ {reason}")
            self.progress_bar.set(v)
            self._render_drop_zone_busy(reason)

        self.after(0, _do)

    def _set_progress_value(self, frac: float):
        def _do():
            v = max(0.0, min(1.0, float(frac)))
            if self._task_busy:
                self._touch_granular_progress()
            else:
                self._pulse_stop_go_determinate()
            self.progress_bar.set(v)

        self.after(0, _do)

    def _set_idle(self, keep_loaded: bool = False):
        self._task_busy = False
        self._disarm_pulse_fallback()

        def _flash_completion():
            self._pulse_stop_go_determinate()
            self.btn_download.configure(state="normal")
            self.start_btn.configure(state="normal", text="▶  开始 AI 转写")
            self.progress_bar.configure(mode="determinate")
            self.progress_bar.set(1.0)

        def _reset_bar_and_drop_zone():
            self.progress_bar.set(0)
            if keep_loaded and self.selected_files:
                self._render_drop_zone_loaded(self.selected_files)
            else:
                self._render_drop_zone_idle()

        self.after(0, _flash_completion)
        self.after(280, _reset_bar_and_drop_zone)

    # ---------- subtitle render/edit ----------
    def _render_subtitles(self, data: list[dict]):
        self._subtitle_data = data
        self._subtitle_start_times = [d.get("start_sec", 0.0) for d in data]
        self._sub_page = 0
        self._subtitle_unsaved = False
        self._active_subtitle_abs_idx = -1
        self._subtitle_row_widgets = []
        self._subtitle_text_widgets = []
        self.is_editing = False
        self._render_current_page()

    def _render_current_page(self):
        for w in self.subtitle_scroll.winfo_children():
            w.destroy()
        for w in self.subtitle_nav_frame.winfo_children():
            w.destroy()

        if not self._subtitle_data:
            ctk.CTkLabel(self.subtitle_scroll, text="暂无字幕", text_color=("gray50", "gray55")).pack(pady=40)
            return

        n = len(self._subtitle_data)
        page_size = self._SUB_PAGE_SIZE
        total_pages = max(1, (n + page_size - 1) // page_size)
        pg = max(0, min(self._sub_page, total_pages - 1))
        self._sub_page = pg
        start_i = pg * page_size
        end_i = min(start_i + page_size, n)
        page_data = self._subtitle_data[start_i:end_i]

        ctk.CTkButton(self.subtitle_nav_frame, text="◀ 上一页", width=80, height=30, state="normal" if pg > 0 else "disabled", command=self._subtitle_prev_page).pack(side="left", padx=(8, 4), pady=5)
        ctk.CTkLabel(self.subtitle_nav_frame, text=f"第 {pg+1}/{total_pages} 页 · 第 {start_i+1}-{end_i} 条 / 共 {n} 条", text_color=("gray40", "gray65")).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(self.subtitle_nav_frame, text="下一页 ▶", width=80, height=30, state="normal" if pg < total_pages - 1 else "disabled", command=self._subtitle_next_page).pack(side="right", padx=(4, 8), pady=5)

        self._subtitle_row_widgets = []
        self._subtitle_text_widgets = []
        for i, item in enumerate(page_data):
            row, txt = self._add_subtitle_row(
                self.subtitle_scroll,
                i,
                item.get("time_str", "00:00:00"),
                item.get("text", ""),
                start_sec=item.get("start_sec", 0.0),
                abs_idx=start_i + i,
            )
            self._subtitle_row_widgets.append(row)
            self._subtitle_text_widgets.append(txt)

    def _add_subtitle_row(self, parent, idx: int, timestamp: str, text: str, start_sec: float = 0.0, abs_idx: int = -1):
        row_bg = ("gray91", "gray17") if idx % 2 == 0 else ("gray88", "gray14")
        row = ctk.CTkFrame(parent, corner_radius=8, fg_color=row_bg)
        row.pack(fill="x", padx=10, pady=(0, 3))

        ts = ctk.CTkLabel(
            row, text=timestamp, width=92, height=52, corner_radius=8,
            fg_color="transparent", text_color=("gray48", "gray58"),
            font=ctk.CTkFont(family="Consolas", size=11, weight="bold"),
            cursor="hand2",
        )
        ts.pack(side="left", padx=(8, 6), pady=6)
        ts.bind("<Enter>", lambda e, lb=ts: lb.configure(text_color=("#2563eb", "#60a5fa")))
        ts.bind("<Leave>", lambda e, lb=ts: lb.configure(text_color=("gray48", "gray58")))
        ts.bind("<Button-1>", lambda e, s=start_sec: self._seek_and_play(s))

        txt = ctk.CTkTextbox(row, height=52, corner_radius=8, font=ctk.CTkFont(size=12), wrap="word", fg_color=("gray97", "gray20"), border_width=1, border_color=("gray82", "gray30"))
        txt.pack(side="left", fill="x", expand=True, padx=(0, 10), pady=6)
        txt.insert("1.0", text)
        tk_text = getattr(txt, "_textbox", txt)
        if abs_idx >= 0:
            tk_text.bind("<FocusIn>", lambda e, ai=abs_idx: self._on_edit_start(e, ai))
            tk_text.bind("<FocusOut>", lambda e, ai=abs_idx, t=txt: self._on_edit_end(e, ai, t))
            # 必须由文本框捕获；部分路径下会无参回调，故用默认可选 event（与全局 F9 一致）
            def _ph(ev=None, ai=abs_idx, t=txt):
                return self._on_subtitle_play_hotkey(ev, ai, t)

            tk_text.bind("<F9>", _ph)
            tk_text.bind("<Control-F9>", _ph)
        return row, txt

    def _subtitle_prev_page(self):
        if self._sub_page > 0:
            self._sub_page -= 1
            self._render_current_page()

    def _subtitle_next_page(self):
        total_pages = max(1, (len(self._subtitle_data) + self._SUB_PAGE_SIZE - 1) // self._SUB_PAGE_SIZE)
        if self._sub_page < total_pages - 1:
            self._sub_page += 1
            self._render_current_page()

    def _on_edit_start(self, _event, abs_idx: int):
        self.is_editing = True
        if self.audio_engine.is_loaded and self.audio_engine.is_playing():
            self.audio_engine.pause()
            self.log(f"[Edit] 进入编辑（第 {abs_idx+1} 条），已自动暂停。")
            self._set_player_visual_state()

    def _finish_subtitle_line_edit(self, txt, abs_idx: int, refresh_highlight: bool = True) -> None:
        """将当前文本框内容写回内存并退出编辑态（不写入磁盘，磁盘仍用按钮或 Ctrl+S）。"""
        self._on_subtitle_edit(txt, abs_idx)
        self.is_editing = False
        if refresh_highlight and self.audio_engine.is_loaded and self._subtitle_data:
            self._update_subtitle_highlight(self.audio_engine.get_current_time(), allow_auto_scroll=True)

    def _on_edit_end(self, _event, abs_idx: int, txt):
        self._finish_subtitle_line_edit(txt, abs_idx, refresh_highlight=True)

    def _on_subtitle_play_hotkey(self, _event, abs_idx: int, txt):
        """结束编辑（写回内存 + 失焦）并切换播放/暂停；吞掉事件，避免与全局绑定重复。"""
        self._finish_subtitle_line_edit(txt, abs_idx, refresh_highlight=True)
        self.focus_set()
        self._toggle_playback()
        return "break"

    def _toggle_playback(self) -> None:
        if self._total_duration <= 0:
            return
        if self.audio_engine.is_playing():
            self._on_pause()
        else:
            self._on_play()

    def _on_subtitle_edit(self, txt, abs_idx: int):
        if not self._subtitle_data or abs_idx >= len(self._subtitle_data):
            return
        new_text = txt.get("1.0", "end-1c").strip()
        old_text = self._subtitle_data[abs_idx].get("text", "")
        if new_text != old_text:
            self._subtitle_data[abs_idx]["text"] = new_text
            self._mark_unsaved()

    def _mark_unsaved(self):
        self._subtitle_unsaved = True
        self.subtitle_save_btn.configure(text="💾 保存字幕 ●", fg_color=("dodger blue", "#1a6bbf"))

    def _mark_saved(self):
        self._subtitle_unsaved = False
        self.subtitle_save_btn.configure(text="✓ 已保存", fg_color=("gray80", "gray28"))

    @staticmethod
    def _sec_to_srt_ts(sec: float) -> str:
        sec = max(0.0, sec)
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = int(sec % 60)
        ms = min(999, int(round((sec % 1) * 1000)))
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    def save_subtitle_to_file(self):
        if not self._current_srt_path:
            self.log("[Warn] 当前未绑定字幕文件。")
            return
        try:
            blocks = []
            for item in self._subtitle_data:
                idx = item.get("index", len(blocks) + 1)
                start = self._sec_to_srt_ts(item.get("start_sec", 0.0))
                end = self._sec_to_srt_ts(item.get("end_sec", 0.0))
                text = item.get("text", "").strip()
                blocks.append(f"{idx}\n{start} --> {end}\n{text}")
            Path(self._current_srt_path).write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
            self._mark_saved()
            self.log(f"[Save] ✓ 已保存：{self._current_srt_path}")
        except Exception as exc:
            self.log(f"[Save] ✗ 保存失败：{exc}")

    # ---------- playback ----------
    @staticmethod
    def _fmt_sec(sec: float) -> str:
        sec = max(0.0, sec)
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = int(sec % 60)
        return f"{h}:{m:02d}:{s:02d}"

    def load_transcript(self, srt_path: str, wav_path: str):
        self._current_srt_path = srt_path
        self.log("─" * 40)
        self.log(f"[Load] 字幕：{os.path.basename(srt_path)}")
        self.log(f"[Load] 音频：{os.path.basename(wav_path)}")
        try:
            data = parse_srt(srt_path)
            self.log(f"[Load] SRT 解析完成：{len(data)} 条")
        except Exception as exc:
            self.log(f"[Load] SRT 解析异常：{exc}")
            data = []

        duration = self.audio_engine.load(wav_path)
        self._total_duration = duration
        self.play_slider.configure(to=max(1, duration))
        self.play_slider.set(0)
        self.media_label.configure(text=f"当前文件：{os.path.basename(wav_path)}  [{self._fmt_sec(duration)}]")
        self._render_subtitles(data)
        self._mark_saved()
        try:
            report = analyze_timestamp_quality(wav_path, srt_path)
            for line in format_quality_report(report):
                self.log(line)
        except Exception as exc:
            self.log(f"[TimeDiag] Timestamp quality check failed: {exc}")
        self.log("[Load] 完成。")
        if duration > 0:
            self.after(0, self._start_scope_poll)
        else:
            self._stop_scope_poll()
            self.after(0, self._clear_scope_canvas)

    def _scope_theme_colors(self) -> dict:
        bg, wave, caret = _scope_canvas_bg_and_stroke()
        return {"bg": bg, "wave": wave, "caret": caret}

    def _on_scope_configure(self, event: tk.Event) -> None:
        cv = getattr(self, "scope_canvas", None)
        if cv is None or event.widget is not cv:
            return
        try:
            w = int(event.width)
        except (TypeError, ValueError):
            return
        if w > 8:
            self._scope_layout_w = w

    def _start_scope_poll(self) -> None:
        self._stop_scope_poll()
        self._scope_after_id = self.after(_SCOPE_MS, self._scope_tick)

    def _stop_scope_poll(self) -> None:
        aid = getattr(self, "_scope_after_id", None)
        if aid is not None:
            try:
                self.after_cancel(aid)
            except Exception:
                pass
        self._scope_after_id = None

    def _clear_scope_canvas(self) -> None:
        cv = getattr(self, "scope_canvas", None)
        if cv is None:
            return
        try:
            if cv.winfo_exists():
                cols = self._scope_theme_colors()
                cv.configure(bg=cols["bg"])
                cv.delete("all")
        except tk.TclError:
            pass

    def _scope_tick(self) -> None:
        self._scope_after_id = None
        cv = getattr(self, "scope_canvas", None)
        run_next = False
        try:
            if (
                self.winfo_exists()
                and cv is not None
                and cv.winfo_exists()
                and self._total_duration > 0
                and self.audio_engine.is_loaded
            ):
                run_next = True
                try:
                    self._scope_draw_once()
                except Exception:
                    pass
        except Exception:
            pass
        if run_next:
            try:
                self._scope_after_id = self.after(_SCOPE_MS, self._scope_tick)
            except tk.TclError:
                pass

    def _scope_draw_once(self) -> None:
        cv = getattr(self, "scope_canvas", None)
        if cv is None or not cv.winfo_exists():
            return
        cols = self._scope_theme_colors()
        cv.configure(bg=cols["bg"])
        try:
            cv.update_idletasks()
        except tk.TclError:
            pass
        wt = max(int(cv.winfo_width()), int(self._scope_layout_w or 0), 96)
        h = max(int(cv.winfo_height()), _SCOPE_H - 2, 14)

        sr_e = max(8000, int(self.audio_engine.samplerate))
        window_n = max(640, min(16000, int(sr_e * _SCOPE_WINDOW_SEC)))
        mono_arr = self.audio_engine.get_scope_mono_tail(window_n)
        cv.delete("all")

        pad_x, pad_y = 3.0, 4.0
        plot_w = max(4.0, float(wt - 2 * pad_x))
        plot_h = max(10.0, float(h - 2 * pad_y))
        mid_y = pad_y + plot_h / 2.0
        gain = (plot_h / 2.0) * 0.9

        if mono_arr is not None:
            ln = len(mono_arr)
            if ln >= 2:
                target_pts = max(96, min(288, int(wt * 0.9)))
                coords: list[float] = []
                if ln <= target_pts:
                    denom = float(ln - 1) if ln > 1 else 1.0
                    for j in range(ln):
                        val = float(mono_arr[j])
                        coords.extend([pad_x + (float(j) / denom) * plot_w, mid_y - val * gain])
                else:
                    step = float(ln - 1) / float(target_pts - 1)
                    for jp in range(target_pts):
                        i = min(ln - 1, max(0, int(round(jp * step))))
                        coords.extend(
                            [
                                pad_x + (float(jp) / float(max(1, target_pts - 1))) * plot_w,
                                mid_y - float(mono_arr[i]) * gain,
                            ]
                        )
                if len(coords) >= 4:
                    coord_t = tuple(coords)
                    try:
                        cv.create_line(coord_t, fill=cols["wave"], width=2, smooth=True, splinesteps=4)
                    except tk.TclError:
                        cv.create_line(coord_t, fill=cols["wave"], width=2)

        dur = float(self._total_duration)
        if dur > 0:
            frac = max(0.0, min(1.0, self.audio_engine.get_current_time() / dur))
            xh = pad_x + frac * plot_w
            cv.create_line(xh, pad_y + 2, xh, pad_y + plot_h - 2, fill=cols["caret"], width=1)

    def _seek_and_play(self, start_sec: float):
        if not self.audio_engine.get_status().get("loaded"):
            self.log("[Diag] 音频未加载，无法跳转。")
            return
        # 与进度条拖动一致：仅更新读指针，不重切开正在运行的流
        self.audio_engine.seek(start_sec)
        self.log(f"[Play] ▶ 跳转至 {self._fmt_sec(start_sec)}")
        self._set_player_visual_state()

    def _on_play(self):
        if self._total_duration <= 0:
            self.log("[Warn] 请先加载音频。")
            return
        if self.audio_engine.is_paused():
            self.audio_engine.resume()
            self.log("[Info] ▶ 继续播放")
        else:
            cur = self.audio_engine.get_current_time()
            self.audio_engine.play(cur if cur > 0 else 0.0)
            self.log("[Info] ▶ 开始播放")
        self._set_player_visual_state()

    def _on_pause(self):
        self.audio_engine.pause()
        self.log(f"[Info] ⏸ 暂停于 {self._fmt_sec(self.audio_engine.get_current_time())}")
        self._set_player_visual_state()

    def _set_player_visual_state(self):
        playing = self.audio_engine.is_playing()
        if playing:
            self.play_btn.configure(fg_color=("#3b82f6", "#2563eb"), text_color=("white", "white"))
            self.pause_btn.configure(fg_color=("gray80", "gray28"), text_color=("gray10", "gray90"))
        else:
            self.pause_btn.configure(fg_color=("#f59e0b", "#d97706"), text_color=("white", "white"))
            self.play_btn.configure(fg_color=("gray80", "gray28"), text_color=("gray10", "gray90"))

    def _on_slider_drag(self, value: float):
        self._slider_dragging = True
        self._pending_seek_sec = float(value)
        if self._seek_after_id:
            self.after_cancel(self._seek_after_id)
        self._seek_after_id = self.after(150, self._do_slider_seek)

    def _do_slider_seek(self):
        self._slider_dragging = False
        self._seek_after_id = None
        if self._total_duration > 0:
            self.audio_engine.seek(self._pending_seek_sec)

    def update_playback_ui(self):
        try:
            if self.audio_engine.is_loaded:
                now = self.audio_engine.get_current_time()
                if self._total_duration > 0 and not self._slider_dragging:
                    self.play_slider.set(now)
                if self._subtitle_data:
                    self._update_subtitle_highlight(now, allow_auto_scroll=not self.is_editing)
        except Exception:
            pass
        finally:
            self.after(100, self.update_playback_ui)

    def _update_subtitle_highlight(self, current_time: float, allow_auto_scroll: bool = True):
        import bisect

        starts = self._subtitle_start_times
        if not starts:
            return
        idx = bisect.bisect_right(starts, current_time) - 1
        if idx >= 0:
            end_sec = self._subtitle_data[idx].get("end_sec", float("inf"))
            active = idx if current_time <= end_sec else -1
        else:
            active = -1

        if active == self._active_subtitle_abs_idx:
            return
        self._active_subtitle_abs_idx = active
        self._clear_subtitle_highlight()
        if active == -1:
            return

        page_size = self._SUB_PAGE_SIZE
        active_page = active // page_size
        if active_page != self._sub_page:
            if not allow_auto_scroll:
                return
            self._sub_page = active_page
            self._render_current_page()

        local_idx = active - self._sub_page * page_size
        if 0 <= local_idx < len(self._subtitle_row_widgets):
            row = self._subtitle_row_widgets[local_idx]
            try:
                row.configure(fg_color=("#bfdbfe", "#1e3a5f"))
            except Exception:
                pass
            if allow_auto_scroll:
                self._scroll_to_subtitle_row(local_idx)

    def _clear_subtitle_highlight(self):
        for i, row in enumerate(self._subtitle_row_widgets):
            d = ("gray91", "gray17") if i % 2 == 0 else ("gray88", "gray14")
            try:
                row.configure(fg_color=d)
            except Exception:
                pass

    def _scroll_to_subtitle_row(self, page_local_idx: int):
        """卡拉 OK 高亮行滚动：正文框几何中心对齐到字幕区视口垂直中心。"""
        total = len(self._subtitle_row_widgets)
        if total <= 0 or not (0 <= page_local_idx < total):
            return
        txt = self._subtitle_text_widgets[page_local_idx]
        sf = self.subtitle_scroll  # CTkScrollableFrame 自身即为 Canvas 内的可滚动 Tk Frame（无 _scrollable_frame）

        try:
            now = time.time()
            if now - self._last_scroll_ts < 0.08:
                return
            self._last_scroll_ts = now

            canvas = getattr(sf, "_parent_canvas", None)
            wid = getattr(sf, "_create_window_id", None)
            if canvas is None:
                return

            sf.update_idletasks()
            canvas.update_idletasks()
            txt.update_idletasks()

            bbox = canvas.bbox("all")
            if not bbox:
                return
            cy1 = float(bbox[1])
            cy2 = float(bbox[3])
            canvas_h = max(1, canvas.winfo_height())
            content_h = max(1.0, cy2 - cy1)
            scroll_px = content_h - float(canvas_h)
            if scroll_px <= 1.0:
                return

            win_y = 0.0
            if wid is not None:
                try:
                    c = canvas.coords(wid)
                    if c and len(c) >= 2:
                        win_y = float(c[1])
                except Exception:
                    pass

            # 正文框顶部相对 sf 坐标系累计 winfo_y，再加半高为中心
            y_rel = 0.0
            wtk = txt
            while wtk is not None and wtk is not sf:
                y_rel += float(wtk.winfo_y())
                wtk = wtk.master
            if wtk is not sf:
                return

            center_rel = y_rel + float(txt.winfo_height()) * 0.5
            viewport_top_goal = win_y + center_rel - float(canvas_h) * 0.5
            rel_top = viewport_top_goal - cy1

            frac = max(0.0, min(1.0, rel_top / scroll_px))
            canvas.yview_moveto(frac)
        except Exception:
            pass

    def _on_load_test_srt(self):
        wav = filedialog.askopenfilename(title="① 选择 WAV 音频", filetypes=[("WAV", "*.wav"), ("所有", "*.*")])
        if not wav:
            return
        srt = filedialog.askopenfilename(title="② 选择 SRT 字幕", filetypes=[("SRT", "*.srt"), ("所有", "*.*")])
        if not srt:
            return
        self.load_transcript(srt, wav)

    # ---------- settings ----------
    def _refresh_main_template_menu(self, selected_name: str = ""):
        names = list(self._prompts.keys()) or ["精炼复习笔记"]
        target = selected_name or self._cfg.get("template_name", "")
        if target not in names:
            target = names[0]
        self.main_template_var.set(target)
        self._cfg["template_name"] = target
        show_name = target if len(target) <= 10 else target[:9] + "…"
        self.btn_notes_arrow.configure(text=f"{show_name}  ▾")

    def _refresh_model_hint(self):
        model = str(self._cfg.get("model_name", "")).strip() or "--"
        show = model if len(model) <= 24 else model[:23] + "…"
        self.model_hint_label.configure(text=f"模型：{show}")

    def _on_main_template_change(self, name: str):
        self._refresh_main_template_menu(name)
        self._save_runtime_config()

    def _show_main_template_menu(self):
        self._reload_prompt_library()
        names = list(self._prompts.keys()) or ["精炼复习笔记"]
        current = self.main_template_var.get().strip()
        menu = tk.Menu(self, tearoff=0, bg="#2c2c2c", fg="#dddddd", activebackground="#3a3a3a", activeforeground="#ffffff", bd=0)
        for name in names:
            prefix = "✓ " if name == current else "  "
            menu.add_command(label=f"{prefix}{name}", command=lambda n=name: self._on_main_template_change(n))
        try:
            x = self.btn_notes_arrow.winfo_rootx()
            y = self.btn_notes_arrow.winfo_rooty() + self.btn_notes_arrow.winfo_height()
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _open_settings_dialog(self):
        if self._settings_win is not None and self._settings_win.winfo_exists():
            self._settings_win.focus_force()
            self._settings_win.lift()
            return
        win = ctk.CTkToplevel(self)
        self._settings_win = win
        win.title("⚙ 配置中心")
        win.geometry("920x640")
        win.minsize(840, 560)
        win.transient(self)
        win.lift()
        win.focus_force()
        # 先置顶再取消，确保不会被主窗口遮挡
        try:
            win.attributes("-topmost", True)
            win.after(350, lambda: win.attributes("-topmost", False))
        except Exception:
            pass
        tab = ctk.CTkTabview(win, corner_radius=10)
        tab.pack(fill="both", expand=True, padx=14, pady=14)
        self._build_settings_general_tab(tab.add("模型与通用配置"))
        self._build_settings_prompts_tab(tab.add("Prompt 模板库"))

    def _build_settings_general_tab(self, parent):
        wrap = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        wrap.pack(fill="both", expand=True)
        card = ctk.CTkFrame(wrap, corner_radius=12)
        card.pack(fill="x", padx=14, pady=14)
        ctk.CTkLabel(card, text="LLM 运营商快捷选择", font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", padx=14, pady=(12, 8))

        self.cfg_provider_var = tk.StringVar(value=self._cfg.get("provider", "自定义"))
        self.cfg_provider_menu = ctk.CTkOptionMenu(card, values=list(_PROVIDER_PRESETS.keys()), variable=self.cfg_provider_var, height=34, corner_radius=8, command=self._on_provider_change)
        self.cfg_provider_menu.pack(fill="x", padx=14, pady=(0, 12))

        def _row(lbl, ph, show=""):
            row = ctk.CTkFrame(card, fg_color="transparent")
            row.pack(fill="x", padx=14, pady=(0, 10))
            ctk.CTkLabel(row, text=lbl, width=90, anchor="w").pack(side="left")
            ent = ctk.CTkEntry(row, placeholder_text=ph, height=34, corner_radius=8, show=show)
            ent.pack(side="left", fill="x", expand=True)
            return ent

        self.cfg_base_url = _row("Base URL", "https://api.openai.com/v1")
        self.cfg_api_key = _row("API Key", "sk-xxx", show="•")
        self.cfg_model = _row("Model Name", "gpt-4o-mini")
        self.cfg_base_url.insert(0, str(self._cfg.get("api_base_url", "")))
        self.cfg_api_key.insert(0, str(self._cfg.get("api_key", "")))
        self.cfg_model.insert(0, str(self._cfg.get("model_name", "")))

        # 模型动态拉取区：避免手输模型名，支持 OpenAI 兼容接口与 Ollama
        model_pick_row = ctk.CTkFrame(card, fg_color="transparent")
        model_pick_row.pack(fill="x", padx=14, pady=(0, 10))
        ctk.CTkLabel(model_pick_row, text="可用模型", width=90, anchor="w").pack(side="left")
        self.cfg_model_choice_var = tk.StringVar(value="（点击右侧按钮拉取）")
        self.cfg_model_choice = ctk.CTkOptionMenu(
            model_pick_row,
            values=["（点击右侧按钮拉取）"],
            variable=self.cfg_model_choice_var,
            height=32,
            corner_radius=8,
            command=self._on_model_choice_change,
        )
        self.cfg_model_choice.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.cfg_fetch_models_btn = ctk.CTkButton(
            model_pick_row,
            text="拉取模型",
            width=110,
            height=32,
            corner_radius=8,
            fg_color="transparent",
            border_width=1,
            border_color=("#2563eb", "#60a5fa"),
            hover_color=("#dbeafe", "#1e3a8a"),
            text_color=("#1d4ed8", "#93c5fd"),
            command=self._on_fetch_models_click,
        )
        self.cfg_fetch_models_btn.pack(side="left", padx=(8, 0))
        self.cfg_fetch_status = ctk.CTkLabel(
            card,
            text="状态：待拉取",
            anchor="w",
            font=ctk.CTkFont(size=11),
            text_color=("gray45", "gray60"),
        )
        self.cfg_fetch_status.pack(fill="x", padx=14, pady=(0, 8))

        row_out = ctk.CTkFrame(card, fg_color="transparent")
        row_out.pack(fill="x", padx=14, pady=(0, 12))
        ctk.CTkLabel(row_out, text="输出目录", width=90, anchor="w").pack(side="left")
        self.cfg_output_entry = ctk.CTkEntry(row_out, height=34, corner_radius=8)
        self.cfg_output_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.cfg_output_entry.insert(0, self.output_dir.get())
        ctk.CTkButton(
            row_out, text="浏览", width=64, height=34, corner_radius=8,
            fg_color="transparent", border_width=1, border_color=("gray60", "gray40"),
            text_color=("gray25", "gray85"), hover_color=("gray88", "gray25"),
            command=self._on_browse_output_in_settings
        ).pack(side="left")

        ctk.CTkButton(
            card, text="保存配置", height=38, corner_radius=10,
            fg_color="transparent", border_width=1, border_color=("#2563eb", "#60a5fa"),
            text_color=("#1d4ed8", "#93c5fd"), hover_color=("#dbeafe", "#1e3a8a"),
            command=self._on_save_general_config
        ).pack(fill="x", padx=14, pady=(2, 12))

    def _build_settings_prompts_tab(self, parent):
        body = ctk.CTkFrame(parent, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=12, pady=12)
        left = ctk.CTkFrame(body, width=230, corner_radius=10)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        ctk.CTkLabel(left, text="模板列表", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", padx=10, pady=(10, 6))
        self.prompt_list_frame = ctk.CTkScrollableFrame(left, fg_color="transparent")
        self.prompt_list_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        ctk.CTkButton(
            left, text="➕ 新增模板", height=34, corner_radius=8,
            fg_color="transparent", border_width=1, border_color=("#2563eb", "#60a5fa"),
            text_color=("#1d4ed8", "#93c5fd"), hover_color=("#dbeafe", "#1e3a8a"),
            command=self._on_new_prompt_template
        ).pack(fill="x", padx=8, pady=(0, 8))

        right = ctk.CTkFrame(body, corner_radius=10)
        right.pack(side="left", fill="both", expand=True, padx=(10, 0))
        ctk.CTkLabel(right, text="模板名称").pack(anchor="w", padx=12, pady=(10, 4))
        self.prompt_name_entry = ctk.CTkEntry(right, height=34, corner_radius=8)
        self.prompt_name_entry.pack(fill="x", padx=12)
        ctk.CTkLabel(right, text="提示词内容").pack(anchor="w", padx=12, pady=(10, 4))
        self.prompt_content_box = ctk.CTkTextbox(right, corner_radius=8, wrap="word")
        self.prompt_content_box.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        act = ctk.CTkFrame(right, fg_color="transparent")
        act.pack(fill="x", padx=12, pady=(0, 12))
        ctk.CTkButton(
            act, text="💾 保存当前模板", height=36, corner_radius=8,
            fg_color="transparent", border_width=1, border_color=("#2563eb", "#60a5fa"),
            text_color=("#1d4ed8", "#93c5fd"), hover_color=("#dbeafe", "#1e3a8a"),
            command=self._on_save_prompt_template
        ).pack(side="left", fill="x", expand=True, padx=(0, 6))
        ctk.CTkButton(
            act, text="🗑️ 删除当前模板", height=36, corner_radius=8,
            fg_color="transparent", border_width=1, border_color=("#ef4444", "#f87171"),
            text_color=("#dc2626", "#fca5a5"), hover_color=("#fee2e2", "#7f1d1d"),
            command=self._on_delete_prompt_template
        ).pack(side="left")
        self._render_prompt_list(selected_name=self._cfg.get("template_name", ""))

    def _on_provider_change(self, provider_name: str):
        base_url, model = _PROVIDER_PRESETS.get(provider_name, ("", ""))
        if base_url:
            self.cfg_base_url.delete(0, "end")
            self.cfg_base_url.insert(0, base_url)
        if model:
            self.cfg_model.delete(0, "end")
            self.cfg_model.insert(0, model)

    def _on_model_choice_change(self, name: str):
        name = (name or "").strip()
        if not name or name.startswith("（"):
            return
        self.cfg_model.delete(0, "end")
        self.cfg_model.insert(0, name)

    def _set_fetch_status(self, text: str, level: str = "info"):
        color = ("gray45", "gray60")
        if level == "ok":
            color = ("#065f46", "#34d399")
        elif level == "warn":
            color = ("#92400e", "#fbbf24")
        elif level == "error":
            color = ("#991b1b", "#f87171")
        self.cfg_fetch_status.configure(text=f"状态：{text}", text_color=color)

    def _set_model_choices(self, names: list[str]):
        cleaned: list[str] = []
        for n in names:
            s = str(n).strip()
            if s and s not in cleaned:
                cleaned.append(s)
        self._fetched_models = cleaned
        if not cleaned:
            self.cfg_model_choice.configure(values=["（未获取到模型）"])
            self.cfg_model_choice_var.set("（未获取到模型）")
            self._set_fetch_status("未获取到模型", "warn")
            return
        self.cfg_model_choice.configure(values=cleaned)
        current = self.cfg_model.get().strip()
        pick = current if current in cleaned else cleaned[0]
        self.cfg_model_choice_var.set(pick)
        self.cfg_model.delete(0, "end")
        self.cfg_model.insert(0, pick)
        self._set_fetch_status(f"已获取 {len(cleaned)} 个模型", "ok")

    def _on_fetch_models_click(self):
        base_url = self.cfg_base_url.get().strip()
        api_key = self.cfg_api_key.get().strip()
        if not base_url:
            messagebox.showwarning("提示", "请先填写 Base URL 再拉取模型。")
            return
        self.cfg_fetch_models_btn.configure(state="disabled", text="拉取中…")
        self._set_fetch_status("正在拉取…", "info")
        self.log(f"[Info] 正在拉取模型列表：{base_url}")
        base_url_stripped = base_url.rstrip("/")
        threading.Thread(
            target=self._thread_fetch_models,
            args=(base_url, api_key, base_url_stripped),
            daemon=True,
        ).start()

    def _thread_fetch_models(self, base_url: str, api_key: str, base_url_stripped: str):
        try:
            names, endpoint = self._fetch_remote_models(base_url, api_key)
        except Exception as exc:
            self.after(0, lambda e=str(exc): self._on_fetch_models_done([], e, ""))
            return
        self.after(0, lambda n=names, ep=endpoint, bu=base_url_stripped: self._on_fetch_models_done(n, "", ep, bu))

    def _on_fetch_models_done(self, names: list[str], err: str, endpoint: str, base_url: str = ""):
        self.cfg_fetch_models_btn.configure(state="normal", text="拉取模型")
        if err:
            summary = "未知错误"
            detail = err
            if "::" in err:
                summary, detail = err.split("::", 1)
            self._set_fetch_status(summary, "error")
            self.log(f"[Warn] 模型列表拉取失败：{summary} | {detail}")
            messagebox.showwarning("拉取失败", f"{summary}\n\n诊断信息：\n{detail}")
            return
        if base_url:
            self._fetched_models_base_url = base_url
        self._set_model_choices(names)
        if endpoint:
            self.log(f"[成功] 已拉取 {len(names)} 个可用模型（来源：{endpoint}）。")
        else:
            self.log(f"[成功] 已拉取 {len(names)} 个可用模型。")

    def _fetch_remote_models(self, base_url: str, api_key: str) -> tuple[list[str], str]:
        """
        从远端拉取模型列表：
        - OpenAI 兼容：GET {base}/models 或 {base}/v1/models
        - Ollama：GET {host}/api/tags
        """
        base = base_url.strip().rstrip("/")
        if not base:
            raise RuntimeError("Base URL 为空")

        def _http_get_json(url: str) -> dict:
            headers = {"Accept": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            req = urllib.request.Request(url=url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=12) as resp:
                raw = resp.read()
            return json.loads(raw.decode("utf-8", errors="replace"))

        urls: list[str] = []
        # Ollama 兼容：优先访问 /api/tags
        if "11434" in base or "ollama" in base.lower():
            host = base
            if host.endswith("/v1"):
                host = host[:-3]
            urls.append(f"{host}/api/tags")
        # OpenAI 兼容接口
        urls.append(f"{base}/models")
        if not base.endswith("/v1"):
            urls.append(f"{base}/v1/models")

        # 去重保序
        unique_urls: list[str] = []
        for u in urls:
            if u not in unique_urls:
                unique_urls.append(u)

        errors: list[str] = []
        for url in unique_urls:
            try:
                data = _http_get_json(url)
                names: list[str] = []
                if url.endswith("/api/tags"):
                    for item in data.get("models", []):
                        name = str(item.get("name", "")).strip()
                        if name:
                            names.append(name)
                else:
                    arr = data.get("data", data.get("models", []))
                    if isinstance(arr, list):
                        for item in arr:
                            if not isinstance(item, dict):
                                continue
                            name = (
                                str(item.get("id", "")).strip()
                                or str(item.get("name", "")).strip()
                                or str(item.get("model", "")).strip()
                            )
                            if name:
                                names.append(name)
                # 去重并返回
                out: list[str] = []
                for n in names:
                    if n not in out:
                        out.append(n)
                if out:
                    return out, url
                errors.append(f"{url} 返回空列表")
            except urllib.error.HTTPError as exc:
                body = ""
                try:
                    body = exc.read().decode("utf-8", errors="replace")
                except Exception:
                    body = str(exc)
                if exc.code in (401, 403):
                    errors.append(f"鉴权失败(HTTP {exc.code})::{url}；请检查 API Key。响应：{body[:200]}")
                elif exc.code == 404:
                    errors.append(f"接口路径不存在(HTTP 404)::{url}；请检查 Base URL 是否包含 /v1。响应：{body[:200]}")
                else:
                    errors.append(f"HTTP 错误(HTTP {exc.code})::{url}；响应：{body[:200]}")
            except urllib.error.URLError as exc:
                errors.append(f"网络连接失败::{url}；{exc}")
            except TimeoutError as exc:
                errors.append(f"请求超时::{url}；{exc}")
            except json.JSONDecodeError as exc:
                errors.append(f"响应解析失败::{url}；非 JSON 响应，{exc}")
            except OSError as exc:
                errors.append(f"系统网络错误::{url}；{exc}")

        if errors:
            top = errors[0]
            if "::" in top:
                raise RuntimeError(top)
            raise RuntimeError(f"拉取失败::{top}")
        raise RuntimeError("拉取失败::未知错误")

    def _on_browse_output_in_settings(self):
        chosen = filedialog.askdirectory(title="选择输出目录", initialdir=self.cfg_output_entry.get().strip() or self.output_dir.get())
        if chosen:
            self.cfg_output_entry.delete(0, "end")
            self.cfg_output_entry.insert(0, chosen)

    def _on_save_general_config(self):
        out_dir = self.cfg_output_entry.get().strip() or self.output_dir.get()
        self._cfg.update({
            "provider": self.cfg_provider_var.get().strip() or "自定义",
            "api_base_url": self.cfg_base_url.get().strip(),
            "api_key": self.cfg_api_key.get().strip(),
            "model_name": self.cfg_model.get().strip(),
            "output_dir": out_dir,
        })
        self._save_runtime_config()
        self.output_dir.set(out_dir)
        self._refresh_model_hint()
        self.log("[Info] 配置已保存。")

    def _render_prompt_list(self, selected_name: str = ""):
        self._reload_prompt_library()
        for w in self.prompt_list_frame.winfo_children():
            w.destroy()
        names = list(self._prompts.keys())
        if not names:
            return
        if selected_name not in names:
            selected_name = names[0]
        for name in names:
            sel = name == selected_name
            ctk.CTkButton(
                self.prompt_list_frame, text=name, height=32, anchor="w", corner_radius=8,
                fg_color=("#3b82f6", "#1d4ed8") if sel else ("gray80", "gray26"),
                hover_color=("#2563eb", "#1e40af") if sel else ("gray70", "gray34"),
                command=lambda n=name: self._select_prompt_template(n),
            ).pack(fill="x", pady=(0, 6))
        self._select_prompt_template(selected_name, refresh_list=False)

    def _select_prompt_template(self, name: str, refresh_list: bool = True):
        if refresh_list:
            self._render_prompt_list(selected_name=name)
            return
        self._editing_prompt_name = name
        self.prompt_name_entry.delete(0, "end")
        self.prompt_name_entry.insert(0, name)
        self.prompt_content_box.delete("1.0", "end")
        self.prompt_content_box.insert("1.0", self._prompts.get(name, ""))

    def _on_new_prompt_template(self):
        self._reload_prompt_library()
        base = "新模板"
        i = 1
        name = base
        while name in self._prompts:
            i += 1
            name = f"{base}{i}"
        self._prompt_mgr.upsert(name, "")
        self._render_prompt_list(selected_name=name)

    def _on_save_prompt_template(self):
        old_name = self._editing_prompt_name.strip()
        new_name = self.prompt_name_entry.get().strip()
        content = self.prompt_content_box.get("1.0", "end-1c").strip()
        if not new_name:
            messagebox.showwarning("提示", "模板名不能为空")
            return
        self._reload_prompt_library()
        if new_name != old_name and new_name in self._prompts:
            messagebox.showwarning("提示", f"模板「{new_name}」已存在")
            return
        try:
            if old_name and old_name in self._prompts and old_name != new_name:
                all_prompts = dict(self._prompts)
                all_prompts.pop(old_name, None)
                all_prompts[new_name] = content
                self._prompt_mgr.save_all(all_prompts)
            else:
                self._prompt_mgr.upsert(new_name, content)
            self._reload_prompt_library()
            self._cfg["template_name"] = new_name
            self._save_runtime_config()
            self._refresh_main_template_menu(new_name)
            self._render_prompt_list(new_name)
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc))

    def _on_delete_prompt_template(self):
        name = self._editing_prompt_name.strip()
        if not name:
            return
        if not messagebox.askyesno("确认删除", f"确定删除模板「{name}」吗？"):
            return
        try:
            self._prompt_mgr.delete(name)
            self._reload_prompt_library()
            next_name = list(self._prompts.keys())[0]
            self._cfg["template_name"] = next_name
            self._save_runtime_config()
            self._refresh_main_template_menu(next_name)
            self._render_prompt_list(next_name)
        except Exception as exc:
            messagebox.showwarning("删除失败", str(exc))

    # ---------- pipeline hooks（后台线程通过 cpwpro.transcribe 调用）----------
    def _simple_pipeline_hooks(self) -> SimplePipelineHooks:
        return SimplePipelineHooks(log=self.log, set_progress=self._set_progress, set_idle=self._set_idle)

    def _transcribe_pipeline_hooks(self) -> TranscribePipelineHooks:
        def sync(i: int, t: int) -> None:
            self.after(0, lambda ii=i, tt=t: self._sync_transcribe_slot_main(ii, tt))

        def record_server(p: Optional[object]) -> None:
            if p is not None:
                self._server_proc = p  # type: ignore[assignment]

        return TranscribePipelineHooks(
            log=self.log,
            set_progress=self._set_progress,
            set_idle=self._set_idle,
            sync_transcribe_slot=sync,
            schedule_load_transcript=lambda srt, wav: self.after(0, self.load_transcript, str(srt), wav),
            run_autocleanup_if_enabled=lambda: self._do_cleanup() if self.auto_clean.get() else None,
            record_server_process=record_server,
        )

    # ---------- tasks ----------
    def _on_download_click(self):
        raw = self.url_entry.get().strip()
        if not raw:
            self.log("[Warn] 请先输入链接或 BV 号。")
            return
        url = normalize_url(raw)
        if url != raw:
            self.url_entry.delete(0, "end")
            self.url_entry.insert(0, url)
        tmp_dir = os.path.join(self.output_dir.get(), "tmp_download")
        self._set_busy("正在解析并下载…")
        threading.Thread(target=self._thread_download_and_extract, args=(url, tmp_dir), daemon=True).start()

    def _on_select_files(self):
        if self._is_primary_task_busy():
            self.log("[Warn] 当前有任务进行中，请稍后再选择文件。")
            return
        files = filedialog.askopenfilenames(
            title="选择音视频文件",
            filetypes=[("音视频", "*.mp3 *.mp4 *.wav *.m4a *.mkv *.flac *.aac *.ogg *.webm *.mov"), ("所有", "*.*")],
        )
        if not files:
            return
        self._submit_local_media_files(files)

    def _on_browse_output(self):
        chosen = filedialog.askdirectory(title="选择输出目录", initialdir=self.output_dir.get())
        if chosen:
            self.output_dir.set(chosen)
            self._cfg["output_dir"] = chosen
            self._save_runtime_config()

    def _on_start_transcribe(self):
        if not self.ready_audio_files:
            self.log("[Warn] 请先准备音频文件。")
            return
        if not self.auto_transcribe.get():
            self.log("[Info] 自动转写已关闭。")
            return
        if self.vad_transcribe.get():
            self._set_busy("正在VAD切片转写…")
            target = self._thread_transcribe_all_vad
        else:
            self._set_busy("正在转写…")
            target = self._thread_transcribe_all
        threading.Thread(target=target, args=(list(self.ready_audio_files), self.output_dir.get()), daemon=True).start()

    def _thread_download_and_extract(self, url: str, tmp_dir: str):
        run_download_then_extract(
            url,
            tmp_dir,
            self.output_dir.get(),
            fast_mode=bool(self.fast_download.get()),
            cleanup_queue=self._cleanup_queue,
            hooks=self._simple_pipeline_hooks(),
            keep_loaded_on_fail=lambda: bool(self.selected_files),
            ready_audio_files=self.ready_audio_files,
        )

    def _thread_extract_all(self, files: list[str], out_dir: str):
        run_extract_all(files, out_dir, hooks=self._simple_pipeline_hooks(), ready_audio_files=self.ready_audio_files)

    def _thread_transcribe_all(self, wav_files: list[str], out_dir: str):
        run_transcribe_all(wav_files, out_dir, hooks=self._transcribe_pipeline_hooks())

    def _thread_transcribe_all_vad(self, wav_files: list[str], out_dir: str):
        run_transcribe_all_vad(wav_files, out_dir, hooks=self._transcribe_pipeline_hooks())

    def _do_cleanup(self):
        deleted = 0
        for path in self._cleanup_queue:
            try:
                if os.path.exists(path):
                    os.remove(path)
                    deleted += 1
            except Exception:
                pass
        self._cleanup_queue.clear()
        self.ready_audio_files.clear()
        self.log(f"[Info] 清理完成，删除 {deleted} 个文件。")

    # ---------- llm ----------
    def _on_generate_notes(self):
        if not self._subtitle_data:
            self.log("[Warn] 请先加载字幕。")
            return

        self._cfg = self._cfg_mgr.load()
        self._reload_prompt_library()

        api_key = self._cfg.get("api_key", "").strip()
        if not api_key:
            messagebox.showwarning("缺少配置", "请先在配置中心填写 API Key。")
            return

        base_url = self._cfg.get("api_base_url", "https://api.openai.com/v1").strip()
        model = self._cfg.get("model_name", "gpt-4o-mini").strip()
        # Phase D：生成前校验模型名，避免提交后才报错
        if self._fetched_models and self._fetched_models_base_url == base_url.rstrip("/"):
            if model not in self._fetched_models:
                suggested = self._fetched_models[0]
                use_suggested = messagebox.askyesno(
                    "模型名不可用",
                    f"当前模型「{model}」不在已拉取列表中。\n\n"
                    f"是否改用推荐模型「{suggested}」并继续？",
                )
                if not use_suggested:
                    self.log("[Warn] AI 生成已取消：模型名不在可用列表中。")
                    return
                model = suggested
                self._cfg["model_name"] = suggested
                self._save_runtime_config()
                self._refresh_model_hint()
                self.log(f"[Info] 已自动切换到可用模型：{suggested}")
        elif self._fetched_models and self._fetched_models_base_url != base_url.rstrip("/"):
            self.log("[Info] 检测到模型缓存来自其他 Base URL，本次跳过模型匹配校验。")
        tpl_name = self.main_template_var.get().strip() or self._cfg.get("template_name", "")
        if not tpl_name:
            tpl_name = next(iter(self._prompts.keys()), "精炼复习笔记")
        self._cfg["template_name"] = tpl_name
        self._save_runtime_config()
        sys_p = self._prompts.get(tpl_name, "").strip()
        if not sys_p:
            messagebox.showwarning("模板为空", f"模板「{tpl_name}」内容为空。")
            return

        lines = []
        for item in self._subtitle_data:
            ts = item.get("time_str", "??:??:??")
            tx = item.get("text", "").strip()
            if tx:
                lines.append(f"[{ts}] {tx}")
        transcript = "\n".join(lines)

        if self._notes_win is None or not self._notes_win.winfo_exists():
            self._notes_win = _NoteWindow(self, tpl_name, model)
        else:
            self._notes_win.reset(tpl_name, model)
            self._notes_win.focus_force()
        win = self._notes_win

        # LLM 阶段进度：从 0.80 开始推进
        self._set_progress_value(0.80)

        def _worker():
            msgs = build_messages(sys_p, transcript)
            try:
                full = ""
                prog = 0.80
                for chunk in stream_chat(base_url=base_url, api_key=api_key, model=model, messages=msgs, temperature=0.65, max_tokens=8192, log_fn=self.log):
                    full += chunk
                    prog = min(0.98, prog + max(0.002, min(0.02, len(chunk) / 5000.0)))
                    self._set_progress_value(prog)
                    self.after(0, win.append_chunk, chunk)
                self._set_progress_value(1.0)
                self.after(0, win.on_done, full)
                self.after(1200, lambda: self._set_progress_value(0.0))
            except Exception as exc:
                self._set_progress_value(0.0)
                self.after(0, win.on_error, str(exc))

        threading.Thread(target=_worker, daemon=True).start()

    def _apply_taskbar_icon(self) -> None:
        ico = _BASE / "assets" / "icon.ico"
        if ico.is_file():
            try:
                self.iconbitmap(default=str(ico))
            except Exception:
                pass

    def _finalize_and_exit(self) -> None:
        if self._finalizing_exit:
            return
        self._finalizing_exit = True
        self._stop_scope_poll()
        try:
            self.audio_engine.teardown()
        except Exception:
            pass
        if self._server_proc is not None and self._server_proc.poll() is None:
            try:
                self._server_proc.terminate()
                self._server_proc.wait(timeout=3)
            except Exception:
                try:
                    self._server_proc.kill()
                except Exception:
                    pass
        self._tray.stop()
        try:
            self.destroy()
        except Exception:
            pass

    def _on_app_close(self) -> None:
        """主窗右上角 ×：托盘可用 → 缩小到托盘；否则真正退出。"""
        if tray_disabled_by_env():
            self._finalize_and_exit()
            return

        hide_to_tray = not tray_hide_disabled_by_env()

        # 在用户点 × 时再尝试一次幂等启动，消除「首帧之前就关窗」的竞态。
        if hide_to_tray:
            self._tray.start(self.log)

        if hide_to_tray and getattr(self._tray, "active", False):
            try:
                self.withdraw()
                return
            except Exception:
                pass

        self._finalize_and_exit()


class _NoteWindow(ctk.CTkToplevel):
    def __init__(self, master, template_name: str, model: str):
        super().__init__(master)
        self.title(f"🤖 AI 笔记 · {template_name}")
        self.geometry("780x640")
        self.minsize(580, 420)
        self.transient(master)
        self._full_text = ""
        self._tpl = template_name
        self._model = model
        self._build_ui()
        self._set_status("⏳ 正在生成...", "#f59e0b")

    def _build_ui(self):
        top = ctk.CTkFrame(self, fg_color=("gray90", "gray16"), corner_radius=0, height=44)
        top.pack(fill="x")
        top.pack_propagate(False)
        ctk.CTkLabel(top, text=f"模板：{self._tpl}", text_color=("gray35", "gray65")).pack(side="left", padx=14, pady=10)
        ctk.CTkLabel(top, text=f"模型：{self._model}", text_color=("gray45", "gray55")).pack(side="left")
        self.status_label = ctk.CTkLabel(top, text="", font=ctk.CTkFont(size=11))
        self.status_label.pack(side="right", padx=14)

        self.note_box = ctk.CTkTextbox(self, state="disabled", font=ctk.CTkFont(size=13), wrap="word", corner_radius=0)
        self.note_box.pack(fill="both", expand=True)

        foot = ctk.CTkFrame(self, fg_color=("gray90", "gray16"), corner_radius=0, height=54)
        foot.pack(fill="x")
        foot.pack_propagate(False)
        self.btn_copy = ctk.CTkButton(foot, text="📋 复制全文", width=110, height=34, state="disabled", command=self._copy_to_clipboard)
        self.btn_copy.pack(side="left", padx=(12, 6), pady=10)
        self.btn_save = ctk.CTkButton(foot, text="💾 保存为 .md", width=120, height=34, state="disabled", command=self._save_as_markdown)
        self.btn_save.pack(side="left", pady=10)
        ctk.CTkButton(foot, text="✕ 关闭", width=72, height=34, command=self.destroy).pack(side="right", padx=12, pady=10)

    def _set_status(self, text: str, color: str):
        self.status_label.configure(text=text, text_color=color)

    def reset(self, tpl: str, model: str):
        self._full_text = ""
        self._tpl = tpl
        self._model = model
        self.title(f"🤖 AI 笔记 · {tpl}")
        self.note_box.configure(state="normal")
        self.note_box.delete("1.0", "end")
        self.note_box.configure(state="disabled")
        self.btn_copy.configure(state="disabled")
        self.btn_save.configure(state="disabled")
        self._set_status("⏳ 正在生成...", "#f59e0b")

    def append_chunk(self, chunk: str):
        if not self.winfo_exists():
            return
        self._full_text += chunk
        self.note_box.configure(state="normal")
        self.note_box.insert("end", chunk)
        self.note_box.configure(state="disabled")
        self.note_box.see("end")
        self._set_status(f"⏳ 生成中... {len(self._full_text)} 字", "#f59e0b")

    def on_done(self, full: str):
        if not self.winfo_exists():
            return
        self._full_text = full
        self.btn_copy.configure(state="normal")
        self.btn_save.configure(state="normal")
        self._set_status(f"✓ 完成 {len(full)} 字", "#22c55e")

    def on_error(self, err: str):
        if not self.winfo_exists():
            return
        self.note_box.configure(state="normal")
        self.note_box.insert("end", f"\n\n⚠ 生成失败：\n{err}")
        self.note_box.configure(state="disabled")
        self.note_box.see("end")
        self._set_status("✗ 生成失败", "#ef4444")

    def _copy_to_clipboard(self):
        self.clipboard_clear()
        self.clipboard_append(self._full_text)
        self._set_status("✓ 已复制", "#22c55e")

    def _save_as_markdown(self):
        path = filedialog.asksaveasfilename(
            title="保存 AI 笔记",
            defaultextension=".md",
            filetypes=[("Markdown", "*.md"), ("文本", "*.txt"), ("所有", "*.*")],
            initialfile=f"笔记_{self._tpl}_{datetime.now().strftime('%Y%m%d_%H%M')}.md",
        )
        if not path:
            return
        try:
            Path(path).write_text(self._full_text, encoding="utf-8")
            self._set_status(f"✓ 已保存：{Path(path).name}", "#22c55e")
        except Exception as exc:
            self._set_status(f"✗ 保存失败：{exc}", "#ef4444")


def main() -> None:
    apply_ctk_defaults()
    app = App()
    try:
        app.mainloop()
    finally:
        app._tray.stop()


if __name__ == "__main__":
    main()
