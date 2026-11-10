"""音效层：用代码合成 6 个音，按时间轴事件摆到配音轨旁边。

为什么是「合成」而不是「素材包」：
  1. 可复现 —— 同一份 spec 必须出同一份声音，素材包换版本音色就变了；
  2. 版权干净 —— 自己合成的波形没有来源问题，和 BGM 那条「只用自有/CC0」一致；
  3. 体积小 —— 6 个音共几百 KB，缓存一次之后零成本。

只依赖标准库（wave/struct/math/random），不破坏 videogen「零第三方依赖」的约定。
想换音色：把同名 wav 放进 `data/sfx/`（如 `data/sfx/hit.wav`），优先级高于合成结果。
"""

from __future__ import annotations

import hashlib
import math
import random
import struct
import wave
from pathlib import Path

from .paths import ROOT, WORK_DIR

SR = 48000
PALETTE = ("hit", "sub", "whoosh", "pop", "tick", "riser")
OVERRIDE_DIR = Path(ROOT) / "data" / "sfx"  # 放同名 wav 即可覆盖合成结果
CACHE_DIR = Path(WORK_DIR) / "sfx"
VERSION = "v1"


# ---------------------------------------------------------------- 基础工具

def _write_wav(path: Path, samples: list[float], sr: int = SR) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    peak = max(1e-9, max(abs(s) for s in samples))
    scale = 0.90 / peak
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        frames = bytearray()
        for s in samples:
            v = int(max(-1.0, min(1.0, s * scale)) * 32767)
            frames += struct.pack("<h", v)
        w.writeframes(bytes(frames))
    return path


def _times(dur: float, sr: int = SR) -> list[float]:
    return [i / sr for i in range(int(dur * sr))]


def _lowpass(x: list[float], cutoff: float, sr: int = SR) -> list[float]:
    k = 1.0 - math.exp(-2.0 * math.pi * cutoff / sr)
    y, out = 0.0, []
    for v in x:
        y += (v - y) * k
        out.append(y)
    return out


def _highpass(x: list[float], cutoff: float, sr: int = SR) -> list[float]:
    lp = _lowpass(x, cutoff, sr)
    return [a - b for a, b in zip(x, lp)]


def _soft_clip(x: list[float], drive: float = 1.6) -> list[float]:
    norm = math.tanh(drive)
    return [math.tanh(drive * v) / norm for v in x]


# ---------------------------------------------------------------- 六个音色

def _hit() -> list[float]:
    """大字砸入：低频下潜 + 5ms 噪声瞬态。"""
    dur = 0.28
    ts = _times(dur)
    rnd = random.Random(20261108)
    out = []
    for t in ts:
        f = 55.0 + 40.0 * math.exp(-t * 10.0)
        phase = 2 * math.pi * (55.0 * t + 4.0 * (1.0 - math.exp(-t * 10.0)))
        amp = math.exp(-t / 0.055) * (1.0 - math.exp(-t / 0.0015))
        body = math.sin(phase) * amp
        burst = (rnd.random() * 2 - 1) * math.exp(-t / 0.006) * 0.35
        out.append(body + burst)
    out = _lowpass(out, 12000)
    return _soft_clip(out, 1.9)


def _sub() -> list[float]:
    """低频落点：40Hz 缓慢下潜。"""
    dur = 0.70
    out = []
    for t in _times(dur):
        f0, f1 = 58.0, 34.0
        k = t / dur
        f = f0 + (f1 - f0) * k
        phase = 2 * math.pi * (f0 * t + (f1 - f0) * t * k / 2.0)
        amp = math.exp(-t / 0.30) * (1.0 - math.exp(-t / 0.012))
        out.append(math.sin(phase) * amp)
    return _soft_clip(out, 1.3)


def _whoosh() -> list[float]:
    """转场：白噪过时间变化的带通，400Hz → 4kHz。"""
    dur = 0.45
    ts = _times(dur)
    rnd = random.Random(4417)
    noise = [rnd.random() * 2 - 1 for _ in ts]
    out = []
    y_lo = y_hi = 0.0
    f_lo, f_hi = 350.0, 4000.0
    for i, t in enumerate(ts):
        u = t / dur
        fc = f_lo * (f_hi / f_lo) ** u
        # 两段一阶低通相减，近似一个随中心频率移动的带通
        k = 1.0 - math.exp(-2.0 * math.pi * fc / SR)
        y_lo += (noise[i] - y_lo) * k
        k2 = 1.0 - math.exp(-2.0 * math.pi * min(fc * 2.6, 16000.0) / SR)
        y_hi += (noise[i] - y_hi) * k2
        band = y_hi - y_lo
        env = math.sin(math.pi * u) ** 1.6
        out.append(band * env)
    return _soft_clip(out, 1.2)


def _pop() -> list[float]:
    """标签/贴纸弹出：短促上行正弦。"""
    dur = 0.12
    out = []
    for t in _times(dur):
        f0, f1 = 700.0, 1500.0
        k = t / dur
        f = f0 + (f1 - f0) * min(1.0, k * 2.2)
        phase = 2 * math.pi * (f0 * t + (f - f0) * t / 2.0)
        amp = math.exp(-t / 0.035) * (1.0 - math.exp(-t / 0.002))
        out.append(math.sin(phase) * amp)
    return _soft_clip(out, 1.2)


def _tick() -> list[float]:
    """打字/逐条出现：高通噪声脉冲。"""
    dur = 0.06
    ts = _times(dur)
    rnd = random.Random(9911)
    noise = [rnd.random() * 2 - 1 for _ in ts]
    hp = _highpass(noise, 2200.0)
    return _soft_clip([v * math.exp(-t / 0.008) for v, t in zip(hp, ts)], 1.2)


def _riser() -> list[float]:
    """收尾上升：180Hz → 1.2kHz 正弦扫 + 渐强噪声。"""
    dur = 1.20
    ts = _times(dur)
    rnd = random.Random(777)
    out, phase = [], 0.0
    for i, t in enumerate(ts):
        u = t / dur
        f = 180.0 * (6.5 ** u)
        phase += 2 * math.pi * f / SR
        tone = math.sin(phase) * (u ** 1.4)
        noise = (rnd.random() * 2 - 1) * 0.25 * (u ** 2.0)
        out.append(tone + noise)
    # 末尾 20ms 硬收，避免爆音
    tail = int(0.02 * SR)
    for i in range(max(0, len(out) - tail), len(out)):
        out[i] *= (len(out) - i) / tail
    return _soft_clip(out, 1.3)


_BUILDERS = {
    "hit": _hit,
    "sub": _sub,
    "whoosh": _whoosh,
    "pop": _pop,
    "tick": _tick,
    "riser": _riser,
}


# ---------------------------------------------------------------- 取音

def path_for(name: str) -> Path:
    """返回该音效的 wav 路径：优先 `data/sfx/<name>.wav`，否则用合成缓存。"""
    if name not in _BUILDERS:
        raise KeyError(f"没有这个音效：{name}（可选 {', '.join(PALETTE)}）")
    override = Path(OVERRIDE_DIR) / f"{name}.wav"
    if override.exists():
        return override
    cache = Path(CACHE_DIR) / f"{name}-{VERSION}.wav"
    if cache.exists() and cache.stat().st_size > 1000:
        return cache
    _write_wav(cache, _BUILDERS[name]())
    return cache


def ensure_all() -> dict[str, Path]:
    """预热全部音效，doctor/首次出片时调用。"""
    return {n: path_for(n) for n in PALETTE}


def describe() -> list[tuple[str, str, float]]:
    rows = []
    for n in PALETTE:
        p = path_for(n)
        dur = 0.0
        try:
            with wave.open(str(p), "rb") as w:
                dur = w.getnframes() / float(w.getframerate())
        except Exception:
            pass
        src = "覆盖" if p.parent == Path(OVERRIDE_DIR) else "合成"
        rows.append((n, src, dur))
    return rows


# ---------------------------------------------------------------- 事件推导

def events_for(spec) -> list[dict]:
    """按样式从时间轴推音效事件：[{name, at, gain_db}]。

    时间点全部来自 resolve() 算好的 line.at / spec.duration，所以手写脚本不用标秒数。
    """
    if not getattr(spec, "sfx_enabled", True):
        return []
    explicit = getattr(spec, "sfx_events", None)
    if explicit:
        rows = []
        for e in explicit:
            name = e.get("name", "")
            if name in _BUILDERS:
                rows.append({"name": name, "at": float(e.get("at", 0.0)),
                             "gain_db": float(e.get("gain_db", 0.0))})
        return sorted(rows, key=lambda r: r["at"])

    style = getattr(spec, "style", "a3")
    lines = [ln for ln in spec.lines if getattr(ln, "at", None) is not None]
    if not lines:
        return []
    out: list[dict] = []

    def add(name: str, at: float, gain_db: float) -> None:
        if 0.0 <= at < max(0.1, spec.duration):
            out.append({"name": name, "at": round(at, 3), "gain_db": gain_db})

    # 单条覆盖：line.sfx = "hit" / "hit:0.35" / "none"
    if style == "a3":
        for i, ln in enumerate(lines):
            first, last = i == 0, i == len(lines) - 1
            gain = -4.0 if first else (-1.5 if last else -8.0)
            override = (getattr(ln, "sfx", "") or "").strip()
            if override.lower() == "none":
                pass
            elif override:
                name, _, delta = override.partition(":")
                if name in _BUILDERS:
                    add(name, ln.at + (float(delta) if delta else 0.0), gain)
            else:
                add("hit", ln.at, gain)
            if last:
                add("sub", ln.at + 0.02, -3.0)
    elif style == "title":
        add("pop", 0.30, -3.0)
        for ln in lines:
            add("tick", ln.at, -8.0)
    elif style == "quote":
        for ln in lines:
            add("whoosh", max(0.0, ln.at - 0.10), -18.0)
        add("riser", max(0.0, lines[-1].at - 0.6), -12.0)
    elif style == "explain":
        for ln in lines:
            add("whoosh", max(0.0, ln.at - 0.12), -16.0)
            if getattr(ln, "sticker", ""):
                add("pop", ln.at + 0.25, -12.0)
        if spec.duration > 8:
            add("riser", max(0.0, spec.duration - 1.35), -10.0)
    return sorted(out, key=lambda r: r["at"])
