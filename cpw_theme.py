# -*- coding: utf-8 -*-
"""
CustomTkinter 全局外观与示波 Canvas 配色。
与 Tk 控件布局解耦：他人改主题色时只动本模块即可。
"""
from __future__ import annotations

import customtkinter as ctk

try:
    import darkdetect as _darkdetect_scope
except ImportError:
    _darkdetect_scope = None


def apply_ctk_defaults() -> None:
    """在创建 `App()` 之前调用一次。"""
    ctk.set_appearance_mode("System")
    ctk.set_default_color_theme("blue")


def scope_canvas_bg_and_stroke() -> tuple[str, str, str]:
    """
    与媒体条 ProgressBar 色系一致；原生 tk.Canvas 无 CTk 主题，需给定十六进制色。
    返回：(画布背景, 波形主色, 游标深色)。
    """
    m = ctk.get_appearance_mode()
    dark = m == "Dark" or (
        m != "Light"
        and _darkdetect_scope
        and bool(_darkdetect_scope.isDark())
    )
    if dark:
        return "#2b2b2b", "#5b8def", "#93c5fd"
    return "#ebebeb", "#3b6fb8", "#1d4ed8"
