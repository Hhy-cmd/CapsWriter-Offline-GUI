# -*- coding: utf-8 -*-
"""CPW-Pro：基于 CapsWriter-Offline 的 GUI 与工作流扩展包。

工作台支撑代码位于 `cpwpro.support`（配置 / LLM HTTP 客户端 / 本地播放与 SRT / VAD 切段 / 时间戳诊断），
与引擎侧 `core_*`、`util/server` 等核心转写链路分离。
"""
from __future__ import annotations

from cpwpro.paths import project_root

__all__ = ["project_root"]
