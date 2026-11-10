"""渲染：把 spec 变成一个单文件 HTML，再交给浏览器（CDP）逐帧截图 + ffmpeg 编码。

两种渲染后端，参数与输出格式完全一致：
- node      ：engine/node/render.mjs，Windows / macOS / Linux 通用（首选）
- powershell：tools/render-cdp.ps1，Windows 兜底（没有 node 的机器）

用哪个由 paths.renderer_kind() 决定，可用环境变量 VIDEOGEN_RENDERER=node|powershell 强制。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

from .paths import (
    EDGE,
    FFMPEG,
    NODE_BIN,
    NODE_RENDER,
    POWERSHELL,
    RENDER_PS1,
    TEMPLATES,
    renderer_kind,
)
from .spec import Spec
from .styles import STYLES
from . import stickers


class RenderError(RuntimeError):
    pass


def build_page(spec: Spec, out_html: Path) -> Path:
    """模板 + spec → 自包含页面。页面不读网络、不读文件，只认内联 JSON。

    表情包会先在缓存里抽成 PNG 序列，再以 data URI 内联 —— 这样逐帧截图可复现，
    也不会因为 file:// 图片污染 canvas 让 `toDataURL()` 抛异常。
    """
    info = STYLES[spec.style]
    tpl = TEMPLATES / info.template
    if not tpl.exists():
        raise RenderError(f"模板不存在：{tpl}")
    html = tpl.read_text(encoding="utf-8")

    page = spec.page_spec()
    if spec.style == "explain":
        for i, line in enumerate(spec.lines):
            if not line.sticker:
                continue
            gif = Path(line.sticker)
            if not gif.exists():
                continue
            try:
                page["lines"][i]["frames"] = stickers.frames_data_uris(gif)
            except Exception as exc:  # noqa: BLE001 —— 表情包坏了不该让整片挂掉
                raise RenderError(f"表情包抽帧失败（{gif.name}）：{exc}") from exc

    payload = json.dumps(page, ensure_ascii=False)
    # 用户文本里若有 </script> 会提前闭合脚本块，转义掉
    payload = payload.replace("</", "<\\/")
    if "__SPEC_JSON__" not in html:
        raise RenderError(f"模板缺 __SPEC_JSON__ 占位符：{tpl}")
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(html.replace("__SPEC_JSON__", payload), encoding="utf-8")
    return out_html


def render_command(kind: str, page: Path, out_mp4: Path, *, width: int, height: int, fps: int,
                   duration: float, mode: str, fmt: str, timeout: int) -> list[str]:
    """按后端拼命令行。两边参数名一致、stdout 都打 `frame N/M`。"""
    if kind == "node":
        return [
            str(NODE_BIN),
            str(NODE_RENDER),
            "--html", str(page),
            "--out", str(out_mp4),
            "--width", str(width),
            "--height", str(height),
            "--fps", str(fps),
            "--duration", f"{duration:.3f}",
            "--mode", mode,
            "--format", fmt,
            "--timeout", str(max(60, int(timeout))),
        ]
    return [
        str(POWERSHELL), "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(RENDER_PS1),
        "-Html", str(page),
        "-Out", str(out_mp4),
        "-Width", str(width),
        "-Height", str(height),
        "-Fps", str(fps),
        "-Duration", f"{duration:.3f}",
        "-Mode", mode,
        "-Format", fmt,
        "-Timeout", str(max(60, int(timeout))),
    ]


def render_page(
    page: Path,
    out_mp4: Path,
    *,
    width: int,
    height: int,
    fps: int,
    duration: float,
    mode: str = "canvas",
    fmt: str = "jpeg",
    timeout: int = 900,
    progress=None,
) -> Path:
    """把任意「遵守页面契约」的 HTML 渲成无声 mp4。progress(done, total) 可选。"""
    page = Path(page)
    if not page.exists():
        raise RenderError(f"页面不存在：{page}")
    out_mp4 = Path(out_mp4)
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    kind = renderer_kind()
    try:
        cmd = render_command(
            kind, page, out_mp4,
            width=width, height=height, fps=fps,
            duration=duration, mode=mode, fmt=fmt, timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        raise RenderError(f"渲染器不可用（{kind}）：{exc}") from exc

    env = dict(os.environ)
    if kind == "node":
        env.setdefault("VIDEOGEN_EDGE", str(EDGE))
        env.setdefault("VIDEOGEN_FFMPEG", str(FFMPEG))

    start = time.time()
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        bufsize=1,
        env=env,
    )
    tail: list[str] = []
    code = 0
    try:
        for line in proc.stdout:  # type: ignore[union-attr]
            if time.time() - start > timeout + 60:
                proc.kill()
                raise RenderError(
                    f"渲染超过 {timeout} 秒仍没结束，已终止（最后输出：{tail[-1] if tail else '无'}）"
                )
            line = (line or "").rstrip()
            if not line:
                continue
            tail.append(line)
            if len(tail) > 40:
                tail.pop(0)
            m = re.search(r"frame\s+(\d+)/(\d+)", line)
            if m and progress:
                try:
                    progress(int(m.group(1)), int(m.group(2)))
                except Exception:  # noqa: BLE001
                    pass
        code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise RenderError(f"渲染超时（>{timeout}s）")
    finally:
        if proc.poll() is None:
            proc.kill()

    if code != 0 or not out_mp4.exists():
        raise RenderError(f"渲染失败（{kind}）：\n" + "\n".join(tail[-15:]))
    if out_mp4.stat().st_size < 1024:
        raise RenderError("渲染产物是空文件")
    return out_mp4


def render(
    spec: Spec,
    out_mp4: Path,
    workdir: Path,
    progress=None,
    timeout: int = 900,
) -> Path:
    """渲染无声 mp4。progress(done_frames, total_frames) 可选。"""
    workdir.mkdir(parents=True, exist_ok=True)
    page = build_page(spec, workdir / "index.html")
    # explain 每帧都有渐变/半透明叠加，PNG 压帧要 200ms+；JPEG 快一个数量级（q=0.93 在暗底上看不出差）
    fmt = "jpeg" if spec.style == "explain" else "png"
    return render_page(
        page,
        out_mp4,
        width=spec.width,
        height=spec.height,
        fps=spec.fps,
        duration=spec.duration,
        mode="canvas",
        fmt=fmt,
        timeout=timeout,
        progress=progress,
    )


def preview_page(spec: Spec, workdir: Path, at: float | None = None) -> str:
    """生成页面并返回 file:// URL；at 不为 None 时用 ?t= 定格到那一秒。

    调样式时用这个：改完模板刷新浏览器就看见，不用等整片渲完。
    """
    workdir.mkdir(parents=True, exist_ok=True)
    page = build_page(spec, workdir / "preview.html")
    url = page.resolve().as_uri()
    if at is not None:
        url = f"{url}?t={at:g}&w={spec.width}&h={spec.height}"
    return url
