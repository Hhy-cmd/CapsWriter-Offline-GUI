# -*- coding: utf-8 -*-
"""兼容导出，实现已迁至 cpwpro.progress。"""
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

__all__ = [
    "CORE_TRANSCRIBE_LOG_THROTTLE_SEC",
    "DOWNLOAD_LOG_THROTTLE_SEC",
    "fraction_from_send_progress",
    "fraction_from_transcribe_sec",
    "looks_like_core_progress_line",
    "parse_core_send_sec_pair",
    "parse_core_transcribe_sec",
    "parse_download_bar_fraction",
    "sniff_after_timestamp_prefix",
    "transcribe_bar_segment",
]
