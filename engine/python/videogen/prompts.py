"""风格提示词库：把风格库里的 Markdown 提示词变成可渲染的片子。

两条路：
1. 原生模板（a3/title/quote/explain）—— videogen 自己参数化，逐词/逐屏可控；
2. 提示词风格（本模块）—— 把某条风格提示词交给 DeepSeek，让它写一个自包含的
   Canvas 页面，再走 Edge 逐帧截图 + 配音/BGM 混流。

提示词风格的关键是**页面契约**（见 PAGE_CONTRACT）：只要生成的页面暴露
`window.DUR` 和 `window.seek(t)`，剩下的事情和原生模板完全一样。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import audio
from .paths import DATA_DIR, POWERSHELL, RENDER_PS1, ROOT, STYLE_ROOT as _STYLE_ROOT, WORK_DIR
from .spec import Line, Spec

STYLE_ROOT = Path(_STYLE_ROOT) if _STYLE_ROOT else (ROOT / "代码动画" / "风格提示词")
CACHE_DIR = WORK_DIR / "prompt_pages"
LLM_TIMEOUT = 300

CATEGORIES = {
    "A-G_代码样片": "代码样片",
    "H_现成版式": "现成版式",
    "K_卡通画风": "卡通画风",
    "L_Lemo-Opuscar": "Lemo 教程",
    "M_动态风格": "动态风格",
    "O_奥斯卡98部": "电影质感",
    "R_照参考学来的": "参考仿写",
    "V_mg-styles-15": "MG 动效",
    "X_code-video": "Code Video",
    "Z_中国风": "中国风",
}

PAGE_CONTRACT = """页面必须满足（这是逐帧截图的硬要求）：
1. 单个 HTML 文件，所有 JS/CSS 内联，不引用任何外部资源、字体、图片、网络请求。
2. Canvas 尺寸必须正好是 {w}×{h}（devicePixelRatio 固定 1）。
3. 定义 `window.DUR`（秒，等于 {dur}）和 `window.seek(t)`：`seek` 把画面画到时间 t 的那一帧，
   必须是 t 的纯函数——同一 t 永远同一画面。
4. 禁止 Math.random()（要用就自己写种子化的 mulberry32）、禁止 Date.now()/performance.now() 参与画面。
5. 想看清空帧：不要用 requestAnimationFrame 驱动画面；rAF 只用于浏览器预览，画面由 seek 画。
6. 页面打开时（t=0）就把第一帧画出来，并把 `window.READY = true` 置上。
7. 只输出 HTML 代码本身，不要解释、不要 markdown 代码围栏以外的文字。"""

LLM_SYSTEM = "你是一名 Canvas 2D / WebGL 动效工程师，擅长把一句中文风格描述写成可直接逐帧截图的单文件动画页面。"


# ---------------------------------------------------------------- catalog


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _section(md: str, heading: str) -> str:
    m = re.search(rf"^##\s*{re.escape(heading)}\s*$", md, re.M)
    if not m:
        return ""
    rest = md[m.end():]
    nxt = re.search(r"^##\s+\S", rest, re.M)
    return (rest[: nxt.start()] if nxt else rest).strip()


def _bullet(md: str, label: str) -> str:
    m = re.search(rf"^-\s*{re.escape(label)}\s*[：:]\s*(.+)$", md, re.M)
    return m.group(1).strip() if m else ""


def parse_style(md_path: Path) -> dict:
    md = _read_text(md_path)
    first = next((ln.strip() for ln in md.splitlines() if ln.startswith("# ")), "")
    title = first[2:].strip()
    code = ""
    m = re.search(r"^([A-Z]\d+)\s+(.+)$", title)
    if m:
        code, name = m.group(1), m.group(2).strip()
    else:
        code, name = md_path.stem.split("_")[0], title or md_path.stem
    folder = md_path.parent.name
    prompt = _section(md, "风格提示词")
    if not prompt:
        prompt = md.strip()[:6000]
    thumb = md_path.with_suffix(".jpg")
    return {
        "key": f"{folder}/{md_path.stem}",
        "code": code,
        "name": name,
        "category": CATEGORIES.get(folder, folder),
        "folder": folder,
        "md": str(md_path),
        "thumb": str(thumb) if thumb.exists() else "",
        "look": _bullet(md, "长什么样"),
        "fit": _bullet(md, "适合"),
        "keywords": _bullet(md, "关键词"),
        "prompt": prompt,
        "chars": len(prompt),
    }


def catalog(force: bool = False) -> dict:
    """扫一遍提示词目录，返回 {styles:[...], categories:[...]}；结果缓存到 data/videogen_work/prompt_pages/catalog.json。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / "catalog.json"
    if cache.exists() and not force:
        try:
            data = json.loads(_read_text(cache))
            if data.get("count") == len(data.get("styles") or []):
                return data
        except Exception:  # noqa: BLE001
            pass
    styles: list[dict] = []
    if STYLE_ROOT.exists():
        for folder in sorted(p for p in STYLE_ROOT.iterdir() if p.is_dir()):
            for md in sorted(folder.glob("*.md")):
                try:
                    styles.append(parse_style(md))
                except Exception:  # noqa: BLE001
                    continue
    cats: list[str] = []
    for s in styles:
        if s["category"] not in cats:
            cats.append(s["category"])
    data = {
        "count": len(styles),
        "root": str(STYLE_ROOT),
        "categories": cats,
        "styles": styles,
    }
    cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def find_style(key: str) -> dict:
    for s in catalog()["styles"]:
        if s["key"] == key or s["code"] == key:
            return s
    raise KeyError(f"没有这条风格提示词：{key}")


# ---------------------------------------------------------------- LLM


def _llm_config() -> dict:
    """OpenAI 兼容的 chat 接口：key/base/model 走环境变量（插件 Host 从 config.json 下发）。"""
    key = os.environ.get("VIDEOGEN_LLM_KEY", "").strip()
    base = os.environ.get("VIDEOGEN_LLM_BASE", "https://api.deepseek.com/v1").strip().rstrip("/")
    model = os.environ.get("VIDEOGEN_LLM_MODEL", "deepseek-chat").strip()
    if not key:
        raise RuntimeError(
            "没有配置大模型 key：在插件配置 config.json 的 llm.apiKey 填一个 OpenAI 兼容 key"
            "（默认 https://api.deepseek.com/v1），或设环境变量 VIDEOGEN_LLM_KEY"
        )
    return {"key": key, "base": base, "model": model}


def _chat(messages: list[dict], timeout: int = LLM_TIMEOUT, max_tokens: int = 8192,
          temperature: float = 0.6) -> str:
    cfg = _llm_config()
    body = json.dumps(
        {
            "model": cfg["model"],
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
            # 这个端点默认会先写一大段 reasoning，写页面时纯属浪费 token（也更容易截断）
            "thinking": {"type": "disabled"},
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        cfg["base"] + "/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + cfg["key"],
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise RuntimeError(f"LLM 请求失败 {exc.code}：{detail}") from exc
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"LLM 请求失败：{exc}") from exc
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("LLM 没有返回内容")
    return (choices[0].get("message") or {}).get("content") or ""


def _extract_html(text: str) -> str:
    m = re.search(r"```(?:html)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    start = text.find("<!DOCTYPE")
    if start < 0:
        start = text.lower().find("<html")
    if start > 0:
        text = text[start:]
    end = text.lower().rfind("</html>")
    if end > 0:
        text = text[: end + 7]
    return text.strip()


def page_prompt(style: dict, topic: str, duration: float, w: int, h: int, extra: str = "") -> str:
    return f"""按这条风格提示词做一个 {duration:g} 秒的动画短片的页面。

【风格】{style['code']} {style['name']}（{style['category']}）
【风格提示词】
{style['prompt'][:6000]}

【这次的主题】{topic or '自由发挥，用风格自带的文案'}
{extra}

{PAGE_CONTRACT.format(w=w, h=h, dur=duration)}

再补充：标题/文案用中文；如果原提示词里有参考实现代码，照着它的视觉语言写，但不要直接抄成死板的静态图。
这一段旁白会一直念到第 {duration:g} 秒，所以把动效排成 3–5 个节拍、按 p = t / window.DUR 依次触发
（大致落在 p=0.15 / 0.4 / 0.65 / 0.9），**不允许前 30% 就画完然后干等**：至少在 p=0.3、0.6、0.9 三个时刻
画面都要有肉眼可见的新变化。另外**不要循环**：不要用 `t % xxx`、不要在末帧跳回开头、不要做「播完再来一遍」，
动效要在第 {duration:g} 秒附近收在最终构图上并停住（可以留极轻微的呼吸/闪烁）。"""


# 时间盒：模型常把页面自己的时长写短（或写成循环），导致一句话里动效反复播。
# 这里把页面时间轴压/拉到这一屏的真实时长上：动效逐步播完，之后停在末帧，绝不回头。
TIMEBOX_SCRIPT = """
<script>
(function () {
  var span = __SPAN__;
  var pageDur = Number(window.DUR) || 0;
  var f = window.seek;
  if (typeof f !== 'function' || !(span > 0)) { return; }
  if (!(pageDur > 0)) { window.DUR = span; return; }
  var ratio = Math.min(3, Math.max(pageDur / span, 1 / 2.5));  // 最多放慢 2.5 倍
  var last = pageDur * 0.9995;  // 别正好落在 pageDur：有些页面用 t % DUR，会跳回开头
  window.seek = function (t) {
    var x = Math.max(0, Math.min(Number(t) || 0, span));
    return f(Math.min(last, x * ratio));
  };
  window.DUR = span;
  window.__timebox = { pageDur: pageDur, span: span, ratio: ratio };
})();
</script>
"""


def timebox_page(html: str, span: float) -> str:
    """在页面末尾（body 结束前）注入时间盒脚本，让动效铺满这一屏、播完停住。"""
    snippet = TIMEBOX_SCRIPT.replace("__SPAN__", f"{span:.3f}")
    low = html.lower()
    idx = low.rfind("</body>")
    if idx >= 0:
        return html[:idx] + snippet + html[idx:]
    return html + snippet


def render_external(html: Path, out_mp4: Path, width: int, height: int, fps: int, duration: float,
                    progress=None, timeout: int = 900, mode: str = "screenshot", fmt: str = "") -> Path:
    """把任意「遵守页面契约」的 HTML 渲成无声 mp4。

    mode="screenshot"：逐帧截浏览器画面，兼容任何页面，但慢且可能卡在合成器；
    mode="canvas" + fmt="jpeg"：直接读页面 #cv 的 toDataURL，快一个数量级（LLM 写的页面用这个）。
    渲染后端（node / powershell）由 videogen.paths.renderer_kind() 决定。
    """
    from . import render as render_mod

    chosen = fmt or ("jpeg" if mode == "canvas" else "png")
    return render_mod.render_page(
        Path(html),
        Path(out_mp4),
        width=width,
        height=height,
        fps=fps,
        duration=duration,
        mode=mode,
        fmt=chosen,
        timeout=timeout,
        progress=progress,
    )


# ---------------------------------------------------------------- build


def build(
    style_key: str,
    topic: str = "",
    extra: str = "",
    duration: float = 8.0,
    width: int = 1080,
    height: int = 1920,
    fps: int = 24,
    voice: str = "zh-CN-XiaoxiaoNeural",
    narration: str = "",
    bgm: bool = True,
    bgm_volume: float = 0.13,
    narration_enabled: bool = True,
    out_path: str | Path | None = None,
    seed: int = 0,
    keep_work: bool = False,
    progress=None,
) -> dict:
    """提示词风格出片：LLM 写页面 → 渲帧 → (可选)配音 + BGM → 混流。"""
    t_all = time.time()

    def step(stage: str, pct: float) -> None:
        if progress:
            try:
                progress(stage, max(0.0, min(1.0, pct)))
            except Exception:  # noqa: BLE001
                pass

    style = find_style(style_key)
    slug = f"prompt-{style['code']}-{time.strftime('%Y%m%d-%H%M%S')}-{abs(hash(style_key + topic)) % 9973:04d}"
    work = WORK_DIR / slug
    work.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    step("准备", 0.02)
    sig = f"{style['key']}|{topic}|{extra}|{duration:g}|{width}x{height}|{fps}|{seed}"
    cache_key = re.sub(r"[^0-9A-Za-z]+", "-", f"{style['code']}-{abs(hash(sig)) % 100000}")[:60]
    html = CACHE_DIR / f"{cache_key}.html"
    if html.exists() and html.stat().st_size > 800:
        step("页面（缓存）", 0.06)
    else:
        step("AI 写页面", 0.04)
        text = _chat(
            [
                {"role": "system", "content": LLM_SYSTEM},
                {"role": "user", "content": page_prompt(style, topic, duration, width, height, extra)},
            ]
        )
        page = _extract_html(text)
        if len(page) < 800:
            # 第一次多半是被 reasoning 截断了：再要一次，只要页面，别解释
            step("AI 写页面（重试）", 0.04)
            page = _extract_html(
                _chat(
                    [
                        {"role": "system", "content": LLM_SYSTEM},
                        {"role": "user", "content": page_prompt(style, topic, duration, width, height, extra)},
                        {"role": "assistant", "content": text or "（上一版没写完）"},
                        {"role": "user", "content": "上一版没写完/没给出完整页面。请直接输出完整可运行的单个 HTML 文件，不要解释。"},
                    ]
                )
            )
        html.write_text(page, encoding="utf-8")
        if html.stat().st_size < 500:
            raise RuntimeError("AI 返回的页面太小，可能生成失败了")

    silent = work / "silent.mp4"

    def frame_progress(done: int, total: int) -> None:
        step("渲染画面", 0.08 + 0.72 * (done / max(1, total)))

    # 套上时间盒再渲：动效在整条旁白里逐步播完，播完停在末帧，不循环
    boxed = work / (html.stem + "-boxed.html")
    boxed.write_text(timebox_page(html.read_text(encoding="utf-8"), duration), encoding="utf-8")

    step("渲染画面", 0.08)
    try:
        # 页面契约要求 #cv canvas：走 canvas 模式（快约 8 倍，且避开截图卡死）
        render_external(boxed, silent, width, height, fps, duration, progress=frame_progress,
                        mode="canvas", fmt="jpeg")
    except Exception as canvas_exc:  # noqa: BLE001
        silent.unlink(missing_ok=True)
        try:
            render_external(boxed, silent, width, height, fps, duration, progress=frame_progress)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"渲染画面失败：{exc}（canvas 模式也失败：{canvas_exc}）") from exc

    out = Path(out_path) if out_path else (DATA_DIR / time.strftime("%Y%m%d") / f"{slug}.mp4")
    out.parent.mkdir(parents=True, exist_ok=True)

    spec = Spec(
        style="prompt",
        width=width,
        height=height,
        fps=fps,
        duration=round(duration, 3),
        title=topic or style["name"],
        footer="AI 生成 · Deepseek-Chat",
        lines=[Line(text=narration or topic or style["name"], at=0.0)],
        voice_enabled=bool(narration_enabled and (narration or topic)),
        voice=voice,
        bgm_enabled=bool(bgm),
        bgm_volume=bgm_volume,
    )

    warnings: list[str] = []
    clips: list[dict] = []
    if spec.voice_enabled:
        step("合成配音", 0.84)
        try:
            mp3 = work / "narr.mp3"
            wav = work / "narr.wav"
            audio.tts_line(spec.lines[0].text, mp3, voice, spec.rate, spec.pitch)
            dur = audio.prep_clip(mp3, wav)
            clips.append({"i": 0, "text": spec.lines[0].text, "at": 0.0, "dur": dur, "slot": duration, "wav": wav})
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"旁白配音失败：{exc}")

    want_bgm = bool(spec.bgm_enabled) and Path(str(spec.bgm)).exists()
    if clips or want_bgm:
        step("混音混流", 0.92)
        try:
            audio.mix(silent, clips, out, spec)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"混音失败，退回无声版：{exc}")
            out.write_bytes(silent.read_bytes())
    else:
        out.write_bytes(silent.read_bytes())

    step("校验", 0.98)
    info = audio.probe_streams(out)
    streams = info.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not v:
        raise RuntimeError("产物里没有视频流")
    if not keep_work:
        import shutil

        shutil.rmtree(work, ignore_errors=True)
    step("完成", 1.0)
    return {
        "out": out,
        "duration": round(float((info.get("format") or {}).get("duration") or 0.0), 3),
        "size": out.stat().st_size,
        "width": int(v.get("width") or width),
        "height": int(v.get("height") or height),
        "fps": fps,
        "has_audio": bool(a),
        "duration_sec": round(time.time() - t_all, 2),
        "warnings": warnings,
        "style": f"{style['code']} {style['name']}",
        "title": topic or style["name"],
        "html": str(html),
    }


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print("用法：python -m videogen.prompts [--catalog OUT.json] [--list]")
        return 0
    if argv[0] == "--catalog":
        out = Path(argv[1]) if len(argv) > 1 else (CACHE_DIR / "catalog.json")
        data = catalog(force=True)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        print(f"写好了：{out}（{data['count']} 条风格）")
        return 0
    if argv[0] == "--list":
        data = catalog()
        print(f"{data['count']} 条风格，{len(data['categories'])} 个分类")
        for c in data["categories"]:
            n = sum(1 for s in data["styles"] if s["category"] == c)
            print(f"  {c}: {n}")
        return 0
    print("未知参数", argv)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
