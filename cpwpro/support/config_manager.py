# -*- coding: utf-8 -*-
"""
config_manager.py

配置中心文件读写层（与 UI 解耦）：
1) config.json   - 应用通用配置（API、模型、输出目录等）
2) prompts.json  - Prompt 模板库（模板名 -> 模板内容）
"""

from __future__ import annotations

import json
from pathlib import Path
from threading import RLock
from typing import Optional


DEFAULT_CONFIG: dict = {
    "provider": "自定义",
    "api_base_url": "https://api.openai.com/v1",
    "api_key": "",
    "model_name": "gpt-4o-mini",
    "template_name": "精炼复习笔记",
    "output_dir": "output",
}


DEFAULT_PROMPTS: dict[str, str] = {
    "精炼复习笔记": (
        "你是一位高效的学习助手。请将以下语音转写字幕整理为结构清晰的复习笔记，"
        "要求使用 Markdown，包含：主题概述、核心要点、关键术语、行动建议。"
    ),
    "全量知识库归档": (
        "你是一位知识管理专家。请把字幕内容转为可长期归档文档，"
        "要求：分层标题、概念索引、关键论据、结论摘要。"
    ),
    "会议纪要整理": (
        "你是一位专业会议记录员。请提炼会议纪要，包含：会议主题、参与方观点、"
        "决议事项、待办清单（责任人/截止时间）。"
    ),
}


def _safe_read_json(path: Path) -> dict:
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


class AppConfigManager:
    """管理 config.json 的读写与默认值补齐。"""

    def __init__(self, path: Path, default_config: Optional[dict] = None):
        self.path = path
        self.default_config = dict(DEFAULT_CONFIG if default_config is None else default_config)
        self._lock = RLock()
        self.ensure_file()

    def ensure_file(self) -> None:
        """确保配置文件存在；不存在时写入默认配置。"""
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if not self.path.is_file():
                self.path.write_text(
                    json.dumps(self.default_config, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                return

            data = _safe_read_json(self.path)
            merged = dict(self.default_config)
            merged.update(data)
            self.path.write_text(
                json.dumps(merged, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def load(self) -> dict:
        """读取配置并自动补齐默认字段。"""
        with self._lock:
            data = _safe_read_json(self.path)
            merged = dict(self.default_config)
            merged.update(data)
            return merged

    def save(self, cfg: dict) -> dict:
        """保存配置（会与默认值合并并返回最终结果）。"""
        with self._lock:
            merged = dict(self.default_config)
            merged.update(cfg or {})
            self.path.write_text(
                json.dumps(merged, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return merged

    def update(self, patch: dict) -> dict:
        """读取当前配置并应用增量更新。"""
        with self._lock:
            curr = self.load()
            curr.update(patch or {})
            self.path.write_text(
                json.dumps(curr, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return curr


class PromptLibraryManager:
    """管理 prompts.json 的模板增删改查。"""

    def __init__(self, path: Path, default_prompts: Optional[dict[str, str]] = None):
        self.path = path
        self.default_prompts = dict(DEFAULT_PROMPTS if default_prompts is None else default_prompts)
        self._lock = RLock()
        self.ensure_file()

    def ensure_file(self) -> None:
        """确保模板库文件存在；不存在时写入默认模板。"""
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if not self.path.is_file():
                self.path.write_text(
                    json.dumps(self.default_prompts, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                return

            data = _safe_read_json(self.path)
            if not data:
                data = dict(self.default_prompts)
            self.path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def load_all(self) -> dict[str, str]:
        """读取全部模板（若损坏则回退默认模板）。"""
        with self._lock:
            data = _safe_read_json(self.path)
            valid = {
                str(k): str(v)
                for k, v in data.items()
                if str(k).strip()
            }
            if not valid:
                valid = dict(self.default_prompts)
                self.path.write_text(
                    json.dumps(valid, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            return valid

    def save_all(self, templates: dict[str, str]) -> dict[str, str]:
        """整体覆盖保存模板库。"""
        with self._lock:
            cleaned = {
                str(k).strip(): str(v)
                for k, v in (templates or {}).items()
                if str(k).strip()
            }
            if not cleaned:
                cleaned = dict(self.default_prompts)
            self.path.write_text(
                json.dumps(cleaned, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return cleaned

    def list_names(self) -> list[str]:
        """返回模板名称列表（按写入顺序）。"""
        return list(self.load_all().keys())

    def get(self, name: str) -> str:
        """按名称获取模板内容，若不存在返回空字符串。"""
        return self.load_all().get(name, "")

    def upsert(self, name: str, content: str) -> dict[str, str]:
        """新增或更新模板。"""
        clean_name = str(name).strip()
        if not clean_name:
            raise ValueError("模板名称不能为空")
        with self._lock:
            all_templates = self.load_all()
            all_templates[clean_name] = str(content)
            return self.save_all(all_templates)

    def delete(self, name: str) -> dict[str, str]:
        """删除模板；若仅剩最后一个模板则拒绝删除。"""
        with self._lock:
            all_templates = self.load_all()
            if name not in all_templates:
                return all_templates
            if len(all_templates) <= 1:
                raise ValueError("至少保留一个模板")
            all_templates.pop(name, None)
            return self.save_all(all_templates)

