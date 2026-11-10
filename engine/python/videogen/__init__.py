"""videogen —— AI 速成视频的本地渲染管线（页面 → 逐帧 → 配音 → 混音）。

对外只暴露四件事：

    from videogen import spec, pipeline, jobs, styles

    s = spec.Spec(style="a3", lines=[spec.Line("你"), spec.Line("懂什么")])
    r = pipeline.build(s)            # -> dict(out=Path, duration=..., size=...)
    jobs.enqueue(...)                # 丢进 SQLite 队列，由 worker 出片

设计约束（都是前几轮实测踩出来的）：
  * 画面由「单文件 HTML + Canvas 2D」画，draw(t) 必须是时间纯函数，
    渲染器只做 window.seek(t) + toDataURL，所以能跑 33ms/帧；
  * 页面不读网络、不读随机数（种子固定），改 spec 只用字符串替换；
  * 渲染靠本机 Edge 的 CDP（PowerShell 实现，见 tools/render-cdp.ps1），
    音频靠 edge-tts + ffmpeg，全部本机零 API key。
"""

from . import spec, styles, render, audio, pipeline, jobs  # noqa: F401

__version__ = "1.0.0"
