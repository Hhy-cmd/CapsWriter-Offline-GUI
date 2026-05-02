# -*- coding: utf-8 -*-
"""兼容导出，实现已迁至 cpwpro.transcribe。"""
from cpwpro.transcribe import (
    SimplePipelineHooks,
    TranscribePipelineHooks,
    find_capswriter_srt,
    run_download_then_extract,
    run_extract_all,
    run_transcribe_all,
    run_transcribe_all_vad,
)

__all__ = [
    "SimplePipelineHooks",
    "TranscribePipelineHooks",
    "find_capswriter_srt",
    "run_download_then_extract",
    "run_extract_all",
    "run_transcribe_all",
    "run_transcribe_all_vad",
]
