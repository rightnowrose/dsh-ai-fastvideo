"""生成「工作区导览」讲解片的 spec：先逐句念一遍量时长，再算每屏时长。

用法（在 apps 目录下）：
    python -m videogen.examples.workflow_film            # 只写 spec.json
    python -m videogen.examples.workflow_film --build    # 写完顺手出片
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from videogen import audio, pipeline
from videogen.paths import DATA_DIR
from videogen.spec import Spec
from videogen.styles import resolve

STICKERS = Path(r"F:\Deepseek-Chat\data\stickers\maid")
VOICE = "zh-CN-XiaoxiaoNeural"
RATE = "+6%"
PITCH = "-1Hz"
OUT_JSON = Path(r"F:\Deepseek-Chat\data\video_out\_p\explain.json")

# (label, 大标题, 说明, 表情包, 这一屏念什么)
SCENES: list[tuple[str, str, str, str, str]] = [
    (
        "开篇",
        "一个工作区\n一整套机器人",
        "从一条 QQ 消息，到图片、语音、视频",
        "gpt_TT02_比心.gif",
        "这是一套跑在本机的 QQ 机器人工作区。你给我一句话，它能回语音、能画图、能看视频，还能自己剪片子。",
    ),
    (
        "① 入口层",
        "消息从哪进来",
        "QQ > NTQQ > SnowLuma > AstrBot",
        "dfy_6N02_猫在么.gif",
        "一条消息先进 QQ，由本机的 NTQQ 收到；SnowLuma 把它转成标准协议，再交给 AstrBot 处理。",
    ),
    (
        "② 人格层",
        "谁来回这句话",
        "五个人格，各有语气和记忆",
        "dfy_N11_诶嘿.gif",
        "AstrBot 里住着五个人格：绯椿、小盈、容书盈。按场景切换，各有各的语气和记性。",
    ),
    (
        "③ 大脑",
        "DeepSeek 负责想",
        "回话、拆任务、决定调哪个工具",
        "dfy_2N09_思考.gif",
        "真正动脑子的是 DeepSeek。它不只要回话，还要决定这一句该调哪个工具。",
    ),
    (
        "④ 能力 · 说话",
        "回你一段语音",
        "Edge TTS 一秒出声，SoVITS 给专属音色",
        "dfy_EX20_主唱.gif",
        "想听声音，走 Edge TTS，一秒就出声；要专属音色，就上 GPT-SoVITS，明前奶绿那种。",
    ),
    (
        "④ 能力 · 画画",
        "现场画一张",
        "本地 ComfyUI、通义万相、免费接口三档",
        "dfy_EX56_自动画画.gif",
        "生图有三个后端：本地显卡跑 ComfyUI，云端走通义万相，兜底用免费接口。",
    ),
    (
        "④ 能力 · 表情",
        "还会甩表情包",
        "三百多张猫娘表情，按情绪挑",
        "dfy_N06_略略略.gif",
        "还有三百多张表情包，按情绪挑一张甩过来，比干巴巴说话有意思。",
    ),
    (
        "⑤ 看视频",
        "B 站视频，能看懂",
        "官方摘要和字幕，取不到再用本地模型转写",
        "dfy_11N02_WATCHING-YOU.gif",
        "B 站链接丢进来，先取官方摘要和字幕；取不到，再用本地模型转写，三级降级。",
    ),
    (
        "⑥ 做视频",
        "一句话，十几秒后出片",
        "竖屏 1080×1920，带配音和 BGM",
        "dfy_EX55_自动工作.gif",
        "今天新加的能力：说一句话，十几秒后收到一条竖屏短片，有配音、有字幕节奏。",
    ),
    (
        "内部 · 怎么这么快",
        "画面其实是网页",
        "Edge 逐帧截图，一帧几十毫秒",
        "dfy_9N07_蹦.gif",
        "画面不是模型生成的，是网页画出来的。浏览器逐帧截屏，一帧几十毫秒。",
    ),
    (
        "内部 · 不丢单",
        "所有任务都落库",
        "排队跑，重启之后接着跑",
        "dfy_10N12_好多猫.gif",
        "每条任务写进本地数据库，队列一次跑一个，进程重启也不丢单。",
    ),
    (
        "收尾",
        "一个工作区\n四个零件",
        "入口 · 人格 · 能力 · 本地算力",
        "dfy_16N01_超开心.gif",
        "说白了就四件事：消息入口、人格设定、能力工具、本地算力。剩下的，就是慢慢加。",
    ),
]


def measure(say: str, tmp: Path) -> float:
    mp3 = tmp / "t.mp3"
    wav = tmp / "t.wav"
    audio.tts_line(say, mp3, VOICE, RATE, PITCH)
    return audio.prep_clip(mp3, wav)


def build_spec(verbose: bool = True) -> Spec:
    lines = []
    cursor = 0.0
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for i, (label, text, sub, sticker, say) in enumerate(SCENES):
            dur = measure(say, tmp)
            # 每屏 = 语音 + 收尾呼吸；最后一屏多留一点
            span = max(3.4, dur + (1.55 if i == len(SCENES) - 1 else 1.05))
            span = round(span, 2)
            item = {
                "label": label,
                "text": text,
                "sub": sub,
                "sticker": str(STICKERS / sticker),
                "say": say,
                "at": round(cursor, 2),
                "dur": span,
            }
            if sub.count(">") >= 2:
                item["kind"] = "chain"
            lines.append(item)
            if verbose:
                print(f"  {i+1:02d}  {span:5.2f}s  (念 {dur:4.2f}s)  {label}")
            cursor += span

    spec = Spec.from_dict(
        {
            "style": "explain",
            "width": 1080,
            "height": 1920,
            "fps": 24,
            "voice": {"enabled": True, "name": VOICE, "rate": RATE, "pitch": PITCH},
            "bgm": {"enabled": True, "volume": 0.13},
            "footer": "AI 生成 · Deepseek-Chat",
            "lines": lines,
        }
    )
    resolve(spec)
    return spec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true", help="写完 spec 顺手出片")
    ap.add_argument("--out", default="")
    ap.add_argument("--sfx-volume", type=float, default=0.14, help="音效整体音量（0=关）")
    args = ap.parse_args()

    print(f"量配音（{VOICE}）：")
    spec = build_spec()
    if args.sfx_volume <= 0:
        spec.sfx_enabled = False
    else:
        spec.sfx_volume = float(args.sfx_volume)
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(spec.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n共 {len(spec.lines)} 屏，总时长 {spec.duration:.2f}s")
    print(f"spec → {OUT_JSON}")
    if not args.build:
        return 0

    print("\n开始出片...")
    result = pipeline.build(spec, out_path=args.out or None, progress=lambda s, p: None)
    print(f"\n成片 {result['out']}  {result['duration']}s  {result['size']/1024:.0f} KB")
    print(f"耗时 {result['duration_sec']}s（渲染 {result['render_sec']}s / 配音 {result['tts_sec']}s / 混音 {result['mix_sec']}s）")
    if result["warnings"]:
        print("注意：" + "；".join(result["warnings"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
