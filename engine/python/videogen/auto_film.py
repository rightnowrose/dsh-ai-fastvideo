"""AI 自拟讲解片：只给主题/要求，剩下的（分镜、文案、念白、表情包）由模型自己写。

和 prompts.py 的区别：
- prompts.py 是「一条风格提示词 + 主题 → AI 写一个单文件网页」，适合 4–30 秒的视觉短片；
- 本模块是「一句话要求 → AI 写分镜脚本 → 讲解片模板渲染」，适合 30 秒–6 分钟的知识型片子，
  因为一屏一个知识点、可以无限接屏，而且每屏的念白时长是量出来的，音画天然同步。

流程：收集上下文 → LLM 出场景 JSON → 逐屏 TTS 量时长 → explain Spec → pipeline.build。
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from . import audio, paths, pipeline, prompts, stickers
from .spec import Line, Spec
from .styles import STYLES, resolve

Progress = Callable[[str, float], None] | None

STICKER_DIR = paths._env_opt("VIDEOGEN_STICKERS") or (paths.ROOT / "data" / "stickers" / "maid")

# 给模型挑的表情包白名单：只放「表情明确、适合做知识讲解配图」的那些，避免它挑到莫名其妙的帧。
STICKER_CHOICES = [
    "dfy_2N09_思考",
    "dfy_N11_诶嘿",
    "dfy_16N01_超开心",
    "dfy_EX20_主唱",
    "dfy_EX56_自动画画",
    "dfy_EX55_自动工作",
    "dfy_6N02_猫在么",
    "dfy_10N12_好多猫",
    "gpt_TT02_比心",
    "gpt_6N02_喝药",
    "gpt_EX12_发布会",
    "dfy_10N08_敲黑板",
    "dfy_9N07_蹦",
    "dfy_11N02_WATCHING-YOU",
]

SCENE_BUDGET = 7.6  # 每屏平均秒数，用来估算屏数


def _report(progress: Progress, stage: str, pct: float) -> None:
    if progress:
        progress(stage, pct)


# ---------------------------------------------------------------- 上下文打包

def _read_head(path: Path, limit: int = 2400) -> str:
    try:
        text = path.read_text("utf-8", errors="replace")
    except OSError:
        return ""
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text[:limit]


def _tree(root: Path, depth: int = 2, limit: int = 120) -> list[str]:
    out: list[str] = []
    root = root.resolve()

    def walk(cur: Path, level: int) -> None:
        if len(out) >= limit or level > depth:
            return
        try:
            entries = sorted(cur.iterdir(), key=lambda p: (p.is_file(), p.name))
        except OSError:
            return
        for entry in entries:
            if len(out) >= limit:
                return
            if entry.name.startswith((".", "__pycache__", "node_modules", ".venv")):
                continue
            rel = entry.relative_to(root)
            if entry.is_dir():
                out.append(f"{rel}/")
                walk(entry, level + 1)
            else:
                out.append(f"{rel}  ({entry.stat().st_size} B)")

    walk(root, 1)
    return out


def gather_context(kind: str) -> str:
    """把工作区/插件的事实压成一段给模型看的资料。kind: workspace|plugins|none。"""
    if kind in ("", "none"):
        return ""
    chunks: list[str] = []
    if kind in ("workspace", "all"):
        readme = paths.ROOT / "README.md"
        head = _read_head(readme)
        if head:
            chunks.append("# 工作区 README 摘要\n" + head)
        docs = [p.name for p in paths.ROOT.glob("*.md") if p.is_file()]
        if docs:
            chunks.append("## 工作区根目录文档\n" + "\n".join(f"- {d}" for d in docs))
        app_dirs = [p.name for p in paths.APPS_DIR.iterdir() if p.is_dir()] if paths.APPS_DIR.exists() else []
        if app_dirs:
            chunks.append("## apps/ 下的子系统\n" + ", ".join(app_dirs))
        tree = _tree(paths.ROOT, depth=2, limit=90)
        if tree:
            chunks.append("## 目录结构（两层，节选）\n" + "\n".join(tree))
    if kind in ("plugins", "all"):
        plugin_root = paths.APPS_DIR / "AstrBot" / "data" / "plugins"
    if not plugin_root.exists():
        plugin_root = paths.ROOT / "plugins"
        rows: list[str] = []
        if plugin_root.exists():
            for entry in sorted(plugin_root.iterdir()):
                if not entry.is_dir() or entry.name.startswith("."):
                    continue
                meta = entry / "metadata.yaml"
                desc = ""
                if meta.exists():
                    text = meta.read_text("utf-8", errors="replace")
                    m = re.search(r"^\s*desc(?:ription)?\s*:\s*(.+)$", text, re.M)
                    if m:
                        desc = m.group(1).strip().strip('"\'')
                main_py = entry / "main.py"
                hint = ""
                if main_py.exists():
                    src = main_py.read_text("utf-8", errors="replace")
                    tools = re.findall(r'@filter\.llm_tool\(\s*name\s*=\s*["\']([^"\']+)', src)
                    cmds = re.findall(r'@filter\.command\(\s*["\']([^"\']+)', src)
                    bits = []
                    if tools:
                        bits.append("LLM 工具 " + "/".join(tools))
                    if cmds:
                        bits.append("命令 " + "/".join("/" + c for c in cmds))
                    hint = "；".join(bits)
                rows.append(f"- {entry.name}：{desc or '（无描述）'}{('（' + hint + '）') if hint else ''}")
        if rows:
            chunks.append("## AstrBot 插件清单\n" + "\n".join(rows))
        cfg = paths.APPS_DIR / "AstrBot" / "data" / "plugins" / "video_gen" / "main.py"
        if cfg.exists():
            chunks.append("## 视频生成能力（apps/videogen）\n"
                          "支持四种模板：a3 大字冲击 / title 标题卡 / quote 竖排金句 / explain 讲解片；"
                          "341 条风格提示词；Edge TTS 与 GPT-SoVITS 配音；BGM 闪避；程序化音效；"
                          "成片 1080×1920 或 1920×1080，H.264 + AAC。")
    text = "\n\n".join(chunks).strip()
    return text[:8000]


# ---------------------------------------------------------------- 剧本生成

PLAN_SYSTEM = """你是一位擅长把复杂系统讲清楚的短视频编导。
你只输出 JSON，不输出任何解释文字。用中文写作。"""

PLAN_TEMPLATE = """请为下面这支讲解短视频写分镜脚本。

观众要求：{brief}

硬性要求：
- 一共 {scenes} 屏（不少于 {min_scenes} 屏，不多于 {max_scenes} 屏），总时长约 {duration} 秒。
- 第 1 屏是开场，最后一屏是收尾（总结一句 + 邀请继续）。
- 每一屏只讲一个知识点，标题不超过 14 个字，说明不超过 46 个字，念白 18–55 个字（念白要口语、能直接朗读，不要出现 Markdown、括号注释、数字列表）。
- 如果提供了资料，必须基于资料里的事实写，不许编造不存在的文件名/功能/数字。
- 讲清「是什么 → 怎么做 → 为什么这样做」的因果，而不是罗列名词。
- label 是这一屏左上角的小标签，2–8 个字，例如「① 入口层」「② 大脑」「关键取舍」「为什么要排队」。
{sticker_rule}
- 台词要朗读得动听：避免连续的长句和生僻缩写。
{style_block}

输出 JSON（严格按这个结构，不要加注释）：
{{"title": "整片标题（不超过 16 字）", "scenes": [{{"label": "...", "title": "...", "sub": "...", "say": "...", "sticker": "..."}}]}}

{context}"""


def _scene_count(duration: float) -> int:
    n = int(round(duration / SCENE_BUDGET))
    return max(3, min(60, n))


def _read_text_file(path: str | None) -> str:
    """命令行用：把一份文本文件整段当额外资料（utf-8，缺文件就忽略）。"""
    if not path:
        return ""
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def plan(
    brief: str,
    duration: float,
    context_kind: str = "none",
    temperature: float = 0.8,
    stickers_enabled: bool = True,
    style_hints: str = "",
    context_text: str = "",
) -> dict:
    """让模型写分镜。返回 {title, scenes:[{label,title,sub,say,sticker}]}。

    `context_text` 是调用方（DSH 面板）已经取好的额外资料——本次对话记录、
    自选文件——它们不在本进程能看到的范围里，所以直接以文本传进来，与
    `context_kind` 抓到的项目资料拼在一起。
    """
    brief = (brief or "").strip()
    if not brief:
        raise ValueError("brief 不能为空")
    context = gather_context(context_kind)
    extra = (context_text or "").strip()
    if extra:
        context = (context + "\n\n" + extra).strip() if context else extra
    scenes_wanted = _scene_count(duration)
    min_scenes = max(3, scenes_wanted - 2)
    max_scenes = min(60, scenes_wanted + 2)
    if stickers_enabled:
        sticker_rule = (
            "- sticker 从下面列表里挑一个最贴合这一屏情绪的名字；不确定就留空字符串。\n"
            f"  可选项：{', '.join(STICKER_CHOICES)}"
        )
    else:
        sticker_rule = '- sticker 一律输出空字符串 ""（这一支视频不要表情包）。'
    style_block = ""
    if style_hints:
        style_block = (
            "视觉参考（观众挑的风格提示词，只用它们的视觉气质，不要复刻文字）：\n"
            "<<<\n" + style_hints + "\n>>>\n"
            "分镜的文字和念白照旧按内容写，但 label/标题的用词和节奏要贴合上面这些风格的调性。"
        )
    ctx_block = ""
    if context:
        ctx_block = "资料（只能引用这里出现的事实）：\n<<<\n" + context + "\n>>>"
    prompt = PLAN_TEMPLATE.format(
        brief=brief,
        scenes=scenes_wanted,
        min_scenes=min_scenes,
        max_scenes=max_scenes,
        duration=int(duration),
        sticker_rule=sticker_rule,
        style_block=style_block,
        context=ctx_block,
    )
    messages = [
        {"role": "system", "content": PLAN_SYSTEM},
        {"role": "user", "content": prompt},
    ]
    raw = prompts._chat(messages, timeout=300, max_tokens=8192, temperature=temperature)
    data = _parse_plan(raw)
    data["scenes"] = _normalize_scenes(data.get("scenes") or [], stickers_enabled=stickers_enabled)
    if not data["scenes"]:
        raise ValueError("模型没有给出可用场景")
    return data


def _parse_plan(raw: str) -> dict:
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"模型返回的不是合法 JSON：{exc}") from exc


def _clean(value, limit: int) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    text = text.strip(" \"'`…")
    return text[:limit]


def _normalize_scenes(scenes: Iterable[dict], stickers_enabled: bool = True) -> list[dict]:
    known = {s["name"] for s in sticker_catalog()} if stickers_enabled else set()
    out: list[dict] = []
    for item in scenes:
        if not isinstance(item, dict):
            continue
        name = _clean(item.get("title") or item.get("heading"), 24)
        if not name:
            continue
        sticker = _clean(item.get("sticker"), 60) if stickers_enabled else ""
        if sticker and sticker not in known:
            sticker = ""
        out.append({
            "label": _clean(item.get("label"), 16),
            "title": name,
            "sub": _clean(item.get("sub") or item.get("desc") or item.get("detail"), 64),
            "say": _clean(item.get("say") or item.get("narration") or name, 140),
            "sticker": sticker,
        })
    return out


def style_hints(style_keys: Iterable[str] | None, limit: int = 4) -> str:
    """把选中的风格提示词压成一段给编导模型看的视觉参考。"""
    keys = [k for k in (style_keys or []) if isinstance(k, str) and k.strip()][:limit]
    if not keys:
        return ""
    try:
        data = prompts.catalog()
        items = data.get("styles") if isinstance(data, dict) else data
        index = {item.get("key"): item for item in (items or []) if isinstance(item, dict)}
    except Exception:  # 风格库读不出来不该挡住出片
        return ""
    blocks: list[str] = []
    seen: set[str] = set()
    for key in keys:
        if key in seen:
            continue
        seen.add(key)
        item = index.get(key)
        if not item:
            continue
        look = (item.get("look") or "").strip()
        prompt = item.get("prompt") or ""
        # 参考实现里的代码块对编导模型没用，只留一句画法气质
        prompt = re.sub(r"```.*?(?:```|$)", " ", prompt, flags=re.S)
        cut = re.search(r"(下面是我们自己写的参考实现|公共的小工具函数|参考实现)", prompt)
        if cut:
            prompt = prompt[:cut.start()]
        prompt = prompt.replace("用代码（Canvas 2D）画一段", "")
        prompt = re.sub(r"\s+", " ", prompt).strip()
        line = f"- {item.get('code', '')} {item.get('name', key)}"
        if look:
            line += f"：{look}"
        if prompt:
            line += f"（画法气质：{prompt[:150]}）"
        blocks.append(line)
    return "\n".join(blocks)


def sticker_catalog() -> list[dict]:
    out: list[dict] = []
    if not STICKER_DIR.exists():
        return out
    for name in STICKER_CHOICES:
        path = STICKER_DIR / f"{name}.gif"
        if path.exists():
            out.append({"name": name, "path": str(path)})
    return out


def _sticker_path(name: str) -> str:
    if not name:
        return ""
    path = STICKER_DIR / f"{name}.gif"
    return str(path) if path.exists() else ""


# ------------------------------------------------- 风格画面（选了风格提示词时）

SCENE_PAGE_DIR = paths.WORK_DIR / "prompt_pages" / "auto_scenes"

SCENE_PAGE_RULES = """你现在给一部讲解短片画**其中一屏**的画面。

硬性要求：
1. 只输出一个完整的 HTML 文件（<!DOCTYPE html> 开头、</html> 结尾），不要解释、不要 Markdown 代码围栏。
2. 页面里必须有一个 id="cv" 的 canvas，尺寸正好 {width}x{height}，铺满窗口，不留滚动条。
3. 必须定义 `window.DUR = {duration:.3f}` 和纯函数 `window.seek(t)`；同一 t 必须画出同一帧（禁止 Math.random、禁止未播种的随机、禁止 Date.now 参与画面）。
4. `t=0` 时画面就要有主体（不要淡入到 1 秒还是一片黑）。
5. 只允许原生 canvas 2D 绘制，不要外部图片/字体/库/网络请求。
6. 文字用中文字体栈（"Microsoft YaHei", "PingFang SC", sans-serif），字号要大、对比要够，手机竖着看也清楚。
7. **这一点最重要——时长就是 {duration:.3f} 秒，整段旁白会一直念到第 {duration:.3f} 秒**：把这一屏排成 3–5 个节拍，按 `p = t / window.DUR` 依次触发，节拍大致落在 p=0.15 / 0.4 / 0.65 / 0.9 上（例如：0.15 标题砸入 → 0.4 关键词变色 → 0.65 图形/线条长出来 → 0.9 收束到最终构图）。**不允许前 30% 就把画面画完然后干等**：至少在 p=0.3、0.6、0.9 三个时刻，画面都要有肉眼可见的新变化。
8. **绝对不要循环**：不要写 `t % xxx`、不要在末帧跳回开头、不要做「播完再来一遍」的循环动画。动效应该在第 {duration:.3f} 秒附近刚好收在最终构图上，之后保持静止（可以让极轻微的呼吸/闪烁延续，但主体结构不许重复出现第二次）。
9. 不要自己加字幕条/进度条/大段旁白文字，旁白由后期配音，画面上只放标题/关键词/图形。
10. **每一屏都必须有画面，不能只排几行字**：除了标题/关键词，至少要有 1 个用 canvas 画出来的具象元素（人物/道具/示意图/图表/色块构成的场景），占画面 30% 以上面积；只有文字加纯色背景的这一屏视为不合格。

内容：把下面的标题/说明/念白画出来，文字可以直接上屏，但更推荐用图形把这句话讲明白。
- 屏序号：第 {index}/{total} 屏
- 小标签：{label}
- 标题：{title}
- 说明：{sub}
- 念白：{say}

视觉风格（同一位导演的同一套视觉系统，请照它来，别自己另发明一套）：
{style_block}
"""

def _timebox_page(html: str, span: float) -> str:
    """在页面末尾注入时间盒脚本（实现见 prompts.timebox_page，保证动效铺满一屏且不循环）。"""
    from . import prompts as prompts_mod

    return prompts_mod.timebox_page(html, span)



def _scene_style_block(style: dict) -> str:
    body = (style.get("prompt") or "").strip()
    # 提示词正文里常带参考实现代码，写分镜页用不上，砍掉省 token
    for marker in ("下面是我们自己写的参考实现", "公共的小工具函数", "参考实现", "```"):
        if marker in body:
            body = body.split(marker)[0]
    body = body.strip()[:1600]
    return (
        f"- 风格名：{style.get('code', '')} {style.get('name', '')}\n"
        f"- 长什么样：{style.get('look', '') or '（同下）'}\n"
        f"- 画法：\n{body}"
    )


def _scene_key(style_key: str, scene: dict, span: float, width: int, height: int) -> str:
    raw = "|".join([
        style_key,
        scene.get("label", ""),
        scene.get("title", ""),
        scene.get("sub", ""),
        scene.get("say", ""),
        f"{span:.3f}",
        f"{width}x{height}",
        "v2",
    ])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def render_style_film(
    scenes: list[dict],
    style_keys: list[str],
    spans: list[float],
    width: int,
    height: int,
    fps: int,
    work: Path,
    progress: Progress = None,
    timeout: int = 2400,
) -> Path:
    """按选中的风格提示词，逐屏让 AI 画页面 → 逐屏渲成片段 → 拼成一条无声长片。"""
    from . import prompts as prompts_mod  # 延迟导入，避免没装 LLM 配置时影响其它路径

    picked: list[dict] = []
    for key in style_keys:
        style = prompts_mod.find_style(key)
        if style:
            picked.append(style)
    if not picked:
        raise RuntimeError("选的风格提示词都找不到，没法按风格出画面")

    SCENE_PAGE_DIR.mkdir(parents=True, exist_ok=True)
    segments: list[Path] = []
    total = len(scenes)
    for i, scene in enumerate(scenes):
        style = picked[i % len(picked)]
        span = max(1.2, float(spans[i]))
        key = _scene_key(style["key"], scene, span, width, height)
        html_path = SCENE_PAGE_DIR / f"{key}.html"
        mp4_path = SCENE_PAGE_DIR / f"{key}-{fps}fps.mp4"
        base = 0.42 + 0.50 * (i / total)

        if not mp4_path.exists():
            if not html_path.exists():
                _report(progress, f"画第 {i + 1}/{total} 屏（{style.get('name', '')}）…", base)
                prompt = SCENE_PAGE_RULES.format(
                    width=width,
                    height=height,
                    duration=span,
                    index=i + 1,
                    total=total,
                    label=scene.get("label", ""),
                    title=scene.get("title", ""),
                    sub=scene.get("sub", ""),
                    say=scene.get("say", ""),
                    style_block=_scene_style_block(style),
                )
                html = ""
                for attempt in range(2):
                    raw = prompts_mod._chat(
                        [
                            {"role": "system", "content": "你是严谨的前端动画工程师，只输出可运行的单文件 HTML。"},
                            {"role": "user", "content": prompt + ("" if attempt == 0 else "\n注意：上一版不合格，必须包含 id=\"cv\" 的 canvas、window.DUR 和 window.seek(t)。")},
                        ],
                        temperature=0.7,
                    )
                    html = prompts_mod._extract_html(raw)
                    if "window.seek" in html and "window.DUR" in html and "canvas" in html:
                        break
                else:
                    raise RuntimeError(f"第 {i + 1} 屏的页面没写出 window.seek/window.DUR")
                html_path.write_text(html, "utf-8")

            boxed_path = SCENE_PAGE_DIR / f"{key}-boxed.html"
            raw_html = html_path.read_text("utf-8")
            boxed_path.write_text(_timebox_page(raw_html, span), "utf-8")

            _report(progress, f"渲染第 {i + 1}/{total} 屏…", base + 0.01)

            def _frame_cb(done, all_):
                _report(
                    progress,
                    f"渲染第 {i + 1}/{total} 屏 {done}/{all_}",
                    base + 0.01 + 0.49 * (done / max(1, all_)) / total,
                )

            try:
                # LLM 页面必须自带 #cv canvas：直接读 canvas 比截浏览器画面快约 8 倍（108ms/帧 vs 800ms/帧），
                # 而且绕开 Page.captureScreenshot 在重绘页面上的卡死路径。
                prompts_mod.render_external(
                    boxed_path,
                    mp4_path,
                    width,
                    height,
                    fps,
                    span,
                    progress=_frame_cb,
                    timeout=min(timeout, 420),
                    mode="canvas",
                    fmt="jpeg",
                )
            except Exception as canvas_exc:  # noqa: BLE001
                mp4_path.unlink(missing_ok=True)
                try:
                    prompts_mod.render_external(
                        boxed_path,
                        mp4_path,
                        width,
                        height,
                        fps,
                        span,
                        progress=_frame_cb,
                        timeout=min(timeout, 420),
                    )
                except Exception as exc:  # noqa: BLE001
                    # 这一屏的页面渲不出来（主线程卡死/超时）：把页面和半成品清掉，别让下次继续踩同一个坑
                    for stale in (mp4_path, boxed_path, html_path):
                        try:
                            stale.unlink(missing_ok=True)
                        except Exception:  # noqa: BLE001
                            pass
                    raise RuntimeError(
                        f"第 {i + 1}/{total} 屏渲染失败：{exc}（canvas 模式也失败：{canvas_exc}）"
                    ) from exc
        segments.append(mp4_path)

    _report(progress, "拼接分屏…", 0.93)
    list_file = work / "concat.txt"
    list_file.write_text(
        "".join("file '" + str(p).replace("'", "'\\''") + "'\n" for p in segments),
        "utf-8",
    )
    joined = work / "film_video.mp4"
    cmd = [
        str(paths.FFMPEG),
        "-y",
        "-v",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(joined),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0 or not joined.exists() or joined.stat().st_size < 1024:
        raise RuntimeError("拼接分屏失败：" + (proc.stderr or "")[-600:])
    return joined


# ---------------------------------------------------------------- 成片

@dataclass
class AutoResult:
    out: Path
    title: str
    scenes: list[dict]
    duration: float


def build(
    brief: str,
    duration: float = 90.0,
    width: int = 1080,
    height: int = 1920,
    fps: int = 24,
    voice: str = "zh-CN-XiaoxiaoNeural",
    rate: str = "+6%",
    context_kind: str = "none",
    bgm: str | None = None,
    bgm_volume: float = 0.13,
    sfx_volume: float = 0.14,
    narration: bool = True,
    extra: str = "",
    stickers_enabled: bool = True,
    style_keys: list[str] | None = None,
    context_text: str = "",
    out_path: str | Path | None = None,
    keep_work: bool = False,
    timeout: int = 3600,
    progress: Progress = None,
    plan_only: bool = False,
) -> dict:
    """一句话 → 分镜 → 配音 → 成片。返回 pipeline.build 的结果外加 title/scenes。"""
    spec_info = STYLES["explain"]
    _report(progress, "让 AI 写分镜…", 0.02)
    if extra:
        brief = f"{brief}\n补充要求：{extra}"
    data = plan(
        brief,
        duration,
        context_kind=context_kind,
        stickers_enabled=stickers_enabled,
        style_hints=style_hints(style_keys),
        context_text=context_text,
    )
    scenes = data["scenes"]
    title = _clean(data.get("title"), 40) or _clean(scenes[0]["title"], 40)

    work = Path(tempfile.mkdtemp(prefix="vg_auto_"))
    try:
        lines: list[Line] = []
        clips: list[dict] = []
        spans: list[float] = []
        cursor = 0.0
        if narration:
            for i, scene in enumerate(scenes):
                _report(progress, f"配音 {i + 1}/{len(scenes)}…", 0.04 + 0.36 * (i + 1) / len(scenes))
                mp3 = work / f"s{i:03d}.mp3"
                wav = work / f"s{i:03d}.wav"
                audio.tts_line(scene["say"], mp3, voice=voice, rate=rate, pitch="-1Hz")
                clip = audio.prep_clip(mp3, wav)
                span = max(4.2, clip + 1.05)
                spans.append(round(span, 3))
                clips.append({"i": i, "text": scene["say"], "at": round(cursor, 3), "dur": clip, "slot": round(span, 3), "wav": wav})
                cursor += span
                lines.append(Line(
                    text=scene["title"],
                    sub=scene["sub"],
                    label=scene["label"],
                    sticker=_sticker_path(scene["sticker"]),
                    dur=round(span, 3),
                    say=scene["say"],
                ))
        else:
            each = max(4.0, duration / max(1, len(scenes)))
            for scene in scenes:
                spans.append(round(each, 3))
                lines.append(Line(
                    text=scene["title"],
                    sub=scene["sub"],
                    label=scene["label"],
                    sticker=_sticker_path(scene["sticker"]),
                    dur=round(each, 3),
                    say=scene["say"],
                ))
        spec = Spec(
            style="explain",
            width=width,
            height=height,
            fps=fps,
            lines=lines,
            title=title,
            voice_enabled=bool(narration),
            voice=voice,
            rate=rate,
            bgm_enabled=bgm is not None,
            bgm=bgm or "",
            bgm_volume=bgm_volume,
            sfx_enabled=sfx_volume > 0,
            sfx_volume=max(0.0, min(0.5, sfx_volume)),
            footer="AI 生成 · Deepseek-Chat",
        )
        resolve(spec)
        _report(progress, f"分镜 {len(lines)} 屏 · 约 {spec.duration:.0f}s", 0.42)

        if plan_only:
            plan_out = paths.DATA_DIR / "_auto" / f"plan-{time.strftime('%Y%m%d-%H%M%S')}.json"
            plan_out.parent.mkdir(parents=True, exist_ok=True)
            plan_out.write_text(json.dumps({"title": title, "scenes": scenes, "spec": spec.to_dict()},
                                           ensure_ascii=False, indent=2), "utf-8")
            return {"plan": str(plan_out), "title": title, "scenes": scenes, "duration": spec.duration}

        out = Path(out_path) if out_path else _default_out(title)
        out.parent.mkdir(parents=True, exist_ok=True)

        # 选了风格提示词：画面改成「逐屏按风格画页面」，不再用 explain 模板
        picked_keys = [k for k in (style_keys or []) if k]
        style_failure = ""
        if picked_keys:
            _report(progress, "按风格提示词画画面…", 0.42)
            started_style = time.time()
            try:
                video = render_style_film(
                    scenes,
                    picked_keys,
                    spans,
                    width,
                    height,
                    fps,
                    work,
                    progress=progress,
                    timeout=timeout,
                )
            except Exception as exc:  # noqa: BLE001
                # 模型写崩 / 渲染超时都不该让整条任务失败：退回讲解片模板，把原因写进 warnings
                style_failure = f"{type(exc).__name__}: {exc}"[:300]
                _report(progress, "风格画面失败，改用讲解片模板…", 0.42)
            else:
                spec.duration = round(sum(spans), 3)
                warnings: list[str] = []
                if clips or spec.bgm_enabled or spec.sfx_enabled:
                    _report(progress, "混音…", 0.94)
                    audio.mix(video, clips, out, spec, progress=lambda s: _report(progress, "混音", 0.94))
                else:
                    import shutil

                    shutil.copyfile(video, out)
                streams = audio.probe_streams(out)
                vstream = (streams.get("streams") or [{}])[0] if streams.get("streams") else {}
                has_audio = any(s.get("codec_type") == "audio" for s in streams.get("streams") or [])
                if not vstream.get("width"):
                    raise RuntimeError("产物里没有视频流")
                result = {
                    "out": str(out),
                    "duration": float((streams.get("format") or {}).get("duration") or spec.duration),
                    "size": out.stat().st_size,
                    "width": int(vstream.get("width") or width),
                    "height": int(vstream.get("height") or height),
                    "fps": fps,
                    "has_audio": has_audio,
                    "duration_sec": round(time.time() - started_style, 2),
                    "warnings": warnings,
                    "style": f"style:{','.join(picked_keys[:2])}",
                    "title": title,
                }
                result["scenes"] = scenes
                return result

        result = pipeline.build(
            spec,
            out_path=out,
            progress=lambda stage, pct: _report(progress, stage, 0.42 + pct * 0.58),
            keep_work=keep_work,
            timeout=timeout,
        )
        result["title"] = title
        result["scenes"] = scenes
        if style_failure:
            result.setdefault("warnings", []).append("风格画面失败，已改用讲解片模板：" + style_failure)
        return result
    finally:
        if not keep_work:
            for p in work.glob("*"):
                try:
                    p.unlink()
                except OSError:
                    pass
            try:
                work.rmdir()
            except OSError:
                pass


def _default_out(title: str) -> Path:
    day = time.strftime("%Y%m%d")
    stamp = time.strftime("%H%M%S")
    slug = re.sub(r"[^\w\u4e00-\u9fff]+", "-", title)[:24].strip("-") or "auto"
    return paths.DATA_DIR / day / f"auto-{slug}-{stamp}-{random.randint(1000, 9999)}.mp4"


# ---------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(prog="videogen.auto_film", description="一句话 → AI 自拟分镜 → 讲解片")
    ap.add_argument("--brief", required=True, help="观众要求，比如「总结这个工作区的工作流程」")
    ap.add_argument("--duration", type=float, default=90.0, help="目标时长（秒），15–360")
    ap.add_argument("--context", default="none", choices=["none", "workspace", "plugins", "all"],
                    help="给 AI 参考的资料范围")
    ap.add_argument("--context-file", default="",
                    help="额外资料文本文件（如会话记录、自选文档），整段拼进提示词")
    ap.add_argument("--orientation", default="portrait", choices=["portrait", "landscape", "square"])
    ap.add_argument("--quality", type=int, default=1080, choices=[720, 1080])
    ap.add_argument("--voice", default="zh-CN-XiaoxiaoNeural")
    ap.add_argument("--rate", default="+6%")
    ap.add_argument("--no-narration", action="store_true")
    ap.add_argument("--no-bgm", action="store_true")
    ap.add_argument("--sfx-volume", type=float, default=0.14)
    ap.add_argument("--bgm-volume", type=float, default=0.13)
    ap.add_argument("--extra", default="")
    ap.add_argument("--no-stickers", action="store_true", help="不要表情包")
    ap.add_argument("--styles", default="", help="视觉参考的风格提示词 key，多个用 , 分隔（可留空让 AI 决定）")
    ap.add_argument("--plan-only", action="store_true", help="只写分镜 JSON，不出片")
    ap.add_argument("--out", default="")
    ap.add_argument("--keep-work", action="store_true")
    args = ap.parse_args(argv)

    w, h = _size(args.orientation, args.quality)
    res = build(
        args.brief,
        duration=max(8.0, min(360.0, args.duration)),
        width=w,
        height=h,
        voice=args.voice,
        rate=args.rate,
        context_kind=args.context,
        context_text=_read_text_file(args.context_file),
        bgm=None if args.no_bgm or not str(paths.DEFAULT_BGM) else str(paths.DEFAULT_BGM),
        bgm_volume=args.bgm_volume,
        sfx_volume=args.sfx_volume,
        narration=not args.no_narration,
        extra=args.extra,
        stickers_enabled=not args.no_stickers,
        style_keys=[x.strip() for x in args.styles.split(",") if x.strip()],
        out_path=args.out or None,
        keep_work=args.keep_work,
        plan_only=args.plan_only,
        progress=lambda stage, pct: print(f"  [{pct * 100:5.1f}%] {stage}", flush=True),
    )
    if args.plan_only:
        print(f"\n  分镜   {res['plan']}\n  标题   {res['title']}   {len(res['scenes'])} 屏 / {res['duration']:.1f}s")
        for i, sc in enumerate(res["scenes"], 1):
            print(f"   {i:02d}. [{sc['label']}] {sc['title']} — {sc['say']}")
        return 0
    print(f"\n  成片   {res['out']}")
    print(f"  规格   {res['width']}x{res['height']} @{res['fps']}fps  {res['duration']:.3f}s  "
          f"{res['size'] / 1024:.0f} KB  {'有声' if res['has_audio'] else '无声'}")
    if "render_sec" in res:
        print(f"  耗时   {res['render_sec'] + res['tts_sec'] + res['mix_sec']:.1f}s"
              f"（渲染 {res['render_sec']:.1f}s / 配音 {res['tts_sec']:.1f}s / 混音 {res['mix_sec']:.1f}s）")
    else:
        print(f"  耗时   {res.get('duration_sec', 0):.1f}s（含逐屏写页面 + 渲帧 + 混音）")
    return 0


def _size(orientation: str, quality: int) -> tuple[int, int]:
    long_edge = 1920 if quality >= 1080 else 1280
    short_edge = 1080 if quality >= 1080 else 720
    if orientation == "landscape":
        return long_edge, short_edge
    if orientation == "square":
        return short_edge, short_edge
    return short_edge, long_edge


if __name__ == "__main__":
    raise SystemExit(main())
