"""给「风格提示词」目录里没有同名 .jpg 的风格批量补缩略图。

做法（与面板出片同一条链路）：
  1. 读该风格的 .md 提示词，让 DeepSeek 写一个遵守页面契约的单文件动画页面；
  2. 用本机 Edge 逐帧截图渲成 2.6 秒的无声 mp4（960x720）；
  3. 在 0.9/1.6/2.3 秒各取一帧，挑细节最多（文件最大）的一张，缩成 800x600 存成与 .md 同名的 .jpg。

页面缓存在 data/videogen_work/thumbs/pages/，已存在的 jpg 默认跳过，可反复跑续传。

    python tools/thumb_fill.py                # 补全部缺的
    python tools/thumb_fill.py --limit 3      # 先试 3 条
    python tools/thumb_fill.py --category 卡通画风 --force
    python tools/thumb_fill.py --repair       # 只重做空白/纯色的静帧（<12KB）
    python tools/thumb_fill.py --dry-run      # 只列清单
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
APPS = HERE.parents[2]
if str(APPS) not in sys.path:
    sys.path.insert(0, str(APPS))

from videogen import paths, prompts  # noqa: E402

WORK = paths.WORK_DIR / "thumbs"
PAGE_DIR = WORK / "pages"
VIDEO_DIR = WORK / "video"
LOG_PATH = WORK / "fill.log"
FAIL_PATH = WORK / "failures.json"

WIDTH, HEIGHT, FPS = 960, 720, 24
CLIP_SECONDS = 2.6
# 取帧候选点：0.9/1.6/2.3 秒各出一张，挑文件最大（细节最多）的那张。
# 单点取帧踩过坑：不少页面前 1 秒还在淡入，1.2 秒抓到的是纯黑或纯白。
STILL_CANDIDATES = (0.9, 1.6, 2.3)
OUT_W, OUT_H = 800, 600
# 小于这个体积的 jpg 基本是空白/纯色，用 --repair 重做。
REPAIR_BELOW = 12 * 1024

# 缩略图要「代表性」，所以限定文案量，并要求代表画面在取帧时刻已经出现。
THUMB_EXTRA = (
    "这个页面只用于生成风格缩略图：请让最具代表性、最像这条风格的画面在 "
    f"{STILL_CANDIDATES[0]:g} 到 {STILL_CANDIDATES[-1]:g} 秒之间稳定可见"
    "（别在这段时间里闪白、全黑或只剩背景）；"
    "画面要有明确的主体和构图，对比度要够，别做成整屏都很淡的渐变或大面积留白；"
    "画面里的文字尽量少（不超过 6 个字），不要画 UI 边框、播放器或说明文字。"
)


def log(message: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {message}"
    print(line, flush=True)
    WORK.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as fp:
        fp.write(line + "\n")


def out_path(style: dict) -> Path:
    return Path(style["md"]).with_suffix(".jpg")


def run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "").strip()[-600:] or f"exit {proc.returncode}")


def make_page(style: dict) -> Path:
    PAGE_DIR.mkdir(parents=True, exist_ok=True)
    sig = json.dumps([style["key"], prompts.PAGE_CONTRACT[:40], WIDTH, HEIGHT, THUMB_EXTRA], ensure_ascii=False)
    digest = hashlib.sha1(sig.encode("utf-8")).hexdigest()[:8]
    html_path = PAGE_DIR / f"{style['code']}-{digest}.html"
    if html_path.exists() and html_path.stat().st_size > 800:
        return html_path

    user = prompts.page_prompt(style, topic=style["name"], duration=CLIP_SECONDS, w=WIDTH, h=HEIGHT, extra=THUMB_EXTRA)
    messages = [
        {"role": "system", "content": prompts.LLM_SYSTEM},
        {"role": "user", "content": user},
    ]
    last_error = None
    for attempt in range(1, 3):
        try:
            text = prompts._chat(messages, max_tokens=8192)  # noqa: SLF001
            html = prompts._extract_html(text)  # noqa: SLF001
            if len(html) < 800:
                raise RuntimeError(f"页面太短（{len(html)} 字符）")
            html_path.write_text(html, encoding="utf-8")
            return html_path
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            log(f"  第 {attempt} 次写页面失败：{exc}")
            time.sleep(2)
    raise RuntimeError(f"写页面失败：{last_error}")


def make_still(style: dict) -> tuple[Path, int]:
    html_path = make_page(style)
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    mp4 = VIDEO_DIR / f"{style['code']}-{html_path.stem.split('-')[-1]}.mp4"
    if not mp4.exists() or mp4.stat().st_size < 2048:
        prompts.render_external(html_path, mp4, WIDTH, HEIGHT, FPS, CLIP_SECONDS)

    target = out_path(style)
    target.parent.mkdir(parents=True, exist_ok=True)
    best: Path | None = None
    best_size = 0
    for at in STILL_CANDIDATES:
        probe = VIDEO_DIR / f"_probe-{style['code']}-{at:.1f}.jpg"
        run([
            str(paths.FFMPEG), "-y", "-v", "error",
            "-ss", f"{at:.3f}", "-i", str(mp4),
            "-frames:v", "1",
            "-vf", f"scale={OUT_W}:{OUT_H}:flags=lanczos",
            "-q:v", "3",
            str(probe),
        ])
        size = probe.stat().st_size if probe.exists() else 0
        if size > best_size:
            if best is not None and best.exists():
                best.unlink()
            best, best_size = probe, size
        else:
            probe.unlink(missing_ok=True)

    if best is None or best_size < 2048:
        raise RuntimeError("取帧失败")
    if target.exists():
        target.unlink()
    best.replace(target)
    return target, target.stat().st_size


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    ap = argparse.ArgumentParser(description="给风格提示词补缩略图")
    ap.add_argument("--limit", type=int, default=0, help="最多处理几条（0 = 全部）")
    ap.add_argument("--category", default="", help="只处理某个分类名（模糊匹配）")
    ap.add_argument("--force", action="store_true", help="已有 jpg 也重做")
    ap.add_argument("--repair", action="store_true", help=f"只重做体积小于 {REPAIR_BELOW // 1024}KB 的静帧（多为空白帧）")
    ap.add_argument("--dry-run", action="store_true", help="只打印清单")
    args = ap.parse_args(argv)

    data = prompts.catalog()
    styles = data["styles"]
    todo = []
    for style in styles:
        if args.category and args.category not in style.get("category", ""):
            continue
        jpg = out_path(style)
        if args.repair:
            if jpg.exists() and jpg.stat().st_size >= REPAIR_BELOW:
                continue
        elif jpg.exists() and not args.force:
            continue
        todo.append(style)
    if args.limit:
        todo = todo[: args.limit]

    total_all = len(styles)
    have = sum(1 for s in styles if out_path(s).exists())
    log(f"风格总数 {total_all}，已有静帧 {have}，本次待补 {len(todo)}")
    if args.dry_run:
        for style in todo:
            print(f"  {style['code']:<6} {style['name']:<16} {style['category']}")
        return 0

    ok = 0
    failures: list[dict] = []
    for index, style in enumerate(todo, start=1):
        started = time.time()
        tag = f"[{index}/{len(todo)}] {style['code']} {style['name']}（{style['category']}）"
        log(tag + " ...")
        try:
            target, size = make_still(style)
            ok += 1
            log(f"{tag} OK {size / 1024:.0f}KB 用时 {time.time() - started:.1f}s -> {target.relative_to(prompts.STYLE_ROOT)}")
        except Exception as exc:  # noqa: BLE001
            failures.append({"key": style["key"], "name": style["name"], "error": str(exc)[:400]})
            log(f"{tag} 失败：{str(exc)[:300]}")
            WORK.mkdir(parents=True, exist_ok=True)
            FAIL_PATH.write_text(json.dumps(failures, ensure_ascii=False, indent=1), encoding="utf-8")

    # 让面板下次读到新的 hasThumb
    cache = prompts.CACHE_DIR / "catalog.json"
    if cache.exists():
        cache.unlink()
    prompts.catalog(force=True)

    log(f"=== 完成：成功 {ok}，失败 {len(failures)}，总计已有 {sum(1 for s in styles if out_path(s).exists())}/{total_all} ===")
    if failures:
        log("失败清单见 " + str(FAIL_PATH))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
