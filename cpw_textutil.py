# -*- coding: utf-8 -*-
"""CPW-Pro 纯文本工具：日志 ANSI 剥离、链接栏 URL / BV 规范化。"""

from __future__ import annotations

import re

_ANSI_RE = re.compile(r"\x1b(?:\[[0-9;?]*[A-Za-z]|[()][A-B]|[A-Z\\^_`{}|~])")
_BV_STANDALONE_RE = re.compile(r"^BV[a-zA-Z0-9]{10,}$")
_AV_STANDALONE_RE = re.compile(r"^av\d+$", re.IGNORECASE)


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text).replace("\r", "")


def normalize_url(raw: str) -> str:
    """
    将输入清洗为可用视频页 URL：
    1) 允许用户直接粘贴 BV/av 号
    2) 若输入中混入日志文本，只提取首个 http(s) URL
    """
    s = raw.strip()
    if not s:
        return ""

    m_url = re.search(r"https?://[^\s'\"<>]+", s, flags=re.IGNORECASE)
    if m_url:
        s = m_url.group(0).strip()
        s = s.replace("开始解析链接：", "").replace("开始解析链接:", "").strip()
        return s

    m_bv = re.search(r"BV[a-zA-Z0-9]{10,}", s)
    if m_bv:
        return f"https://www.bilibili.com/video/{m_bv.group(0)}"
    m_av = re.search(r"av\d+", s, flags=re.IGNORECASE)
    if m_av:
        return f"https://www.bilibili.com/video/{m_av.group(0)}"

    if _BV_STANDALONE_RE.match(s) or _AV_STANDALONE_RE.match(s):
        return f"https://www.bilibili.com/video/{s}"
    return s
