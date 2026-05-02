# -*- coding: utf-8 -*-
"""
CPW-Pro 任务进度条：从日志行解析粒度进度（下载 %、[核心] 发送/转录秒数）。

纯函数与正则集中于此，便于单测；UI 侧仅负责节流、CTkProgressBar 与 indeterminate 回退。
"""

from __future__ import annotations

import re
from typing import Optional

# 与 App._append_log 中 sniff 一致：含「[下载]」「[核心]」等关键字
_DOWNLOAD_PCT_FROM_LOG_RE = re.compile(r"\[下载\][^\n]*?([\d.]+)\s*%")
_CORE_SEND_PROGRESS_RE = re.compile(
    r"(?:\[核心\]\s*)?发送进度\s*[：:]\s*([\d.]+)\s*s\s*/\s*([\d.]+)\s*s",
    re.I,
)
_CORE_TRANSCRIBE_SEC_RE = re.compile(r"(?:\[核心\]\s*)?转录进度\s*[：:]\s*([\d.]+)\s*s?", re.I)

# 与历史 App 行为一致：避免日志风暴刷进度条
DOWNLOAD_LOG_THROTTLE_SEC = 0.12
CORE_TRANSCRIBE_LOG_THROTTLE_SEC = 0.05


def sniff_after_timestamp_prefix(line: str) -> str:
    """去掉 '[HH:MM:SS]  ' 前缀，得到与进度解析器匹配的正文。"""
    ts_end = line.find("] ")
    return line[ts_end + 2 :] if ts_end >= 0 else line


def transcribe_bar_segment(batch_idx: int, batch_total: int) -> tuple[float, float]:
    """与 _thread_transcribe_all 阶梯一致：第 i 个文件占用 [lo, hi)。"""
    t = max(1, int(batch_total))
    i = max(0, min(t - 1, int(batch_idx)))
    lo = 0.62 + (i / t) * 0.35
    hi = 0.62 + ((i + 1) / t) * 0.35
    return lo, hi


def parse_download_bar_fraction(line: str) -> Optional[float]:
    """
    从含 [下载] 与百分比的日志行得到进度条位置（约 0.06~0.46）。
    若无匹配或数值非法则返回 None。
    """
    m = _DOWNLOAD_PCT_FROM_LOG_RE.search(line)
    if not m:
        return None
    try:
        pct = float(m.group(1))
    except ValueError:
        return None
    if not (0.0 <= pct <= 100.0):
        return None
    return max(0.06, min(0.46, 0.08 + (pct / 100.0) * 0.38))


def parse_core_send_sec_pair(line: str) -> Optional[tuple[float, float]]:
    """发送进度 cur/tot（秒）；无匹配则 None。"""
    m = _CORE_SEND_PROGRESS_RE.search(line)
    if not m:
        return None
    try:
        cur_sec = float(m.group(1))
        tot_sec = float(m.group(2))
    except ValueError:
        return None
    if tot_sec <= 0:
        return None
    return cur_sec, tot_sec


def parse_core_transcribe_sec(line: str) -> Optional[float]:
    m = _CORE_TRANSCRIBE_SEC_RE.search(line)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def fraction_from_send_progress(cur_sec: float, tot_sec: float, lo: float, hi: float) -> float:
    span = max(hi - lo, 0.004)
    r = max(0.0, min(1.0, cur_sec / tot_sec))
    return lo + r * span * 0.995


def fraction_from_transcribe_sec(
    sec: float,
    audio_total_sec: Optional[float],
    lo: float,
    hi: float,
    last_sec_line: float,
    cur_bar: float,
) -> tuple[Optional[float], float]:
    """
    转录进度行映射到条上位置。

    若已知音频总长（来自发送进度行），按比例；否则按秒数阶梯式微增。

    返回 (new_fraction 或 None 表示跳过本行, 更新后的 last_sec_line)。
    当按总长比例计算时，last_sec_line 不变（调用方应忽略第二项或保持原值）。
    """
    span = max(hi - lo, 0.004)
    tot = audio_total_sec
    if tot is not None and tot > 0:
        r = max(0.0, min(1.0, sec / tot))
        return lo + r * span * 0.995, last_sec_line
    if sec <= last_sec_line:
        return None, last_sec_line
    cb = max(float(cur_bar), lo)
    frac = min(hi - 0.003, cb + span * 0.04)
    return frac, sec


def looks_like_core_progress_line(line: str) -> bool:
    return "发送进度" in line or "转录进度" in line
