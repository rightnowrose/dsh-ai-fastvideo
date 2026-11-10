"""视频描述（spec）—— 这是整个功能的唯一输入格式。

一份 spec 描述「一条片子」：

    {
      "style": "a3",                 # a3 大字冲击 / title 标题卡 / quote 竖排金句
      "width": 1080, "height": 1920,  # 竖屏；横屏写 1920/1080
      "fps": 24,
      "beat": 0.55,                   # 每个词的节拍（秒），同时决定配音摆位
      "title": "你懂什么",            # title/quote 用
      "subtitle": "为你好的版本",      # 可选
      "lines": [                      # 台词，at 不填就按节拍自动排
        {"text": "你"},
        {"text": "懂什么"},
        {"text": "听话", "at": 2.2}
      ],
      "voice": {"enabled": true, "name": "zh-CN-YunxiNeural", "rate": "+10%"},
      "bgm": {"enabled": true, "volume": 0.16},
      "out": ""                       # 不填则自动落在 data/video_out/日期/
    }

`resolve()` 会把「没填 at 的台词」按风格规则算出时间轴和总时长——时间轴是
Python 算的，页面只负责按 window.DUR 把画面画出来，这样音画才不会各排各的。
"""

from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .paths import DEFAULT_BGM


@dataclass
class Line:
    text: str
    at: float | None = None          # 秒；None = 自动排
    color: str = ""                  # "" 自动 / "red" / "white" / 任意 #hex（讲解片里当强调色）
    # --- 讲解片（explain）用的扩展字段，其他样式忽略 ---
    sub: str = ""                     # 副标题/说明
    label: str = ""                   # 左上角小标签，如「② 人格层」
    sticker: str = ""                 # 表情包 GIF 绝对路径
    kind: str = ""                    # "" / "chain"（画流程链）
    dur: float = 0.0                  # 本场景时长（0 = 用样式默认）
    say: str = ""                     # 这一屏实际配的音（不填就念 text）
    sfx: str = ""                      # 本行音效覆盖："hit" / "hit:0.35" / "none"
    frames: list[str] = field(default_factory=list)  # 运行时注入的 data URI，不进配置

    def to_page(self) -> dict[str, Any]:
        d: dict[str, Any] = {"text": self.text, "at": float(self.at or 0.0)}
        if self.color:
            d["color"] = self.color
        for key in ("sub", "label", "kind"):
            value = getattr(self, key)
            if value:
                d[key] = value
        if self.dur:
            d["dur"] = float(self.dur)
        if self.frames:
            d["frames"] = self.frames
        return d


@dataclass
class Spec:
    style: str = "a3"
    width: int = 1080
    height: int = 1920
    fps: int = 24
    beat: float = 0.55
    seed: int = 0
    title: str = ""
    subtitle: str = ""
    footer: str = "AI 生成 · Deepseek-Chat"
    lines: list[Line] = field(default_factory=list)
    # 声音
    voice_enabled: bool = True
    voice: str = "zh-CN-YunxiNeural"
    rate: str = "+10%"
    pitch: str = "-2Hz"
    bgm_enabled: bool = True
    bgm: str = str(DEFAULT_BGM) if str(DEFAULT_BGM) else ""
    bgm_volume: float = 0.16
    bgm_start: float = 0.0
    target_lufs: float = -14.0
    # 音效（代码合成，见 sfx.py）；gain 分轨靠 sfx_volume，事件增益在 events 里给 dB
    sfx_enabled: bool = True
    sfx_volume: float = 0.14
    sfx_events: list[dict[str, Any]] = field(default_factory=list)  # 留空 = 按样式自动推
    # 视觉
    accent: str = "#ff4a32"
    bg: str = "#08080a"
    # 产物
    duration: float = 0.0            # 由 resolve() 计算
    out: str = ""

    # ---------------------------------------------------------------- 构造/读取
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Spec":
        data = dict(data or {})
        raw_lines = data.pop("lines", []) or []
        voice = data.pop("voice", None)
        bgm = data.pop("bgm", None)
        sfx = data.pop("sfx", None)
        lines: list[Line] = []
        for item in raw_lines:
            if isinstance(item, str):
                lines.append(Line(item.strip()))
            elif isinstance(item, dict):
                lines.append(
                    Line(
                        str(item.get("text") or "").strip(),
                        float(item["at"]) if item.get("at") is not None else None,
                        str(item.get("color") or ""),
                        sub=str(item.get("sub") or ""),
                        label=str(item.get("label") or ""),
                        sticker=str(item.get("sticker") or ""),
                        kind=str(item.get("kind") or ""),
                        dur=float(item.get("dur") or 0.0),
                        say=str(item.get("say") or ""),
                        sfx=str(item.get("sfx") or ""),
                    )
                )
        spec = cls(**_known(cls, data))
        spec.lines = [ln for ln in lines if ln.text]
        if isinstance(voice, dict):
            spec.voice_enabled = bool(voice.get("enabled", spec.voice_enabled))
            spec.voice = str(voice.get("name") or spec.voice)
            spec.rate = str(voice.get("rate") or spec.rate)
            spec.pitch = str(voice.get("pitch") or spec.pitch)
        if isinstance(bgm, dict):
            spec.bgm_enabled = bool(bgm.get("enabled", spec.bgm_enabled))
            spec.bgm = str(bgm.get("path") or spec.bgm)
            spec.bgm_volume = float(bgm.get("volume", spec.bgm_volume))
            spec.bgm_start = float(bgm.get("start", spec.bgm_start))
        if isinstance(sfx, dict):
            spec.sfx_enabled = bool(sfx.get("enabled", spec.sfx_enabled))
            spec.sfx_volume = float(sfx.get("volume", spec.sfx_volume))
            spec.sfx_events = list(sfx.get("events") or [])
        return spec

    @classmethod
    def from_json_file(cls, path: str | Path) -> "Spec":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8-sig")))

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        lines = []
        for ln in self.lines:
            item = asdict(ln)
            item.pop("frames", None)  # data URI 只存在于页面，不进任务库
            lines.append(item)
        d["lines"] = lines
        return d

    # ------------------------------------------------------------------ 页面数据
    def page_spec(self) -> dict[str, Any]:
        """交给 HTML 模板的最小集合（别把配音参数塞进页面）。"""
        return {
            "style": self.style,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "beat": self.beat,
            "duration": self.duration,
            "seed": self.seed,
            "title": self.title,
            "subtitle": self.subtitle,
            "footer": self.footer,
            "accent": self.accent,
            "bg": self.bg,
            "lines": [ln.to_page() for ln in self.lines],
        }

    # ------------------------------------------------------------------- 校验
    def validate(self) -> None:
        from .styles import STYLES

        if self.style not in STYLES:
            raise ValueError(f"未知样式 {self.style!r}，可选：{', '.join(STYLES)}")
        if not (240 <= self.width <= 3840 and 240 <= self.height <= 3840):
            raise ValueError("width/height 超出范围")
        if not (0.05 <= self.beat <= 5.0):
            raise ValueError("beat 超出范围")
        if self.style == "a3":
            if not self.lines:
                raise ValueError("a3 需要至少一句台词")
            if len(self.lines) > 12:
                raise ValueError("a3 最多 12 句，太多了画面会糊")
        elif self.style == "explain":
            if not self.lines:
                raise ValueError("explain 需要至少一个场景（lines）")
        elif not (self.title or self.lines):
            raise ValueError(f"{self.style} 需要 title 或 lines")


def _known(cls: Any, data: dict[str, Any]) -> dict[str, Any]:
    """丢掉落在本 dataclass 之外的键，方便配置里留注释/扩展字段。"""
    names = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
    payload = {k: v for k, v in data.items() if k in names}
    return payload


def split_words(text: str, max_len: int = 3, limit: int = 8) -> list[str]:
    """把用户随便说的一句话切成「大字冲击」能用的词块。

    先按标点/空白切，太长的再按 max_len 切；最多 limit 块。
    """
    import re

    text = (text or "").strip()
    if not text:
        return []
    chunks = [c for c in re.split(r"[|,，、。！？!?；;：:\s]+", text) if c]
    out: list[str] = []
    for c in chunks:
        c = c.strip()
        while len(c) > max_len:
            out.append(c[:max_len])
            c = c[max_len:]
        if c:
            out.append(c)
    return out[:limit]


def auto_seed(text: str) -> int:
    return random.Random(text).randint(1, 2**31 - 1)
