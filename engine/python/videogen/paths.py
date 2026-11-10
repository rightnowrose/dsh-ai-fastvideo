"""所有外部依赖路径集中在这里：能自动探测就自动探测，探测不到就用环境变量覆盖。

插件 Host 会在 spawn 引擎时下发 VIDEOGEN_* 环境变量（见 index.js 的 childEnv），
所以发布版不需要改这个文件；命令行单独跑（`python -m videogen.cli`）时也可以自己
export 同名变量。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent           # .../engine/python/videogen
ENGINE_DIR = APP_DIR.parent                          # .../engine/python
PACKAGE_DIR = APP_DIR.parents[2]                     # .../dsh-ai-video


def _env_opt(name: str) -> Path | None:
    """读环境变量；没设置（或只有空白）时返回 None，方便用 `or` 继续往下找。"""
    value = os.environ.get(name)
    if value and value.strip():
        return Path(value.strip())
    return None


def _str_env(name: str) -> str:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else ""


def _first(*candidates: str | Path) -> Path | None:
    for item in candidates:
        if not item:
            continue
        path = Path(item)
        try:
            if path.exists():
                return path
        except OSError:
            continue
    return None


def _which(command: str, *extras: str) -> Path:
    """先查 PATH；查不到再看候选绝对路径；最后退回命令名本身（交给子进程按 PATH 解析）。"""
    found = shutil.which(command)
    if found:
        return Path(found)
    return _first(*extras) or Path(command)


ROOT = _env_opt("VIDEOGEN_WORKSPACE") or Path.cwd()  # 工作区根目录（只用于抓上下文，可为空）
_SHARED = _env_opt("VIDEOGEN_DATA_ROOT") or (Path.home() / ".dsh" / "data" / "dsh-ai-video")

FFMPEG = _env_opt("VIDEOGEN_FFMPEG") or _which(
    "ffmpeg", "C:/ffmpeg/bin/ffmpeg.exe", "D:/ffmpeg/bin/ffmpeg.exe",
    "/usr/local/bin/ffmpeg", "/opt/homebrew/bin/ffmpeg", "/usr/bin/ffmpeg",
)
FFPROBE = _env_opt("VIDEOGEN_FFPROBE") or _which(
    "ffprobe", "C:/ffmpeg/bin/ffprobe.exe", "D:/ffmpeg/bin/ffprobe.exe",
    "/usr/local/bin/ffprobe", "/opt/homebrew/bin/ffprobe", "/usr/bin/ffprobe",
)
EDGE = _env_opt("VIDEOGEN_EDGE") or _first(
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
) or Path("")
POWERSHELL = _env_opt("VIDEOGEN_POWERSHELL") or _first(
    "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
) or _which("pwsh", "powershell")

# 配音：跑同一个 Python（需要 `pip install edge-tts`），脚本是包内自带的 voice_server.py。
VOICE_PY = _env_opt("VIDEOGEN_VOICE_PY") or Path(sys.executable)
VOICE_SERVER = _env_opt("VIDEOGEN_VOICE_SERVER") or (ENGINE_DIR / "voice_server.py")
# 自训练音色（可选）：只有显式配了 GPT-SoVITS 的 voice config 才会出现。
VOICE_CONFIG = _str_env("VIDEOGEN_VOICE_CONFIG")
GSV_START = _str_env("VIDEOGEN_GSV_START")
GSV_PORT = int(os.environ.get("VIDEOGEN_GSV_PORT") or 9880)

APPS_DIR = _env_opt("VIDEOGEN_APPS") or (ROOT / "apps")

TEMPLATES = APP_DIR / "templates"
RENDER_PS1 = APP_DIR / "tools" / "render-cdp.ps1"

DATA_DIR = _env_opt("VIDEOGEN_DATA") or (_SHARED / "out")
DB_PATH = _env_opt("VIDEOGEN_DB") or (_SHARED / "jobs.db")
WORK_DIR = _env_opt("VIDEOGEN_WORK") or (_SHARED / "work")
DEFAULT_BGM = _str_env("VIDEOGEN_BGM")
STYLE_ROOT = _str_env("VIDEOGEN_STYLE_ROOT")


def ensure_dirs() -> None:
    for path in (DATA_DIR, WORK_DIR):
        path.mkdir(parents=True, exist_ok=True)


def check_env() -> list[str]:
    """返回缺失的硬依赖列表（空列表表示齐了）。"""
    missing = []
    for label, path in (
        ("ffmpeg", FFMPEG),
        ("ffprobe", FFPROBE),
        ("浏览器(Edge/Chrome)", EDGE),
        ("powershell/pwsh", POWERSHELL),
        ("render-cdp.ps1", RENDER_PS1),
    ):
        if not str(path) or not Path(path).exists():
            missing.append(f"{label}: {path or '（未找到）'}")
    return missing


def check_audio_env() -> list[str]:
    missing = []
    for label, path in (("配音 python", VOICE_PY), ("voice_server.py", VOICE_SERVER)):
        if not str(path) or not Path(path).exists():
            missing.append(f"{label}: {path or '（未找到）'}")
    return missing


def gsv_voices() -> dict:
    """可选的自训练音色表（键就是 --voice 的值）。"""
    if not str(VOICE_CONFIG):
        return {}
    try:
        cfg = json.loads(Path(VOICE_CONFIG).read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return {}
    voices = cfg.get("gsv_voices")
    return voices if isinstance(voices, dict) else {}
