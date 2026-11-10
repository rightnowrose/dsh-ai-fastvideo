"""样式注册表 + 时间轴规则。

加一个新样式只要两步：
  1. 在 templates/<key>/index.html 写一个读 `SPEC` 的页面（draw(t) 纯函数）；
  2. 在 STYLES 里登记，并给它一条 at/duration 规则。
"""

from __future__ import annotations

from dataclasses import dataclass

from .spec import Spec


@dataclass(frozen=True)
class StyleInfo:
    key: str
    label: str
    template: str
    blurb: str
    default_beat: float
    # 一条台词都没给 at 时，第 i 句落在哪一秒
    first_at: float = 0.0
    gap: float = 0.55
    # 最后一句之后留多久画面（尾巴）
    tail: float = 1.4
    min_duration: float = 2.0
    max_lines: int = 12


STYLES: dict[str, StyleInfo] = {
    "a3": StyleInfo(
        key="a3",
        label="大字冲击",
        template="a3/index.html",
        blurb="词一个一个砸进画面，旧的变灰，最新的是白的/红的；适合怼人、喊话、情绪爆点。",
        default_beat=0.55,
        first_at=0.0,
        gap=0.0,          # 由 beat 决定
        tail=1.4,
        min_duration=2.0,
        max_lines=12,
    ),
    "title": StyleInfo(
        key="title",
        label="标题卡",
        template="title/index.html",
        blurb="大标题弹性入场 + 副标题 + 下划线动效；适合片头、观点卡、通知。",
        default_beat=0.5,
        first_at=0.7,
        gap=0.6,
        tail=1.3,
        min_duration=3.2,
        max_lines=4,
    ),
    "quote": StyleInfo(
        key="quote",
        label="竖排金句",
        template="quote/index.html",
        blurb="竖排中文、逐字淡入、右下角红章；适合金句、诗词、宣言。",
        default_beat=0.42,
        first_at=0.9,
        gap=0.62,
        tail=1.6,
        min_duration=3.6,
        max_lines=4,
    ),
    "explain": StyleInfo(
        key="explain",
        label="讲解片",
        template="explain/index.html",
        blurb="一屏一个知识点：小标签 + 大标题 + 说明 + 表情包，底部进度条；适合功能导览、教程。",
        default_beat=5.0,
        first_at=0.0,
        gap=5.0,
        tail=1.2,
        min_duration=4.5,
        max_lines=24,
    ),
}


def resolve(spec: Spec) -> Spec:
    """补齐时间轴/时长/默认值，返回同一个对象（原地改）。"""
    info = STYLES[spec.style]

    if not spec.beat or spec.beat <= 0:
        spec.beat = info.default_beat

    # --- 讲解片：一屏一个场景，时间轴由每屏自己的 dur 决定 ---
    if spec.style == "explain":
        cursor = info.first_at
        for i, line in enumerate(spec.lines):
            line.at = round(float(line.at), 3) if line.at is not None else round(cursor, 3)
            span = float(line.dur or info.gap)
            if i == len(spec.lines) - 1:
                span = max(span, info.min_duration)
            line.dur = round(span, 3)
            cursor = max(cursor, line.at + line.dur)
        spec.duration = round(cursor + info.tail, 3)
        return spec

    # --- 每句的入场时间 ---
    cursor = info.first_at
    for i, line in enumerate(spec.lines):
        if line.at is None:
            if spec.style == "a3":
                line.at = round(i * spec.beat, 3)
            else:
                line.at = round(info.first_at + i * info.gap, 3)
        else:
            line.at = round(float(line.at), 3)
        cursor = max(cursor, line.at)

    # --- 总时长 ---
    if spec.style == "a3":
        dur = len(spec.lines) * spec.beat + info.tail
    else:
        dur = max(cursor + info.tail, info.min_duration)
    spec.duration = round(float(dur), 3)
    return spec


def describe() -> list[dict]:
    return [
        {
            "key": s.key,
            "label": s.label,
            "blurb": s.blurb,
            "default_beat": s.default_beat,
            "max_lines": s.max_lines,
        }
        for s in STYLES.values()
    ]
