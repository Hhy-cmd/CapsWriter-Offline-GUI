# -*- coding: utf-8 -*-
"""CapsWriter 项目根路径（内含 start_client.exe / start_server.exe）。"""
from __future__ import annotations

from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parent


def project_root() -> Path:
    """`cpwpro` 上一级目录即为发行根目录。"""
    return _PKG_DIR.parent

