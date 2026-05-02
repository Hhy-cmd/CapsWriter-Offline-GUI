# -*- coding: utf-8 -*-
"""
VAD-style audio slicing and SRT restitching helpers.

This module is a sidecar for CPW-Pro. It does not touch the core CapsWriter
recognition pipeline; it only prepares smaller WAV chunks and rewrites chunk
SRT timestamps back onto the original audio timeline.
"""

from __future__ import annotations

import re
import shutil
import wave
from pathlib import Path
from typing import Any

try:
    from pydub import AudioSegment
    from pydub.silence import detect_nonsilent
    _PYDUB_IMPORT_ERROR = ""
except Exception as exc:
    AudioSegment = None  # type: ignore[assignment]
    detect_nonsilent = None  # type: ignore[assignment]
    _PYDUB_IMPORT_ERROR = str(exc)


_SRT_TS_RE = re.compile(
    r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*"
    r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})"
)


def _ts_to_sec(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0


def _sec_to_srt_ts(sec: float) -> str:
    sec = max(0.0, float(sec))
    total_ms = int(round(sec * 1000.0))
    ms = total_ms % 1000
    total_sec = total_ms // 1000
    s = total_sec % 60
    total_min = total_sec // 60
    m = total_min % 60
    h = total_min // 60
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _merge_ranges(ranges: list[list[int]], join_gap_ms: int, min_chunk_ms: int) -> list[list[int]]:
    if not ranges:
        return []

    merged: list[list[int]] = []
    for start, end in sorted(ranges):
        if end <= start:
            continue
        if not merged or start - merged[-1][1] > join_gap_ms:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)

    stable: list[list[int]] = []
    for start, end in merged:
        if stable and end - start < min_chunk_ms:
            stable[-1][1] = max(stable[-1][1], end)
        else:
            stable.append([start, end])
    return stable


def _cap_long_ranges(ranges: list[list[int]], max_chunk_ms: int) -> list[list[int]]:
    if max_chunk_ms <= 0:
        return ranges

    capped: list[list[int]] = []
    for start, end in ranges:
        cur = start
        while end - cur > max_chunk_ms:
            capped.append([cur, cur + max_chunk_ms])
            cur += max_chunk_ms
        if end > cur:
            capped.append([cur, end])
    return capped


def split_audio_smart(
    wav_path: str | Path,
    output_dir: str | Path,
    *,
    min_silence_len: int = 700,
    silence_margin_db: float = 15.0,
    buffer_ms: int = 200,
    min_chunk_ms: int = 800,
    join_gap_ms: int = 350,
    max_chunk_ms: int = 60_000,
) -> list[dict[str, Any]]:
    """
    Split a WAV file into non-silent chunks and return absolute offsets.

    Returns:
        [{"filepath": "...", "offset_sec": 12.345, "start_ms": 12345,
          "end_ms": 23456, "duration_sec": 11.111, "srt_path": ""}, ...]
    """
    wav = Path(wav_path)
    if not wav.is_file():
        raise FileNotFoundError(f"WAV not found: {wav}")
    if AudioSegment is None or detect_nonsilent is None:
        return _split_audio_smart_wave_fallback(
            wav,
            output_dir,
            min_silence_len=min_silence_len,
            silence_margin_db=silence_margin_db,
            buffer_ms=buffer_ms,
            min_chunk_ms=min_chunk_ms,
            join_gap_ms=join_gap_ms,
            max_chunk_ms=max_chunk_ms,
        )

    root = Path(output_dir)
    chunks_dir = root / "chunks"
    if chunks_dir.exists():
        shutil.rmtree(chunks_dir)
    chunks_dir.mkdir(parents=True, exist_ok=True)

    audio = AudioSegment.from_wav(str(wav))
    audio_len = len(audio)
    if audio_len <= 0:
        raise ValueError(f"Empty WAV: {wav}")

    base_dbfs = audio.dBFS
    if base_dbfs == float("-inf"):
        nonsilent_ranges = [[0, audio_len]]
    else:
        silence_thresh = base_dbfs - silence_margin_db
        nonsilent_ranges = detect_nonsilent(
            audio,
            min_silence_len=min_silence_len,
            silence_thresh=silence_thresh,
        )
        if not nonsilent_ranges:
            nonsilent_ranges = [[0, audio_len]]

    buffered: list[list[int]] = []
    for start_ms, end_ms in nonsilent_ranges:
        start = max(0, int(start_ms) - buffer_ms)
        end = min(audio_len, int(end_ms) + buffer_ms)
        if end > start:
            buffered.append([start, end])

    ranges = _merge_ranges(buffered, join_gap_ms=join_gap_ms, min_chunk_ms=min_chunk_ms)
    ranges = _cap_long_ranges(ranges, max_chunk_ms=max_chunk_ms)

    chunk_info: list[dict[str, Any]] = []
    for idx, (start_ms, end_ms) in enumerate(ranges, start=1):
        if end_ms <= start_ms:
            continue
        chunk_path = chunks_dir / f"chunk_{idx:03d}.wav"
        audio[start_ms:end_ms].export(str(chunk_path), format="wav")
        chunk_info.append(
            {
                "filepath": str(chunk_path),
                "offset_sec": start_ms / 1000.0,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "duration_sec": (end_ms - start_ms) / 1000.0,
                "srt_path": "",
            }
        )

    if not chunk_info:
        chunk_path = chunks_dir / "chunk_001.wav"
        audio.export(str(chunk_path), format="wav")
        chunk_info.append(
            {
                "filepath": str(chunk_path),
                "offset_sec": 0.0,
                "start_ms": 0,
                "end_ms": audio_len,
                "duration_sec": audio_len / 1000.0,
                "srt_path": "",
            }
        )
    return chunk_info


def _split_audio_smart_wave_fallback(
    wav: Path,
    output_dir: str | Path,
    *,
    min_silence_len: int,
    silence_margin_db: float,
    buffer_ms: int,
    min_chunk_ms: int,
    join_gap_ms: int,
    max_chunk_ms: int,
) -> list[dict[str, Any]]:
    """Dependency-light fallback for PCM WAV when pydub cannot import."""
    import numpy as np

    root = Path(output_dir)
    chunks_dir = root / "chunks"
    if chunks_dir.exists():
        shutil.rmtree(chunks_dir)
    chunks_dir.mkdir(parents=True, exist_ok=True)

    with wave.open(str(wav), "rb") as wf:
        channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        sample_rate = wf.getframerate()
        frame_count = wf.getnframes()
        raw = wf.readframes(frame_count)

    if frame_count <= 0 or sample_rate <= 0:
        raise ValueError(f"Empty WAV: {wav}")
    if sample_width != 2:
        raise ValueError(
            "VAD fallback currently supports 16-bit PCM WAV. Install audioop-lts to enable pydub for other WAV formats."
        )

    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32)
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    audio_len_ms = int(round(len(samples) / sample_rate * 1000.0))

    full_scale = 32768.0
    full_rms = float(np.sqrt(np.mean(samples * samples))) if samples.size else 0.0
    if full_rms <= 1e-8:
        ranges = [[0, audio_len_ms]]
    else:
        base_dbfs = 20.0 * np.log10(max(full_rms / full_scale, 1e-12))
        thresh_dbfs = base_dbfs - silence_margin_db
        win_ms = 30
        win_n = max(1, int(sample_rate * win_ms / 1000.0))
        nonsilent_windows: list[tuple[int, int]] = []

        for start in range(0, len(samples), win_n):
            end = min(len(samples), start + win_n)
            chunk = samples[start:end]
            rms = float(np.sqrt(np.mean(chunk * chunk))) if chunk.size else 0.0
            dbfs = -120.0 if rms <= 1e-8 else 20.0 * np.log10(max(rms / full_scale, 1e-12))
            if dbfs >= thresh_dbfs:
                start_ms = int(round(start / sample_rate * 1000.0))
                end_ms = int(round(end / sample_rate * 1000.0))
                nonsilent_windows.append((start_ms, end_ms))

        ranges = []
        for start_ms, end_ms in nonsilent_windows:
            if not ranges or start_ms - ranges[-1][1] > min_silence_len:
                ranges.append([start_ms, end_ms])
            else:
                ranges[-1][1] = end_ms
        if not ranges:
            ranges = [[0, audio_len_ms]]

    buffered = [
        [max(0, start - buffer_ms), min(audio_len_ms, end + buffer_ms)]
        for start, end in ranges
        if end > start
    ]
    ranges = _merge_ranges(buffered, join_gap_ms=join_gap_ms, min_chunk_ms=min_chunk_ms)
    ranges = _cap_long_ranges(ranges, max_chunk_ms=max_chunk_ms)

    chunk_info: list[dict[str, Any]] = []
    for idx, (start_ms, end_ms) in enumerate(ranges, start=1):
        start_frame = max(0, int(round(start_ms * sample_rate / 1000.0)))
        end_frame = min(len(samples), int(round(end_ms * sample_rate / 1000.0)))
        chunk_path = chunks_dir / f"chunk_{idx:03d}.wav"
        mono_i16 = np.clip(samples[start_frame:end_frame], -32768, 32767).astype("<i2")
        with wave.open(str(chunk_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(mono_i16.tobytes())
        chunk_info.append(
            {
                "filepath": str(chunk_path),
                "offset_sec": start_ms / 1000.0,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "duration_sec": (end_ms - start_ms) / 1000.0,
                "srt_path": "",
            }
        )
    return chunk_info


def _shift_timestamp_line(line: str, offset_sec: float) -> str | None:
    match = _SRT_TS_RE.search(line)
    if not match:
        return None

    start = _ts_to_sec(*match.groups()[:4]) + offset_sec
    end = _ts_to_sec(*match.groups()[4:]) + offset_sec
    if end <= start:
        return None

    shifted = f"{_sec_to_srt_ts(start)} --> {_sec_to_srt_ts(end)}"
    return line[: match.start()] + shifted + line[match.end() :]


def stitch_srt_chunks(chunk_info_list: list[dict[str, Any]], final_srt_path: str | Path) -> str:
    """
    Merge chunk SRT files back into one SRT on the original audio timeline.

    Each chunk_info item must include "offset_sec" and either "srt_path" or a
    "filepath" whose suffix can be replaced with .srt.
    """
    blocks_out: list[str] = []

    for info in chunk_info_list:
        offset_sec = float(info.get("offset_sec", 0.0) or 0.0)
        srt_path = Path(info.get("srt_path") or Path(info["filepath"]).with_suffix(".srt"))
        if not srt_path.is_file():
            raise FileNotFoundError(f"Chunk SRT not found: {srt_path}")

        text = srt_path.read_text(encoding="utf-8", errors="replace")
        raw_blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n").strip())
        for raw_block in raw_blocks:
            lines = [line.rstrip() for line in raw_block.splitlines() if line.strip()]
            if not lines:
                continue

            ts_idx = next((i for i, line in enumerate(lines) if "-->" in line), -1)
            if ts_idx < 0:
                continue

            shifted_ts = _shift_timestamp_line(lines[ts_idx], offset_sec)
            if not shifted_ts:
                continue

            content = "\n".join(lines[ts_idx + 1:]).strip()
            if not content:
                continue
            blocks_out.append(f"{len(blocks_out) + 1}\n{shifted_ts}\n{content}")

    final_path = Path(final_srt_path)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    final_path.write_text("\n\n".join(blocks_out) + ("\n" if blocks_out else ""), encoding="utf-8")
    return str(final_path)
