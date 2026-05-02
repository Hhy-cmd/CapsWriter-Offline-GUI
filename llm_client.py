# -*- coding: utf-8 -*-
"""
llm_client.py — CPW-Pro LLM 流式调用客户端

设计目标
─────────────────────────────────────────────────────────────────
· 仅使用 Python 标准库（urllib / ssl / json），零额外依赖。
· 兼容所有 OpenAI 格式的 API：
    OpenAI / DeepSeek / 阿里 Qwen / 月之暗面 Kimi /
    智谱 GLM / 百川 / Ollama 本地部署 / 任何 /v1/chat/completions 端点。
· 支持 Server-Sent Events（SSE）流式响应，逐 token 实时 yield。
· 超时 / HTTP 错误 / 网络错误均抛出 RuntimeError，调用方统一处理。

典型用法
─────────────────────────────────────────────────────────────────
for chunk in stream_chat(base_url, api_key, model, messages):
    print(chunk, end="", flush=True)
"""

import json
import ssl
import urllib.error
import urllib.request
from typing import Callable, Generator, Optional


def stream_chat(
    base_url:    str,
    api_key:     str,
    model:       str,
    messages:    list[dict],
    temperature: float = 0.7,
    max_tokens:  int   = 4096,
    log_fn: Optional[Callable[[str], None]] = None,
) -> Generator[str, None, None]:
    """
    向 OpenAI 兼容 API 发起流式 Chat Completion 请求。

    参数
    ──────────────────────────────────────────────────────────────
    base_url     API 根地址，如 "https://api.openai.com/v1"
                 或本地 Ollama "http://localhost:11434/v1"
    api_key      API 密钥；Ollama 等本地服务可传 "ollama" 或任意字符串
    model        模型名称，如 "gpt-4o-mini" / "deepseek-chat" / "qwen-plus"
    messages     标准 OpenAI messages 列表，格式：
                 [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}]
    temperature  采样温度，0.0 ~ 2.0，建议 0.5 ~ 0.8
    max_tokens   最大生成 token 数
    log_fn       日志回调，传入 App.log 可将诊断信息显示到 UI

    Yield
    ──────────────────────────────────────────────────────────────
    逐块 yield str（模型输出的增量文本），直到流结束。

    异常
    ──────────────────────────────────────────────────────────────
    RuntimeError  包含 HTTP 状态码或网络错误详情，由调用方捕获并显示给用户。

    SSL 说明
    ──────────────────────────────────────────────────────────────
    部分本地部署使用自签证书，此处对 HTTPS 跳过证书验证（只影响本工具，
    不影响系统信任链）。若需严格验证，可将 ctx.verify_mode 改回 CERT_REQUIRED。
    """
    # ── 构造请求 URL ──────────────────────────────────────────────────────────
    endpoint = base_url.rstrip("/") + "/chat/completions"
    if log_fn:
        log_fn(f"[LLM] → {endpoint}  模型：{model}")

    # ── 请求体 ─────────────────────────────────────────────────────────────────
    payload = json.dumps({
        "model":       model,
        "messages":    messages,
        "stream":      True,
        "temperature": temperature,
        "max_tokens":  max_tokens,
    }, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        endpoint,
        data=payload,
        headers={
            "Content-Type":  "application/json",
            "Authorization": f"Bearer {api_key}",
            "Accept":        "text/event-stream",
            "User-Agent":    "CPW-Pro/4.0",
        },
        method="POST",
    )

    # ── SSL 上下文（宽松模式，兼容本地自签证书） ──────────────────────────────
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode    = ssl.CERT_NONE

    # ── 发送请求并读取 SSE 流 ─────────────────────────────────────────────────
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=180) as resp:
            for raw_line in resp:
                line: str = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                # SSE 格式：每行以 "data: " 开头
                if line.startswith("data:"):
                    line = line[5:].lstrip()
                # 流结束标志
                if line == "[DONE]":
                    return
                # 解析 JSON 增量
                try:
                    chunk   = json.loads(line)
                    delta   = chunk["choices"][0].get("delta", {})
                    content = delta.get("content") or ""
                    if content:
                        yield content
                except (json.JSONDecodeError, IndexError, KeyError, TypeError):
                    # 部分平台会在流中夹杂非 JSON 行（如注释），直接跳过
                    continue

    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"HTTP {exc.code} {exc.reason}\n\n"
            f"响应体（前 500 字符）：\n{body[:500]}"
        )
    except urllib.error.URLError as exc:
        raise RuntimeError(f"网络错误：{exc.reason}")
    except TimeoutError:
        raise RuntimeError("请求超时（180 秒），请检查网络或 API 端点。")


def build_messages(system_prompt: str, user_content: str) -> list[dict]:
    """
    构造标准 OpenAI messages 列表。

    参数
    ──────────────────────────────────────────────────────────────
    system_prompt   系统提示词（定义 AI 角色与任务）
    user_content    用户输入（转写字幕文本）

    返回
    ──────────────────────────────────────────────────────────────
    [{"role": "system", "content": system_prompt},
     {"role": "user",   "content": user_content}]
    """
    return [
        {"role": "system", "content": system_prompt.strip()},
        {"role": "user",   "content": user_content.strip()},
    ]
