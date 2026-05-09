# -*- coding: utf-8 -*-
"""
CPW-Pro 后台任务管线：下载→抽轨、批量 ffmpeg、CapsWriter 多文件/VAD 转写。

仅从 worker 线程调用；通过 hooks 回填 UI（hooks 内需自行 schedule 主线程）。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from cpwpro.worker import CPWWorker
from cpwpro.support.vad_utils import split_audio_smart, stitch_srt_chunks


def find_capswriter_srt(wav_path: str | Path) -> Path | None:
    p = Path(wav_path)
    cands = [p.with_suffix(".srt"), p.parent / (p.stem.replace("_16k", "") + ".srt")]
    return next((x for x in cands if x.is_file()), None)


@dataclass
class SimplePipelineHooks:
    """下载 / 仅抽轨等非转写管线。"""

    log: Callable[[str], None]
    set_progress: Callable[[float, str], None]
    set_idle: Callable[[bool], None]


@dataclass
class TranscribePipelineHooks:
    """多文件/VAD 转写。"""

    log: Callable[[str], None]
    set_progress: Callable[[float, str], None]
    set_idle: Callable[[bool], None]
    sync_transcribe_slot: Callable[[int, int], None]
    schedule_load_transcript: Callable[[str, str], None]
    run_autocleanup_if_enabled: Callable[[], None]
    record_server_process: Callable[[Optional[object]], None]


def run_download_then_extract(
    url: str,
    tmp_dir: str,
    output_dir: str,
    *,
    fast_mode: bool,
    cleanup_queue: list,
    hooks: SimplePipelineHooks,
    keep_loaded_on_fail: Callable[[], bool],
    ready_audio_files: list,
) -> None:
    hooks.set_progress(0.06, "正在下载…")
    video = CPWWorker.download_video(url, tmp_dir, hooks.log, fast_mode=fast_mode)
    if not video:
        hooks.set_idle(keep_loaded=keep_loaded_on_fail())
        return
    cleanup_queue.append(video)
    hooks.set_progress(0.50, "正在提取音频…")
    wav = CPWWorker.extract_audio(video, output_dir, hooks.log)
    if not wav:
        hooks.set_idle(keep_loaded=keep_loaded_on_fail())
        return
    ready_audio_files.append(wav)
    hooks.set_idle(keep_loaded=keep_loaded_on_fail())


def run_extract_all(
    files: list[str],
    out_dir: str,
    *,
    hooks: SimplePipelineHooks,
    ready_audio_files: list,
) -> None:
    ok = 0
    total = max(1, len(files))
    for idx, src in enumerate(files):
        p = Path(src)
        hooks.set_progress(0.12 + (idx / total) * 0.40, f"正在提取音频 {idx + 1}/{total}…")
        if p.suffix.lower() == ".wav" and "_16k" in p.stem:
            ready_audio_files.append(src)
            ok += 1
            continue
        wav = CPWWorker.extract_audio(src, out_dir, hooks.log)
        if wav:
            ready_audio_files.append(wav)
            ok += 1
    hooks.set_progress(0.58, "提取完成")
    n = len(files)
    if ok == n:
        hooks.log(f"[Info] 提取成功：{ok}/{n}")
    elif ok == 0:
        hooks.log(f"[Error] 全部提取失败（{ok}/{n}）。请根据上方 FFmpeg 报错检查源文件是否完整、可播放。")
    else:
        hooks.log(f"[Warn] 部分提取成功：{ok}/{n}，未成功的文件已跳过。")
    hooks.set_idle(keep_loaded=True)


def run_transcribe_all(
    wav_files: list[str],
    out_dir: str,
    *,
    hooks: TranscribePipelineHooks,
) -> None:
    proc = CPWWorker.ensure_server_running(hooks.log)
    if proc:
        hooks.record_server_process(proc)
    last_wav: str | None = None
    total = max(1, len(wav_files))
    for idx, wav in enumerate(wav_files):
        hooks.sync_transcribe_slot(idx, total)
        hooks.set_progress(0.62 + (idx / total) * 0.35, f"正在转写 {idx + 1}/{total}…")
        CPWWorker.run_capswriter(wav, out_dir, hooks.log)
        found = find_capswriter_srt(wav)
        if found:
            last_wav = wav
    if last_wav:
        found = find_capswriter_srt(last_wav)
        if found:
            hooks.schedule_load_transcript(str(found), last_wav)
    hooks.run_autocleanup_if_enabled()
    hooks.set_progress(1.0, "转写完成")
    hooks.set_idle(keep_loaded=False)


def run_transcribe_all_vad(
    wav_files: list[str],
    out_dir: str,
    *,
    hooks: TranscribePipelineHooks,
) -> None:
    proc = CPWWorker.ensure_server_running(hooks.log)
    if proc:
        hooks.record_server_process(proc)

    last_result: tuple[str, str] | None = None
    total_files = max(1, len(wav_files))

    try:
        for file_idx, wav in enumerate(wav_files):
            wav_path = Path(wav)
            hooks.sync_transcribe_slot(file_idx, total_files)
            hooks.log(f"[VAD] 分析音频：{wav_path.name}")
            hooks.set_progress(0.60 + (file_idx / total_files) * 0.08, "VAD分析音频…")

            task_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            task_dir = Path(out_dir) / "chunks" / f"{wav_path.stem}_{task_stamp}"
            if task_dir.exists():
                shutil.rmtree(task_dir, ignore_errors=True)
            task_dir.mkdir(parents=True, exist_ok=True)

            chunk_info = split_audio_smart(wav_path, task_dir)
            chunk_count = len(chunk_info)
            hooks.log(f"[VAD] 切片完成，共 {chunk_count} 块。")

            if chunk_count <= 0:
                hooks.log("[VAD] 未生成有效切片，跳过该音频。")
                shutil.rmtree(task_dir, ignore_errors=True)
                continue

            chunk_paths = [str(Path(info["filepath"])) for info in chunk_info]
            hooks.set_progress(0.68 + (file_idx / total_files) * 0.25, f"VAD批量转写 {chunk_count} 块…")
            hooks.log(f"[VAD] 批量提交 {chunk_count} 块，减少客户端反复启动。")
            batch_ok = CPWWorker.run_capswriter_batch(chunk_paths, hooks.log)

            if not batch_ok:
                hooks.log("[VAD] 批量提交失败，回退为逐块转写。")
                for idx, info in enumerate(chunk_info, start=1):
                    chunk_path = Path(info["filepath"])
                    lo = 0.68 + (file_idx / total_files) * 0.25
                    hi = 0.68 + ((file_idx + 1) / total_files) * 0.25
                    frac = lo + (idx - 1) / max(1, chunk_count) * max(0.01, hi - lo)
                    hooks.set_progress(frac, f"VAD回退转写 {idx}/{chunk_count}…")
                    hooks.log(
                        f"[VAD] 回退转写第 {idx}/{chunk_count} 块："
                        f"{info.get('offset_sec', 0.0):.2f}s + {info.get('duration_sec', 0.0):.2f}s"
                    )
                    ok = CPWWorker.run_capswriter(str(chunk_path), str(task_dir), hooks.log)
                    if not ok:
                        raise RuntimeError(f"chunk {idx}/{chunk_count} 转写失败：{chunk_path.name}")

            for idx, info in enumerate(chunk_info, start=1):
                chunk_path = Path(info["filepath"])
                srt = find_capswriter_srt(chunk_path)
                if not srt:
                    raise FileNotFoundError(f"chunk {idx}/{chunk_count} 未生成 SRT：{chunk_path.name}")
                info["srt_path"] = str(srt)

            final_srt = Path(out_dir) / f"{wav_path.stem}_vad.srt"
            hooks.set_progress(0.95, "正在重组SRT…")
            stitched = stitch_srt_chunks(chunk_info, final_srt)
            hooks.log(f"[VAD] 重组完成：{stitched}")
            last_result = (stitched, str(wav_path))

            shutil.rmtree(task_dir, ignore_errors=True)
            hooks.log("[VAD] 已清理本次切片临时目录。")

        if last_result:
            hooks.schedule_load_transcript(last_result[0], last_result[1])

        hooks.run_autocleanup_if_enabled()
        hooks.set_progress(1.0, "VAD转写完成")
    except Exception as exc:
        hooks.log(f"[VAD] 转写流程失败：{exc}")
    finally:
        hooks.set_idle(keep_loaded=False)
