"""一条片子的完整流水线：resolve → 渲染画面 → 配音/BGM → 混流 → 校验。

    from videogen import spec, pipeline
    s = spec.Spec(style="a3", lines=[spec.Line("你"), spec.Line("懂什么")])
    r = pipeline.build(s)     # {'out': Path(...), 'duration': 2.5, ...}

约定：所有阶段都写进 progress(stage, pct)，pct 是 0~1 的整条片进度，
这样插件把进度直接扔回 QQ 也不突兀。
"""

from __future__ import annotations

import shutil
import time
import uuid
from pathlib import Path

from . import audio, render, sfx
from .paths import OUT_DIR, WORK_DIR, ensure_dirs
from .spec import Spec
from .styles import resolve


def build(
    spec: Spec,
    out_path: str | Path | None = None,
    workdir: str | Path | None = None,
    progress=None,
    keep_work: bool = False,
    timeout: int = 900,
) -> dict:
    t_all = time.time()
    warnings: list[str] = []

    def step(stage: str, pct: float) -> None:
        if progress:
            try:
                progress(stage, max(0.0, min(1.0, pct)))
            except Exception:  # noqa: BLE001
                pass

    ensure_dirs()
    step("准备", 0.01)
    resolve(spec)
    spec.validate()

    stamp = time.strftime("%Y%m%d-%H%M%S")
    slug = f"{spec.style}-{stamp}-{uuid.uuid4().hex[:4]}"
    out = Path(out_path) if out_path else (OUT_DIR / stamp[:8] / f"{slug}.mp4")
    out.parent.mkdir(parents=True, exist_ok=True)
    work = Path(workdir) if workdir else (WORK_DIR / slug)
    work.mkdir(parents=True, exist_ok=True)

    # ---------- 1. 画面 ----------
    t0 = time.time()
    silent = work / "silent.mp4"

    def frame_progress(done: int, total: int) -> None:
        step("渲染画面", 0.05 + 0.75 * (done / max(1, total)))

    step("渲染画面", 0.05)
    render.render(spec, silent, work, progress=frame_progress, timeout=timeout)
    render_sec = time.time() - t0

    # ---------- 2. 声音 ----------
    tts_sec = mix_sec = 0.0
    final = out
    clips: list[dict] = []
    t1 = time.time()
    if spec.voice_enabled and spec.lines:
        step("合成配音", 0.82)
        # 自训练音色要先把 GPT-SoVITS 拉起来；起不来就退回 edge 并记 warning。
        voice, voice_warns = audio.prepare_voice(spec.voice)
        warnings.extend(voice_warns)
        for i, line in enumerate(spec.lines):
            at = float(line.at or 0.0)
            nxt = float(spec.lines[i + 1].at) if i + 1 < len(spec.lines) and spec.lines[i + 1].at else spec.duration
            slot = max(0.12, nxt - at)
            mp3 = work / f"c{i:02d}.mp3"
            wav = work / f"c{i:02d}.wav"
            text = line.say or line.text
            try:
                audio.tts_line(text, mp3, voice, spec.rate, spec.pitch)
                dur = audio.prep_clip(mp3, wav)
                clips.append({"i": i, "text": text, "at": at, "dur": dur, "slot": slot, "wav": wav})
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"第 {i + 1} 句配音失败：{exc}")
                continue
            step("合成配音", 0.82 + 0.08 * (i + 1) / len(spec.lines))
    tts_sec = time.time() - t1

    # 没有人声也要混音：BGM / 音效可以单独成片（--no-audio、配音全失败都走这里）
    want_bgm = bool(spec.bgm_enabled) and bool(spec.bgm) and Path(spec.bgm).exists()
    try:
        want_sfx = bool(sfx.events_for(spec))
    except Exception:  # noqa: BLE001
        want_sfx = False
    if clips or want_bgm or want_sfx:
        t2 = time.time()
        step("混音混流", 0.92)
        try:
            audio.mix(silent, clips, out, spec, progress=lambda s: step(s, 0.92))
            mix_sec = time.time() - t2
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"混音失败，退回无声版：{exc}")
            if out.exists():
                out.unlink(missing_ok=True)
            shutil.copyfile(silent, out)
    else:
        if spec.voice_enabled and spec.lines:
            warnings.append("所有配音都失败了，这条是无声版")
        shutil.copyfile(silent, out)

    # ---------- 3. 校验 ----------
    step("校验", 0.98)
    info = audio.probe_streams(final)
    streams = info.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = float((info.get("format") or {}).get("duration") or 0.0)
    size = final.stat().st_size if final.exists() else 0
    if not v:
        raise RuntimeError("产物里没有视频流")
    if spec.voice_enabled and spec.lines and not a:
        warnings.append("产物里没有音频流")

    if not keep_work:
        shutil.rmtree(work, ignore_errors=True)

    step("完成", 1.0)
    return {
        "out": final,
        "duration": round(duration, 3),
        "size": size,
        "width": int(v.get("width") or spec.width),
        "height": int(v.get("height") or spec.height),
        "fps": spec.fps,
        "has_audio": bool(a),
        "duration_sec": round(time.time() - t_all, 2),
        "render_sec": round(render_sec, 2),
        "tts_sec": round(tts_sec, 2),
        "mix_sec": round(mix_sec, 2),
        "warnings": warnings,
        "style": spec.style,
        "title": spec.title or (spec.lines[0].text if spec.lines else ""),
    }
