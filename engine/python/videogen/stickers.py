"""表情包（GIF）→ 逐帧 PNG → 页面里的 data URI。

为什么不用 `<img src="xxx.gif">`：
  1. GIF 播放进度由浏览器时钟决定，逐帧截图时同一 t 拿到的帧可能不一样（不可确定性）；
  2. file:// 页面把本地图片画进 canvas 会把 canvas 标记为「被污染」，`toDataURL()` 直接抛异常。
所以这里先用 ffmpeg 把 GIF 抽成固定 fps 的 PNG 序列并缓存，再以 data URI 内联进页面 ——
data URI 不污染 canvas，且帧序号由我们按时间算，完全可复现。
"""

from __future__ import annotations

import base64
import hashlib
import shutil
import subprocess
from pathlib import Path

from .paths import FFMPEG, WORK_DIR


def _slug(path: Path) -> str:
    stem = path.stem
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in stem)[:40]
    digest = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:6]
    return f"{safe}-{digest}"


def frames_dir(gif: Path, fps: int = 10) -> Path:
    """抽帧并缓存到 data/videogen_work/stickers/<slug>-<size>-<mtime>/。"""
    gif = Path(gif)
    st = gif.stat()
    key = f"{_slug(gif)}-{st.st_size}-{int(st.st_mtime)}-{fps}"
    out = Path(WORK_DIR) / "stickers" / key
    done = out / ".ok"
    if done.exists() and any(out.glob("f*.png")):
        return out
    if out.exists():
        shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            str(FFMPEG), "-y", "-v", "error",
            "-i", str(gif),
            "-vf", f"fps={fps}",
            "-start_number", "1",
            str(out / "f%03d.png"),
        ],
        check=True,
        capture_output=True,
    )
    if not any(out.glob("f*.png")):
        raise RuntimeError(f"表情包抽帧失败：{gif}")
    done.write_text("ok", encoding="ascii")
    return out


def _drop_blank(files: list[Path]) -> list[Path]:
    """丢掉「空帧」。

    有些 GIF（比如猫蹦出画面再落回来的那类）中间若干帧整张是全透明的，
    像素上什么也没有。这种帧按时间播放时就是一次空白闪帧（本机 `dfy_9N07_蹦`
    的 20 帧里有 5 帧是 1084B 的全透明 PNG，明显比其他帧的 30–87KB 小一截）。
    不引入 Pillow 也能判断：全透明 PNG 压缩后极小，按体积把离群的小帧筛掉即可。
    """
    if len(files) < 3:
        return files
    sizes = sorted(p.stat().st_size for p in files)
    median = sizes[len(sizes) // 2]
    floor = max(1500, median * 0.28)
    kept = [p for p in files if p.stat().st_size >= floor]
    return kept or files


def frames_data_uris(gif: Path, fps: int = 10, limit: int = 40) -> list[str]:
    """返回按时间顺序的 data URI 列表（最多 limit 帧，超出则等间隔抽样）。"""
    files = _drop_blank(sorted(frames_dir(gif, fps=fps).glob("f*.png")))
    if not files:
        return []
    if len(files) > limit:
        step = len(files) / float(limit)
        files = [files[int(i * step)] for i in range(limit)]
    return ["data:image/png;base64," + base64.b64encode(f.read_bytes()).decode("ascii") for f in files]
