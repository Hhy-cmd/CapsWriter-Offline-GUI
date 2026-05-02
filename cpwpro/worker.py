# -*- coding: utf-8 -*-
"""
CPW-Pro 与 CapsWriter 核心引擎之间的胶水层（子进程与外部命令）。

不负责 ASR 推理；封装 yt-dlp 下载、ffmpeg 抽轨、按需拉起 start_server、
调用 start_client 转写文件。供 GUI (`cpwpro.ui.app`) 与其它入口复用。
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
from pathlib import Path
from typing import Callable

from cpwpro.paths import project_root

_ROOT = project_root()
_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _http_headers_for_url(url: str) -> dict[str, str]:
    """仅对需伪装的站点加 Referer；泛用网页音频若误带 B 站 Referer 易被 CDN 拒绝。"""
    h = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
    }
    u = url.lower()
    if any(x in u for x in ("bilibili.com", "b23.tv", "bilivideo.com")):
        h["Referer"] = "https://www.bilibili.com/"
    return h


def _decode_subprocess_stdout_bytes(raw: bytes) -> str:
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except Exception:
            pass
    return raw.decode("utf-8", errors="replace")


class CPWWorker:
    """无状态封装：下载、音频提取、服务端探测、CapsWriter 客户端子进程。"""

    @staticmethod
    def download_video(url: str, tmp_dir: str, log_fn: Callable[[str], None], fast_mode: bool = False) -> str | None:
        try:
            import yt_dlp
        except ImportError:
            log_fn("[Error] 未安装 yt-dlp，请先执行：pip install yt-dlp")
            return None

        os.makedirs(tmp_dir, exist_ok=True)

        def _hook(d: dict):
            s = d.get("status", "")
            if s == "downloading":
                log_fn(
                    f"[下载] {d.get('_percent_str','?%').strip()} "
                    f"速度 {d.get('_speed_str','N/A').strip()} "
                    f"剩余 {d.get('_eta_str','N/A').strip()}"
                )
            elif s == "finished":
                log_fn(f"[下载] 分片完成 → {os.path.basename(d.get('filename', ''))}")

        def _has_aria2c() -> bool:
            try:
                r = subprocess.run(
                    ["aria2c", "--version"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=0x08000000,
                )
                return r.returncode == 0
            except Exception:
                return False

        aria_available = _has_aria2c()

        def _run_once(relaxed_ssl: bool, *, use_aria: bool) -> str | None:
            opts = {
                "outtmpl": os.path.join(tmp_dir, "%(title)s.%(ext)s"),
                # 极速模式仅用更高分片并发 +（可选）aria2；不写 abr≤128，避免部分站点无可匹配格式。
                "format": "bestaudio/best",
                "quiet": True,
                "no_warnings": True,
                "progress_hooks": [_hook],
                "merge_output_format": "mp4",
                "noplaylist": True,
                "concurrent_fragment_downloads": 12 if fast_mode else 8,
                "retries": 10,
                "fragment_retries": 10,
                "extractor_retries": 5,
                "socket_timeout": 30,
                "nocheckcertificate": relaxed_ssl,
                "prefer_insecure": relaxed_ssl,
                "http_headers": _http_headers_for_url(url),
            }
            if use_aria and aria_available:
                opts["external_downloader"] = "aria2c"
                opts["external_downloader_args"] = [
                    "-x", "20" if fast_mode else "16",
                    "-s", "20" if fast_mode else "16",
                    "-k", "512K" if fast_mode else "1M",
                    "--summary-interval=0",
                    "--allow-overwrite=true",
                ]
                log_fn("[Info] 检测到 aria2c，已启用高速并发下载。")
            if fast_mode:
                log_fn("[Info] 极速下载模式已开启（更高分片并发）。")
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                out = ydl.prepare_filename(info)
                if not os.path.exists(out):
                    cands = sorted(Path(tmp_dir).iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
                    if cands:
                        out = str(cands[0])
                if os.path.exists(out):
                    return out
                return None

        def _try_download(relaxed_ssl: bool) -> str | None:
            """若已安装 aria2c，先用之；下载异常或未产出文件时退回 yt-dlp 内置下载器。"""
            use_sequence = ([True, False] if aria_available else [False])
            for i, use_aria in enumerate(use_sequence):
                try:
                    out = _run_once(relaxed_ssl=relaxed_ssl, use_aria=use_aria)
                    if out:
                        return out
                    if aria_available and use_aria and i == 0:
                        log_fn("[Warn] aria2 未取得输出文件，改用内置下载器重试…")
                except Exception as ex:
                    if aria_available and use_aria and i == 0:
                        log_fn(f"[Warn] aria2 下载异常，改用内置下载器重试：{ex}")
                        continue
                    raise
            return None

        log_fn(f"[Info] 开始解析链接：{url}")
        try:
            out = _try_download(relaxed_ssl=False)
            if out:
                log_fn(f"[成功] 视频已下载：{os.path.basename(out)}")
                return out
            log_fn("[Error] 找不到下载输出文件。")
            return None
        except Exception as exc:
            msg = str(exc)
            if any(k in msg.upper() for k in ("SSL", "EOF", "CERTIFICATE")):
                log_fn(f"[Warn] 检测到 SSL 网络异常，尝试兼容模式重试：{msg}")
                try:
                    out = _try_download(relaxed_ssl=True)
                    if out:
                        log_fn(f"[成功] 兼容模式下载成功：{os.path.basename(out)}")
                        return out
                    log_fn("[Error] 兼容模式下载后仍未找到输出文件。")
                    return None
                except Exception as exc2:
                    log_fn(f"[Error] 兼容模式下载失败：{exc2}")
                    return None
            log_fn(f"[Error] 下载失败：{exc}")
            return None

    @staticmethod
    def extract_audio(input_path: str, output_dir: str, log_fn: Callable[[str], None]) -> str | None:
        os.makedirs(output_dir, exist_ok=True)
        out_wav = os.path.join(output_dir, f"{Path(input_path).stem}_16k.wav")
        cmd = [
            "ffmpeg",
            "-i",
            input_path,
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            "-y",
            out_wav,
        ]
        try:
            log_fn(f"[ffmpeg] 开始转换：{os.path.basename(input_path)}")
            r = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if r.returncode == 0:
                log_fn(f"[成功] 音频已就绪：{os.path.basename(out_wav)}")
                return out_wav
            for line in [l for l in r.stderr.splitlines() if l.strip()][-5:]:
                log_fn(f"[ffmpeg] {line}")
            log_fn(f"[Error] ffmpeg 失败（退出码 {r.returncode}）")
            return None
        except FileNotFoundError:
            log_fn("[Error] 未找到 ffmpeg，请安装并加入 PATH")
            return None
        except Exception as exc:
            log_fn(f"[Error] 音频提取异常：{exc}")
            return None

    @staticmethod
    def ensure_server_running(log_fn: Callable[[str], None]) -> subprocess.Popen | None:
        host, port = "127.0.0.1", 6016
        server_exe = str(_ROOT / "start_server.exe")

        def _alive() -> bool:
            try:
                with socket.create_connection((host, port), timeout=1):
                    return True
            except OSError:
                return False

        if _alive():
            log_fn("[Info] 服务端已在运行（6016）。")
            return None

        if not os.path.exists(server_exe):
            log_fn(f"[Error] 找不到 start_server.exe：{server_exe}")
            return None

        try:
            log_fn("[Info] 正在启动 start_server.exe ...")
            proc = subprocess.Popen([server_exe], cwd=str(_ROOT), creationflags=0x08000000)
        except Exception as exc:
            log_fn(f"[Error] 启动服务端失败：{exc}")
            return None

        log_fn("[Info] 等待服务端就绪（最多 30 秒）...")
        for _ in range(30):
            time.sleep(1)
            if _alive():
                log_fn("[成功] 服务端已就绪。")
                return proc
        log_fn("[Warn] 服务端等待超时，继续尝试转写。")
        return proc

    @staticmethod
    def run_capswriter(audio_path: str, output_dir: str, log_fn: Callable[[str], None]) -> bool:
        exe = str(_ROOT / "start_client.exe")
        if not os.path.exists(exe):
            log_fn(f"[Error] 找不到 start_client.exe：{exe}")
            return False

        log_fn(f"[转写] 提交：{os.path.basename(audio_path)}")
        ok, code = CPWWorker._run_client_process([exe, audio_path], log_fn)
        if ok:
            log_fn(f"[成功] {Path(audio_path).stem}.srt/.txt/.json 已生成。")
            return True
        if code is not None:
            log_fn(f"[Warn] 客户端退出码 {code}。")
        return False

    @staticmethod
    def _run_client_process(args: list[str], log_fn: Callable[[str], None]) -> tuple[bool, int | None]:
        try:
            proc = subprocess.Popen(
                args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
                cwd=str(_ROOT),
                creationflags=_CREATE_NO_WINDOW,
            )

            buf = bytearray()
            tail = bytearray()
            replied_exit_prompt = False
            while True:
                ch = proc.stdout.read(1) if proc.stdout else b""
                if not ch:
                    break
                tail.extend(ch)
                if len(tail) > 512:
                    del tail[:-512]
                if not replied_exit_prompt and proc.stdin:
                    tail_text = _decode_subprocess_stdout_bytes(bytes(tail))
                    if "按回车" in tail_text or "鎸夊洖杞" in tail_text:
                        try:
                            proc.stdin.write(b"\n")
                            proc.stdin.flush()
                            proc.stdin.close()
                        except Exception:
                            pass
                        replied_exit_prompt = True
                if ch in (b"\r", b"\n"):
                    if buf:
                        s = _decode_subprocess_stdout_bytes(bytes(buf)).strip()
                        if s:
                            log_fn(f"[核心] {s}")
                        buf.clear()
                else:
                    buf.extend(ch)
            if buf:
                s = _decode_subprocess_stdout_bytes(bytes(buf)).strip()
                if s:
                    log_fn(f"[核心] {s}")
            proc.wait()
            return proc.returncode == 0, proc.returncode
        except Exception as exc:
            log_fn(f"[Error] 调用客户端异常：{exc}")
            return False, None

    @staticmethod
    def run_capswriter_batch(audio_paths: list[str], log_fn: Callable[[str], None]) -> bool:
        paths = [str(p) for p in audio_paths if str(p).strip()]
        if not paths:
            return True

        exe = str(_ROOT / "start_client.exe")
        if not os.path.exists(exe):
            log_fn(f"[Error] 找不到 start_client.exe：{exe}")
            return False

        if len(paths) == 1:
            log_fn(f"[转写] 提交：{os.path.basename(paths[0])}")
        else:
            log_fn(f"[转写] 批量提交：{len(paths)} 个文件")

        ok, code = CPWWorker._run_client_process([exe, *paths], log_fn)
        if ok:
            if len(paths) > 1:
                log_fn(f"[成功] 批量转写完成：{len(paths)} 个文件。")
            return True
        if code is not None:
            log_fn(f"[Warn] 批量客户端退出码 {code}。")
        return False
