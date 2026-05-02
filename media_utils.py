# -*- coding: utf-8 -*-
"""
media_utils.py  v3  —  sounddevice + soundfile 音频引擎

改动要点（相较 pygame 版本）
─────────────────────────────────────────────────────────────────
· 使用 sounddevice.OutputStream + 回调函数驱动音频，完全非阻塞。
· soundfile 读取 WAV 数据到 numpy 数组，支持精准帧级 seek。
· 内部维护 _current_frame 帧计数器，get_current_time() 实时返回绝对秒数。
· pause/resume 通过 _paused 标志实现（不停止流，无爆音）。
· seek() 仅更新 _current_frame（流继续运行，下个回调立即从新位置播放）。
· play() 每次重建 OutputStream（确保从新位置干净启动）。

依赖安装
─────────────────────────────────────────────────────────────────
pip install sounddevice soundfile numpy
"""

import re
import threading
from pathlib import Path
from typing import Callable, Optional

# ── sounddevice / soundfile / numpy 懒加载 ───────────────────────────────────
try:
    import numpy as np
    import sounddevice as sd
    import soundfile as sf
    _SD_OK    = True
    _SD_ERROR = ""
except ImportError as _e:
    _SD_OK    = False
    _SD_ERROR = str(_e)


# ═══════════════════════════════════════════════════════════════════════════════
# SRT 字幕解析器（与 v2 相同，保持稳定）
# ═══════════════════════════════════════════════════════════════════════════════
_TS_RE   = re.compile(r'(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})')
_HTML_RE = re.compile(r'<[^>]+>')


def _ts_to_sec(ts: str) -> float:
    m = _TS_RE.search(ts)
    if not m:
        return 0.0
    h, mn, s, ms_raw = m.groups()
    return int(h) * 3600 + int(mn) * 60 + int(s) + int(ms_raw.ljust(3, "0")) / 1000.0


def _sec_to_str(sec: float) -> str:
    sec = max(0.0, sec)
    h   = int(sec // 3600)
    mn  = int((sec % 3600) // 60)
    s   = int(sec % 60)
    return f"{h:02d}:{mn:02d}:{s:02d}"


def parse_srt(path: str) -> list[dict]:
    """
    健壮解析 SRT 字幕文件。
    编码策略：utf-8-sig → utf-8 → gbk → latin-1（保底）。
    返回 [{"index", "start_sec", "end_sec", "time_str", "text"}, ...]
    """
    content = ""
    for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            content = Path(path).read_text(encoding=enc)
            break
        except (UnicodeDecodeError, LookupError, OSError):
            continue

    if not content.strip():
        return []

    content = content.replace("\r\n", "\n").replace("\r", "\n")
    blocks  = re.split(r'\n{2,}', content.strip())
    results: list[dict] = []

    for block in blocks:
        lines   = [l.rstrip() for l in block.splitlines() if l.strip()]
        ts_idx  = next((i for i, l in enumerate(lines) if "-->" in l), -1)
        if ts_idx < 0:
            continue
        parts = lines[ts_idx].split("-->")
        if len(parts) < 2:
            continue
        start_sec = _ts_to_sec(parts[0])
        end_sec   = _ts_to_sec(parts[1])
        raw_text  = " ".join(lines[ts_idx + 1:]).strip()
        text      = _HTML_RE.sub("", raw_text).strip()
        if not text:
            continue
        results.append({
            "index":     len(results) + 1,
            "start_sec": start_sec,
            "end_sec":   end_sec,
            "time_str":  _sec_to_str(start_sec),
            "text":      text,
        })

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# 音频引擎 v3  —  sounddevice OutputStream + numpy 帧计数器
# ═══════════════════════════════════════════════════════════════════════════════
class AudioEngine:
    """
    基于 sounddevice.OutputStream 的非阻塞音频播放引擎。

    核心设计
    ─────────────────────────────────────────────────────────────────
    · soundfile.read() 将 WAV 全量读入 numpy 数组（float32 / always_2d）。
      对于 1 小时 16kHz mono：约 230 MB，可接受。
    · OutputStream 回调 _audio_callback 运行于 PortAudio 音频线程：
        - 每次调用从 _data[_current_frame:] 取出若干帧填入 outdata。
        - 原子性地更新 _current_frame（Python GIL 保护 int 运算）。
        - _paused=True 时输出静音但不推进 _current_frame（保留位置）。
    · seek()  仅改写 _current_frame，下一次回调即从新位置播放。
    · play()  重建 OutputStream，确保从新位置干净启动。
    · pause() 置 _paused=True；resume() 清除 _paused，流不重启无爆音。

    线程安全
    ─────────────────────────────────────────────────────────────────
    _current_frame 由回调线程写、主线程读（get_current_time）。
    Python GIL 保证 int 的读/写是原子操作，无需额外锁。
    _stream 的创建/销毁由 _stream_lock 保护，避免主线程并发操作。
    """

    def __init__(self, log_fn: Optional[Callable[[str], None]] = None):
        self._log = log_fn or (lambda msg: print(f"[AudioEngine] {msg}"))

        # ── 音频数据 ──────────────────────────────────────────────────────────
        self._data:           Optional["np.ndarray"] = None  # float32, shape=(N, C)
        self._samplerate:     int   = 0
        self._channels:       int   = 1
        self._total_frames:   int   = 0
        self._total_duration: float = 0.0
        self._wav_path:       str   = ""

        # ── 播放状态 ──────────────────────────────────────────────────────────
        self._current_frame:  int  = 0       # 当前播放帧（由回调线程写，主线程只读）
        self._paused:         bool = False    # 暂停标志
        self._stream:         Optional[object] = None  # sd.OutputStream
        self._stream_lock = threading.Lock()  # 保护 _stream 的创建/销毁

        if not _SD_OK:
            self._log(f"[Error] sounddevice/soundfile 未安装，音频功能不可用。")
            self._log(f"[Error] 请执行：pip install sounddevice soundfile")

    # ── 加载 ──────────────────────────────────────────────────────────────────
    def load(self, wav_path: str) -> float:
        """
        加载 WAV 文件，返回时长（秒）。
        使用 soundfile.read(always_2d=True) 确保数据形状始终为 (N, C)。
        """
        self.stop()   # 停止旧的播放
        self._data    = None
        self._wav_path = ""
        self._current_frame = 0
        self._total_duration = 0.0

        if not _SD_OK:
            return 0.0

        p = Path(wav_path)
        if not p.is_file():
            self._log(f"[Error] 音频文件不存在：{wav_path}")
            return 0.0

        try:
            # always_2d=True：mono 文件也返回 (N,1) 而非 (N,)，统一处理
            data, sr = sf.read(str(p), dtype="float32", always_2d=True)
            self._data          = data
            self._samplerate    = sr
            self._channels      = data.shape[1]
            self._total_frames  = data.shape[0]
            self._total_duration = self._total_frames / sr
            self._wav_path      = str(p)

            size_mb = p.stat().st_size / 1024 / 1024
            self._log(
                f"[Diag] 已加载：{p.name}  {sr}Hz/{self._channels}ch  "
                f"{self._total_duration:.1f}s  {size_mb:.1f}MB"
            )
            return self._total_duration

        except Exception as exc:
            self._log(f"[Error] soundfile 加载失败：{exc}")
            return 0.0

    # ── 回调函数（在 PortAudio 音频线程调用） ────────────────────────────────
    def _audio_callback(self, outdata, frames, time_info, status) -> None:
        """
        sounddevice OutputStream 的核心回调。
        每次被调用时，向 outdata 填入 `frames` 帧的 PCM 数据。

        关键约束
        ────────────────────────────────────────────────────────────────
        · 绝对不能阻塞（不能有 sleep / lock.acquire(blocking=True) 等）。
        · _paused=True 时输出静音并提前返回，不推进帧计数器。
        · 播放至末尾时输出静音并抛出 sd.CallbackStop，触发流结束。
        """
        if self._paused or self._data is None:
            outdata.fill(0)
            return

        remaining = self._total_frames - self._current_frame
        if remaining <= 0:
            # 播放到末尾
            outdata.fill(0)
            raise sd.CallbackStop()

        to_copy = min(frames, remaining)
        # 从当前帧拷贝 PCM 数据（GIL 保护 slice 操作，无需显式锁）
        outdata[:to_copy] = self._data[self._current_frame: self._current_frame + to_copy]

        if to_copy < frames:
            # 最后一批不足 frames，补零后结束
            outdata[to_copy:].fill(0)

        # 推进帧计数器（Python int += int 是 GIL 下的原子操作）
        self._current_frame += to_copy

        if to_copy < frames:
            raise sd.CallbackStop()

    # ── 播放 ──────────────────────────────────────────────────────────────────
    def play(self, start_sec: float = 0.0) -> None:
        """
        从 start_sec 处开始播放（总是重建 OutputStream）。
        与 seek() 的区别：play() 保证流在运行，seek() 仅改变位置。
        """
        if self._data is None or not _SD_OK:
            return

        self._paused       = False
        self._current_frame = int(
            max(0.0, min(start_sec * self._samplerate, self._total_frames - 1))
        )

        with self._stream_lock:
            # 停止并关闭已有流
            if self._stream is not None:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None

            # 建立新流
            try:
                self._stream = sd.OutputStream(
                    samplerate=self._samplerate,
                    channels=self._channels,
                    dtype="float32",
                    callback=self._audio_callback,
                )
                self._stream.start()
                self._log(
                    f"[Diag] play(start={start_sec:.2f}s)  "
                    f"frame={self._current_frame}  sr={self._samplerate}"
                )
            except Exception as exc:
                self._log(f"[Error] 音频流启动失败：{exc}")
                self._stream = None

    # ── 跳转（保持播放/暂停状态） ─────────────────────────────────────────────
    def seek(self, start_sec: float) -> None:
        """
        跳转到 start_sec 处，不改变播放/暂停状态。

        与进度条拖动一致：播放中只更新读指针 _current_frame，不重切开 OutputStream，
        回调下一包即从目标帧输出，听感最顺滑。

        仅在「非暂停且流已结束或未激活」时重建流（与早期实现一致）。
        """
        if self._data is None:
            return

        self._current_frame = int(
            max(0.0, min(start_sec * self._samplerate, self._total_frames - 1))
        )

        if not self._paused:
            stream_active = (
                self._stream is not None
                and self._stream.active
            )
            if not stream_active:
                with self._stream_lock:
                    if self._stream is not None:
                        try:
                            self._stream.close()
                        except Exception:
                            pass
                        self._stream = None
                    try:
                        self._stream = sd.OutputStream(
                            samplerate=self._samplerate,
                            channels=self._channels,
                            dtype="float32",
                            callback=self._audio_callback,
                        )
                        self._stream.start()
                    except Exception as exc:
                        self._log(f"[Error] seek 重启流失败：{exc}")
                        self._stream = None

    # ── 暂停 / 恢复 ───────────────────────────────────────────────────────────
    def pause(self) -> None:
        """
        暂停播放：将 _paused 置为 True。
        回调下次被调用时输出静音，不推进帧计数器，位置冻结。
        流保持运行（避免重启带来的延迟和爆音）。
        """
        self._paused = True

    def resume(self) -> None:
        """
        恢复播放：清除 _paused 标志。
        若流已结束（播放到末尾）则重启流。
        """
        self._paused = False
        stream_active = self._stream is not None and self._stream.active
        if not stream_active and self._data is not None and _SD_OK:
            self.seek(self._current_frame / max(1, self._samplerate))

    # ── 停止 ──────────────────────────────────────────────────────────────────
    def stop(self) -> None:
        """停止播放，重置帧计数器。"""
        with self._stream_lock:
            if self._stream is not None:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None
        self._paused       = False
        self._current_frame = 0

    # ── 位置查询 ──────────────────────────────────────────────────────────────
    def get_current_time(self) -> float:
        """
        返回当前播放的绝对时间（秒）。
        由主线程调用，读取 _current_frame（GIL 保护）。
        """
        if self._samplerate == 0:
            return 0.0
        return min(self._current_frame / self._samplerate, self._total_duration)

    @property
    def samplerate(self) -> int:
        return int(max(0, self._samplerate))

    def get_scope_mono_tail(self, n: int) -> Optional["np.ndarray"]:
        """
        主线程只读：取以当前播放头为结尾的近 n 个采样，混为单声道 float32，
        按片段峰值归一到约 ±0.88，便于 UI 波形绘制。不改变回调与播放状态。
        """
        if self._data is None or not _SD_OK or n < 2:
            return None
        tf = max(1, int(self._total_frames))
        n_eff = min(max(2, int(n)), tf)
        cf = int(min(max(0, self._current_frame), tf - 1))
        start = max(0, cf + 1 - n_eff)
        chunk = self._data[start : cf + 1, :]
        if self._channels <= 1:
            mono = np.asarray(chunk[:, 0], dtype=np.float32)
        else:
            mono = np.mean(chunk.astype(np.float32, copy=False), axis=1)
        got = int(mono.shape[0])
        if got < n_eff:
            mono = np.pad(mono, (n_eff - got, 0), mode="constant")
        elif got > n_eff:
            mono = mono[-n_eff:]
        peak = float(np.max(np.abs(mono)))
        if peak > 1e-9:
            mono = (mono / peak * 0.88).astype(np.float32, copy=False)
        return mono

    # ── 状态查询 ──────────────────────────────────────────────────────────────
    @property
    def is_loaded(self) -> bool:
        return self._data is not None

    def is_playing(self) -> bool:
        """当前是否正在输出音频（非静音的流式状态）。"""
        if self._data is None or not _SD_OK:
            return False
        return (
            self._stream is not None
            and self._stream.active
            and not self._paused
        )

    def is_paused(self) -> bool:
        return self._paused

    @property
    def total_duration(self) -> float:
        return self._total_duration

    def extract_waveform(self, points: int = 150, mode: str = "peak") -> list[float]:
        """
        计算音频波形摘要并归一化到 0.0~1.0。

        参数
        ────────────────────────────────────────────────────────────────
        points: 采样点数量（默认 150）
        mode:
          - "peak"：每段取最大振幅
          - "rms" ：每段取均方根振幅
        """
        if self._data is None or points <= 0:
            return []

        # 转为单声道振幅序列（多声道取绝对值平均）
        if self._channels <= 1:
            amp = np.abs(self._data[:, 0])
        else:
            amp = np.mean(np.abs(self._data), axis=1)

        total = int(amp.shape[0])
        if total <= 0:
            return [0.0] * points

        # 将整段音频切为 points 份
        edges = np.linspace(0, total, num=points + 1, dtype=np.int64)
        values: list[float] = []
        use_rms = str(mode).lower() == "rms"

        for i in range(points):
            s = int(edges[i])
            e = int(edges[i + 1])
            if e <= s:
                values.append(0.0)
                continue
            chunk = amp[s:e]
            if chunk.size == 0:
                values.append(0.0)
                continue
            if use_rms:
                v = float(np.sqrt(np.mean(chunk * chunk)))
            else:
                v = float(np.max(chunk))
            values.append(max(0.0, v))

        vmax = max(values) if values else 0.0
        if vmax <= 1e-12:
            return [0.0] * points
        return [min(1.0, v / vmax) for v in values]

    def get_status(self) -> dict:
        """返回完整运行状态快照（供诊断报告使用）。"""
        return {
            "sd_available":   _SD_OK,
            "loaded":         self.is_loaded,
            "wav_path":       self._wav_path,
            "total_duration": self._total_duration,
            "current_time":   self.get_current_time(),
            "samplerate":     self._samplerate,
            "channels":       self._channels,
            "is_playing":     self.is_playing(),
            "is_paused":      self._paused,
            "current_frame":  self._current_frame,
        }

    # ── 资源释放 ──────────────────────────────────────────────────────────────
    def teardown(self) -> None:
        """释放 PortAudio 流资源（窗口关闭时调用）。"""
        self.stop()
