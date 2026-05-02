# -*- coding: utf-8 -*-
"""
Read-only timestamp quality checks for generated transcript assets.

This module intentionally does not import or modify the core ASR pipeline. It
looks at files that already exist on disk and reports whether timestamps look
like true acoustic alignment or a uniform fallback estimate.
"""

from __future__ import annotations

import json
import re
import statistics
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


_SRT_TS_RE = re.compile(
    r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*"
    r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})"
)


@dataclass
class TimestampQualityReport:
    wav_path: str
    srt_path: str
    json_path: str
    wav_duration: float | None = None
    srt_count: int = 0
    srt_last_end: float | None = None
    srt_backwards: int = 0
    json_token_count: int = 0
    json_last_timestamp: float | None = None
    json_backwards: int = 0
    gap_mean: float | None = None
    gap_min: float | None = None
    gap_max: float | None = None
    gap_cv: float | None = None
    gap_p05: float | None = None
    gap_p95: float | None = None
    likely_uniform_fallback: bool = False
    confidence: str = "unknown"
    notes: list[str] | None = None


def _ts_to_sec(parts: Sequence[str]) -> float:
    h, m, s, ms = parts
    return int(h) * 3600 + int(m) * 60 + int(s) + int(str(ms).ljust(3, "0")) / 1000.0


def _read_wav_duration(path: Path) -> float | None:
    try:
        with wave.open(str(path), "rb") as wf:
            rate = wf.getframerate()
            if rate <= 0:
                return None
            return wf.getnframes() / rate
    except Exception:
        return None


def _read_srt_bounds(path: Path) -> tuple[int, float | None, int]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return 0, None, 0

    count = 0
    last_end: float | None = None
    backwards = 0
    prev_start = -1.0
    for match in _SRT_TS_RE.finditer(text):
        start = _ts_to_sec(match.groups()[:4])
        end = _ts_to_sec(match.groups()[4:])
        count += 1
        if start < prev_start:
            backwards += 1
        prev_start = start
        last_end = end
    return count, last_end, backwards


def _read_json_timestamps(path: Path) -> list[float]:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []

    raw = data.get("timestamps", [])
    timestamps: list[float] = []
    for value in raw:
        try:
            timestamps.append(float(value))
        except (TypeError, ValueError):
            continue
    return timestamps


def _percentile(sorted_values: Sequence[float], pct: float) -> float | None:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = (len(sorted_values) - 1) * pct
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = pos - lo
    return sorted_values[lo] * (1.0 - frac) + sorted_values[hi] * frac


def _positive_gaps(timestamps: Sequence[float]) -> list[float]:
    return [b - a for a, b in zip(timestamps, timestamps[1:]) if b >= a]


def _is_uniform_fallback(gaps: Sequence[float]) -> tuple[bool, str]:
    if len(gaps) < 30:
        return False, "low"

    mean_gap = statistics.mean(gaps)
    if mean_gap <= 0:
        return False, "low"

    stdev = statistics.pstdev(gaps)
    cv = stdev / mean_gap
    sorted_gaps = sorted(gaps)
    p05 = _percentile(sorted_gaps, 0.05) or 0.0
    p95 = _percentile(sorted_gaps, 0.95) or 0.0
    spread = p95 - p05

    if cv <= 0.015 and spread <= max(0.03, mean_gap * 0.08):
        return True, "high"
    if cv <= 0.04 and spread <= max(0.08, mean_gap * 0.18):
        return True, "medium"
    return False, "low"


def analyze_timestamp_quality(
    wav_path: str | Path,
    srt_path: str | Path,
    json_path: str | Path | None = None,
) -> TimestampQualityReport:
    wav = Path(wav_path)
    srt = Path(srt_path)
    js = Path(json_path) if json_path else srt.with_suffix(".json")
    notes: list[str] = []

    wav_duration = _read_wav_duration(wav)
    srt_count, srt_last_end, srt_backwards = _read_srt_bounds(srt)
    timestamps = _read_json_timestamps(js)
    gaps = _positive_gaps(timestamps)
    backwards = sum(1 for a, b in zip(timestamps, timestamps[1:]) if b < a)

    gap_mean = gap_min = gap_max = gap_cv = gap_p05 = gap_p95 = None
    likely_uniform = False
    confidence = "unknown"

    if timestamps:
        if len(gaps) >= 2:
            gap_mean = statistics.mean(gaps)
            gap_min = min(gaps)
            gap_max = max(gaps)
            gap_cv = statistics.pstdev(gaps) / gap_mean if gap_mean > 0 else None
            sorted_gaps = sorted(gaps)
            gap_p05 = _percentile(sorted_gaps, 0.05)
            gap_p95 = _percentile(sorted_gaps, 0.95)
            likely_uniform, confidence = _is_uniform_fallback(gaps)
        else:
            confidence = "low"
    else:
        notes.append("No JSON timestamps were found.")
        confidence = "low"

    if srt_backwards:
        notes.append(f"SRT start times move backwards {srt_backwards} time(s).")
    if backwards:
        notes.append(f"JSON timestamps move backwards {backwards} time(s).")
    if wav_duration and srt_last_end is not None:
        diff = srt_last_end - wav_duration
        if abs(diff) > 2.0:
            notes.append(f"SRT end differs from WAV duration by {diff:.2f}s.")
    if wav_duration and timestamps:
        diff = timestamps[-1] - wav_duration
        if abs(diff) > 2.0:
            notes.append(f"JSON last timestamp differs from WAV duration by {diff:.2f}s.")

    return TimestampQualityReport(
        wav_path=str(wav),
        srt_path=str(srt),
        json_path=str(js),
        wav_duration=wav_duration,
        srt_count=srt_count,
        srt_last_end=srt_last_end,
        srt_backwards=srt_backwards,
        json_token_count=len(timestamps),
        json_last_timestamp=timestamps[-1] if timestamps else None,
        json_backwards=backwards,
        gap_mean=gap_mean,
        gap_min=gap_min,
        gap_max=gap_max,
        gap_cv=gap_cv,
        gap_p05=gap_p05,
        gap_p95=gap_p95,
        likely_uniform_fallback=likely_uniform,
        confidence=confidence,
        notes=notes,
    )


def format_quality_report(report: TimestampQualityReport) -> list[str]:
    lines = [
        "[TimeDiag] Timestamp quality check:",
        f"[TimeDiag] WAV={_fmt_opt(report.wav_duration)}s, "
        f"SRT last={_fmt_opt(report.srt_last_end)}s/{report.srt_count} lines, "
        f"JSON last={_fmt_opt(report.json_last_timestamp)}s/{report.json_token_count} tokens",
    ]

    if report.gap_mean is not None:
        lines.append(
            f"[TimeDiag] Token gap mean={report.gap_mean:.3f}s, "
            f"p05={_fmt_opt(report.gap_p05)}s, p95={_fmt_opt(report.gap_p95)}s, "
            f"cv={_fmt_opt(report.gap_cv)}"
        )

    if report.likely_uniform_fallback:
        lines.append(
            "[TimeDiag] WARNING: timestamps look uniformly estimated, not acoustic-aligned "
            f"(confidence={report.confidence}). Long-audio subtitle highlight/seek may drift locally."
        )
    elif report.json_token_count:
        lines.append(
            "[TimeDiag] OK: timestamps do not look like a simple uniform fallback "
            f"(confidence={report.confidence})."
        )
    else:
        lines.append("[TimeDiag] WARNING: no usable JSON timestamps; SRT precision cannot be verified.")

    for note in report.notes or []:
        lines.append(f"[TimeDiag] Note: {note}")
    return lines


def _fmt_opt(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.3f}"


def main(paths: Iterable[str]) -> int:
    for raw in paths:
        p = Path(raw)
        if p.suffix.lower() == ".wav":
            wav = p
            srt = p.with_suffix(".srt")
        else:
            srt = p
            wav = p.with_suffix(".wav")
        report = analyze_timestamp_quality(wav, srt)
        for line in format_quality_report(report):
            print(line)
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
