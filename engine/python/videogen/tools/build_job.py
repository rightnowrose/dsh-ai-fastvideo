"""给 DSH 面板用的任务执行器：读一个 job JSON，边跑边往 stdout 写 JSON 行。

面板（DSH Host 侧插件）spawn 这个脚本，逐行解析：
    {"type":"progress","stage":"渲染画面","pct":0.42}
    {"type":"log","text":"..."}
    {"type":"done","out":"F:\\...\\xxx.mp4", ...}
    {"type":"error","message":"..."}

为什么单独写一个脚本而不复用 CLI：CLI 的进度是给人看的，这里是给机器看的。
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve()
APPS = HERE.parents[2]  # engine/python —— videogen 包的父目录
if str(APPS) not in sys.path:
    sys.path.insert(0, str(APPS))

from videogen import auto_film, paths, pipeline, prompts  # noqa: E402
from videogen.spec import Line, Spec, split_words  # noqa: E402


def emit(**kw) -> None:
    sys.stdout.write(json.dumps(kw, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def progress(stage: str, pct: float) -> None:
    emit(type="progress", stage=stage, pct=round(float(pct), 4))


def build_native(cfg: dict) -> dict:
    words = cfg.get("words") or ""
    lines = [Line(text=w) for w in split_words(words, limit=int(cfg.get("max_words") or 8))]
    if not lines:
        raise RuntimeError("没有可用的台词：请在面板里填「台词/主题」")
    spec = Spec(
        style=cfg.get("style") or "a3",
        width=int(cfg.get("width") or 1080),
        height=int(cfg.get("height") or 1920),
        fps=int(cfg.get("fps") or 24),
        beat=float(cfg.get("beat") or 0.55),
        title=cfg.get("title") or "",
        subtitle=cfg.get("subtitle") or "",
        lines=lines,
        voice_enabled=not cfg.get("no_audio"),
        voice=cfg.get("voice") or "zh-CN-XiaoxiaoNeural",
        bgm_enabled=not cfg.get("no_bgm"),
        bgm_volume=float(cfg.get("bgm_volume") or 0.16),
        sfx_enabled=not cfg.get("no_sfx"),
        sfx_volume=float(cfg.get("sfx_volume") or 0.14),
    )
    return pipeline.build(spec, out_path=cfg.get("out") or None, progress=progress)


def build_prompt(cfg: dict) -> dict:
    return prompts.build(
        style_key=cfg.get("style_key") or "",
        topic=cfg.get("topic") or "",
        extra=cfg.get("extra") or "",
        duration=float(cfg.get("duration") or 8.0),
        width=int(cfg.get("width") or 1080),
        height=int(cfg.get("height") or 1920),
        fps=int(cfg.get("fps") or 24),
        voice=cfg.get("voice") or "zh-CN-XiaoxiaoNeural",
        narration=cfg.get("narration") or "",
        narration_enabled=not cfg.get("narration_disabled"),
        bgm=not cfg.get("no_bgm"),
        bgm_volume=float(cfg.get("bgm_volume") or 0.13),
        out_path=cfg.get("out") or None,
        progress=progress,
    )


def build_auto(cfg: dict) -> dict:
    return auto_film.build(
        brief=cfg.get("brief") or "",
        duration=float(cfg.get("duration") or 90.0),
        width=int(cfg.get("width") or 1080),
        height=int(cfg.get("height") or 1920),
        fps=int(cfg.get("fps") or 24),
        voice=cfg.get("voice") or "zh-CN-XiaoxiaoNeural",
        rate=cfg.get("rate") or "+6%",
        context_kind=cfg.get("context") or "none",
        context_text=cfg.get("context_text") or "",
        bgm=None if cfg.get("no_bgm") else str(paths.DEFAULT_BGM),
        bgm_volume=float(cfg.get("bgm_volume") if cfg.get("bgm_volume") is not None else 0.13),
        sfx_volume=float(cfg.get("sfx_volume") if cfg.get("sfx_volume") is not None else 0.14),
        narration=not cfg.get("no_narration"),
        extra=cfg.get("extra") or "",
        stickers_enabled=not cfg.get("no_stickers"),
        style_keys=cfg.get("styles") or [],
        out_path=cfg.get("out") or None,
        progress=progress,
    )


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        emit(type="error", message="用法：build_job.py <job.json>")
        return 2
    job_path = Path(argv[1])
    cfg = json.loads(job_path.read_text(encoding="utf-8-sig"))
    mode = cfg.get("mode") or "native"
    try:
        if mode == "auto":
            result = build_auto(cfg.get("auto") or {})
        elif mode == "prompt":
            result = build_prompt(cfg.get("prompt") or {})
        else:
            result = build_native(cfg.get("native") or {})
        emit(
            type="done",
            out=str(result.get("out")),
            duration=result.get("duration"),
            size=result.get("size"),
            width=result.get("width"),
            height=result.get("height"),
            fps=result.get("fps"),
            has_audio=result.get("has_audio"),
            seconds=result.get("duration_sec"),
            warnings=result.get("warnings") or [],
            style=result.get("style"),
            title=result.get("title"),
        )
        return 0
    except Exception as exc:  # noqa: BLE001
        emit(type="error", message=str(exc), trace=traceback.format_exc()[-1500:])
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
