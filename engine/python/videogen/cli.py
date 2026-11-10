"""命令行入口：`python -m videogen.cli <子命令>`。

    python -m videogen.cli styles
    python -m videogen.cli quick --words "你|懂什么|听话" --style a3
    python -m videogen.cli build examples/a3_roast.json
    python -m videogen.cli worker --once
    python -m videogen.cli list
    python -m videogen.cli doctor
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import audio, jobs, pipeline, render, sfx, styles, worker
from .paths import EDGE, check_audio_env, check_env
from .spec import Spec, split_words


def _spec_from_args(args: argparse.Namespace) -> Spec:
    words = getattr(args, "words", None) or ""
    lines = []
    if words:
        lines = [{"text": w} for w in split_words(words)]
    if getattr(args, "text", None):
        lines.append({"text": args.text})
    data: dict = {
        "style": args.style,
        "width": getattr(args, "width", 1080) or 1080,
        "height": getattr(args, "height", 1920) or 1920,
        "beat": getattr(args, "beat", 0) or 0,
        "title": getattr(args, "title", "") or "",
        "subtitle": getattr(args, "subtitle", "") or "",
        "lines": lines if lines else ([{"text": args.title}] if getattr(args, "title", "") else []),
    }
    if getattr(args, "out", None):
        data["out"] = args.out
    if getattr(args, "voice", None):
        data["voice"] = {"name": args.voice, "enabled": not getattr(args, "no_audio", False)}
    elif getattr(args, "no_audio", False):
        data["voice"] = {"enabled": False}
    if getattr(args, "no_bgm", False):
        data["bgm"] = {"enabled": False}
    if getattr(args, "no_sfx", False):
        data["sfx"] = {"enabled": False}
    elif getattr(args, "sfx_volume", None) is not None:
        data["sfx"] = {"volume": float(args.sfx_volume)}
    return Spec.from_dict(data)


def _print_result(result: dict) -> None:
    out = Path(result["out"])
    print()
    print(f"  成片   {out}")
    print(f"  规格   {result['width']}x{result['height']} @{result['fps']}fps  {result['duration']}s  "
          f"{result['size'] / 1024:.0f} KB  {'有声' if result['has_audio'] else '无声'}")
    print(f"  耗时   {result['duration_sec']}s（渲染 {result['render_sec']}s / 配音 {result['tts_sec']}s / "
          f"混音 {result['mix_sec']}s）")
    for w in result.get("warnings") or []:
        print(f"  提醒   {w}")


def cmd_styles(_: argparse.Namespace) -> int:
    for s in styles.describe():
        print(f"{s['key']:<7} {s['label']:<6} beat={s['default_beat']}  最多 {s['max_lines']} 句")
        print(f"        {s['blurb']}")
    return 0


def cmd_quick(args: argparse.Namespace) -> int:
    spec = _spec_from_args(args)
    result = pipeline.build(spec, out_path=args.out or None, keep_work=args.keep_work)
    _print_result(result)
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    spec = Spec.from_json_file(args.spec_json)
    if args.out:
        spec.out = args.out
    result = pipeline.build(spec, out_path=spec.out or None, keep_work=args.keep_work)
    _print_result(result)
    return 0


def cmd_enqueue(args: argparse.Namespace) -> int:
    spec = _spec_from_args(args)
    styles.resolve(spec)
    spec.validate()
    jid = jobs.enqueue(spec.to_dict(), user_id=args.user or "cli", umo="")
    print(f"已入队 #{jid}")
    return 0


def cmd_worker(args: argparse.Namespace) -> int:
    worker.loop(poll=args.poll, once=args.once)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    rows = jobs.recent(limit=args.limit, status=args.status or "")
    if not rows:
        print("（队列里还没有任务）")
        return 0
    for r in rows:
        marker = {"done": "✓", "failed": "✗", "running": "▶", "pending": "·"}.get(r["status"], "?")
        extra = ""
        if r["status"] == "done":
            extra = f"{r['duration']}s {r['size'] / 1024:.0f}KB {Path(r['out_path']).name}"
        elif r["status"] == "failed":
            extra = (r["err"] or "").splitlines()[0][:70]
        else:
            extra = f"{r['stage']} {r['progress'] * 100:.0f}%"
        print(f"{marker} #{r['id']:<4} {r['created_at']}  {r['style']:<6} {r['user_id']:<12} {extra}")
    print(f"\n统计：{jobs.stats()}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    job = jobs.get(args.job_id)
    if not job:
        print(f"没有 #{args.job_id}")
        return 1
    print(json.dumps(job, ensure_ascii=False, indent=2))
    return 0


def cmd_preview(args: argparse.Namespace) -> int:
    spec = _spec_from_args(args)
    styles.resolve(spec)
    spec.validate()
    url = render.preview_page(spec, Path.cwd() / "_preview", args.at)
    print(url)
    if args.open:
        subprocess.run([str(EDGE), url], check=False)
    return 0


def cmd_doctor(_: argparse.Namespace) -> int:
    missing = check_env()
    audio_missing = check_audio_env()
    print("硬依赖：")
    for label, p in (
        ("ffmpeg", "D:/ffmpeg"),
        ("Edge", "C:/Program Files (x86)/Microsoft/Edge"),
    ):
        _ = label, p
    for item in missing:
        print(f"  ✗ {item}")
    if not missing:
        print("  ✓ ffmpeg / ffprobe / Edge / powershell / render-cdp.ps1 都在")
    print("配音链路：")
    for item in audio_missing:
        print(f"  ✗ {item}")
    if not audio_missing:
        print(f"  ✓ {audio.VOICE_PY if hasattr(audio, 'VOICE_PY') else 'edge-tts'} 就绪")
    print(f"数据目录：{pipeline.DATA_DIR}")
    print("音效层：")
    try:
        rows = sfx.describe()
        print("  ✓ " + "  ".join(f"{n} {d:.2f}s·{src}" for n, src, d in rows))
        print(f"    覆盖目录：{sfx.OVERRIDE_DIR}（放同名 wav 即可替换）")
    except Exception as exc:  # noqa: BLE001
        print(f"  ✗ 合成失败：{exc}")
    print(f"任务库：  {jobs.DB_PATH}")
    print(f"任务统计：{jobs.stats()}")
    return 0 if not missing else 1


def main(argv: list[str] | None = None) -> int:
    # Windows 控制台默认 GBK，直接 print 中文/符号会 mojibake 或 UnicodeEncodeError
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass

    p = argparse.ArgumentParser(prog="videogen", description="AI 速成视频：风格页面 + 逐帧渲染 + edge-tts 配音")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("styles", help="列出可用样式")
    sp.set_defaults(func=cmd_styles)

    def add_spec_args(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--style", default="a3", help="a3 / title / quote")
        sp.add_argument("--words", default="", help="词块，用 | 或逗号分隔，如 '你|懂什么|听话'")
        sp.add_argument("--text", default="", help="追加一整句")
        sp.add_argument("--title", default="", help="大标题（title/quote 样式）")
        sp.add_argument("--subtitle", default="", help="副标题")
        sp.add_argument("--beat", type=float, default=0, help="节拍秒数，默认按样式")
        sp.add_argument("--width", type=int, default=1080)
        sp.add_argument("--height", type=int, default=1920)
        sp.add_argument("--voice", default="", help="edge-tts 音色，如 zh-CN-YunxiNeural")
        sp.add_argument("--no-audio", action="store_true", help="不要配音")
        sp.add_argument("--no-bgm", action="store_true", help="不要 BGM")
        sp.add_argument("--no-sfx", action="store_true", help="不要音效")
        sp.add_argument("--sfx-volume", type=float, default=None,
                        help="音效整体音量（默认 0.14；0.08 轻 / 0.14 中 / 0.20 重）")
        sp.add_argument("--out", default="", help="输出 mp4 路径")

    sp = sub.add_parser("quick", help="一句话出一条片子（同步）")
    add_spec_args(sp)
    sp.add_argument("--keep-work", action="store_true")
    sp.set_defaults(func=cmd_quick)

    sp = sub.add_parser("build", help="按 spec.json 出片")
    sp.add_argument("spec_json")
    sp.add_argument("--out", default="")
    sp.add_argument("--keep-work", action="store_true")
    sp.set_defaults(func=cmd_build)

    sp = sub.add_parser("enqueue", help="入队（交给 worker）")
    add_spec_args(sp)
    sp.add_argument("--user", default="")
    sp.set_defaults(func=cmd_enqueue)

    sp = sub.add_parser("worker", help="跑队列")
    sp.add_argument("--once", action="store_true", help="清一遍就退出")
    sp.add_argument("--poll", type=float, default=1.5)
    sp.set_defaults(func=cmd_worker)

    sp = sub.add_parser("list", help="看队列")
    sp.add_argument("--limit", type=int, default=15)
    sp.add_argument("--status", default="")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("show", help="看一条任务详情")
    sp.add_argument("job_id", type=int)
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("preview", help="生成预览页 URL（调样式用）")
    add_spec_args(sp)
    sp.add_argument("--at", type=float, default=None, help="定格到第几秒")
    sp.add_argument("--open", action="store_true", help="顺便用 Edge 打开")
    sp.set_defaults(func=cmd_preview)

    sp = sub.add_parser("doctor", help="自检环境")
    sp.set_defaults(func=cmd_doctor)

    args = p.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
