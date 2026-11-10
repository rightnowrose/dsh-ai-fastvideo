"""本地语音合成服务（多引擎、可给任何程序用）

默认引擎：edge-tts（微软 Edge 在线语音，免费、无需 key、中文音色多）。
设计目标：QQ 机器人只是第一个调用方；同一套服务也能给脚本、浏览器、其它程序用。

启动：
    F:\\Deepseek-Chat\\apps\\voice\\.venv\\Scripts\\python.exe F:\\Deepseek-Chat\\apps\\voice\\server.py
或双击/运行 F:\\Deepseek-Chat\\apps\\voice\\start-voice.ps1（后台常驻，端口 7700）

HTTP 接口：
    GET  /health                      → {"ok":true,...}
    GET  /voices                      → 中文音色列表
    POST /tts                         body: {"text":"...", "voice":"zh-CN-XiaoxiaoNeural",
                                            "rate":"-5%", "pitch":"-2Hz", "format":"mp3"}
                                      → 直接返回音频字节（默认 audio/mpeg）
    POST /v1/audio/speech             OpenAI 兼容：{"model":"tts-1","input":"...","voice":"alloy"}
                                      → 返回音频字节，方便任何现成客户端接入

命令行：
    python server.py --text "主人，欢迎回来" --out out.mp3 [--voice ...]
"""

from __future__ import annotations

import argparse
import array
import asyncio
import hashlib
import json
import os
import random
import re
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
DATA_ROOT = r"F:\Deepseek-Chat\data"

DEFAULT_CONFIG = {
    "host": "127.0.0.1",
    "port": 7700,
    "engine": "edge",
    "default_voice": "zh-CN-XiaoxiaoNeural",
    "rate": "-5%",
    "pitch": "-2Hz",
    "volume": "+0%",
    "format": "mp3",
    "max_text_chars": 300,
    "cache_dir": os.path.join(DATA_ROOT, "voice_cache"),
    "rvc_url": "http://127.0.0.1:7710/convert",
    "rvc_timeout": 240,
    "rvc_voices": {},
}


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
                cfg.update(json.load(f))
        except Exception:
            traceback.print_exc()
    return cfg


CFG = load_config()
os.makedirs(CFG["cache_dir"], exist_ok=True)

# 同一块 GPU 上并发跑两次合成会互相拖慢、甚至把 api_v2 拖到超时，串行排队更稳
_GSV_LOCK = threading.Lock()
ACTION_LOG = os.path.abspath(os.path.join(HERE, "..", "..", ".cache", "console-run", "actions.log"))


def _note_action(kind: str, detail: str, ok: bool = True) -> None:
    """往控制台的动作日志里记一行，这样「这条语音为什么不是她的声音」能回查。"""
    try:
        os.makedirs(os.path.dirname(ACTION_LOG), exist_ok=True)
        line = "{0}\t{1}\t{2}\t{3}\n".format(
            time.strftime("%Y-%m-%d %H:%M:%S"), "OK" if ok else "FAIL", kind, detail
        )
        with open(ACTION_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:  # noqa: BLE001
        pass


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _ffmpeg_bytes(data: bytes, in_ext: str, out_ext: str) -> bytes:
    """用 ffmpeg 在内存文件之间转码（mp3 <-> wav）。"""
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory(prefix="voice-ff-") as td:
        src = os.path.join(td, "in." + in_ext)
        dst = os.path.join(td, "out." + out_ext)
        with open(src, "wb") as f:
            f.write(data)
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", src]
        if out_ext == "wav":
            cmd += ["-ar", "44100", "-ac", "1"]
        elif out_ext == "mp3":
            # 语音条给到 64kbps / 44.1k：齿音和气息更清楚（QQ 语音条不会再二次压缩）
            cmd += ["-ar", "44100", "-ac", "1", "-b:a", "64k"]
        cmd += [dst]
        flags = 0x08000000 if os.name == "nt" else 0
        proc = subprocess.run(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, timeout=120, creationflags=flags,
        )
        if proc.returncode != 0:
            raise RuntimeError("ffmpeg 转换失败：" + proc.stderr.decode("utf-8", "ignore")[-300:])
        with open(dst, "rb") as f:
            return f.read()


_EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"  # 表情/符号/交通/补充符号
    "\U00002600-\U000027BF"  # 杂项符号 + dingbats
    "\U00002190-\U000021FF"  # 箭头
    "\U00002B00-\U00002BFF"  # 杂项符号和箭头
    "\U0000FE00-\U0000FE0F"  # 变体选择符
    "\U00002000-\U0000206F"  # 各种空格/零宽
    "\U0001F1E6-\U0001F1FF"  # 区域指示符（国旗）
    "]+"
)


_CN_DIGITS = "零一二三四五六七八九"


def _cn_number(n: int) -> str:
    """0~99999 转中文读法，够用于「三点」「五十」这类日常数字。"""
    if n < 0:
        return ""
    if n < 10:
        return _CN_DIGITS[n]
    out = []
    rest = n
    for val, unit in ((10000, "万"), (1000, "千"), (100, "百"), (10, "十")):
        q, rest = divmod(rest, val)
        if q:
            out.append(_CN_DIGITS[q] + unit)
    if rest:
        out.append(_CN_DIGITS[rest])
    s = "".join(out)
    return "十" + s[1:] if s.startswith("一十") else s


def _normalize_reading(s: str) -> str:
    """把「50%」「3 个」换成中文读法，免得 TTS 念成英文或磕巴。"""
    def pct(m):
        try:
            return "百分之" + _cn_number(int(float(m.group(1))))
        except Exception:  # noqa: BLE001
            return m.group(0)

    s = re.sub(r"(\d{1,4}(?:\.\d+)?)\s*%", pct, s)
    s = re.sub(r"(?<![\d.])(\d{1,2})(?![\d.])", lambda m: _cn_number(int(m.group(1))), s)
    return s


def _break_long_commas(s: str) -> str:
    """一句里逗号太多会被 cut5 切成 2~4 字碎片（最容易念成怪声），隔一个改成句号。"""
    out = []
    for sent in re.split(r"(?<=[。！？!?…])", s):
        if not sent:
            continue
        if sent.count("，") + sent.count(",") >= 3:
            chars = list(sent)
            seen = 0
            for i, ch in enumerate(chars):
                if ch in "，,":
                    seen += 1
                    if seen % 2 == 0:
                        chars[i] = "。"
            sent = "".join(chars)
        out.append(sent)
    return "".join(out)


def _clean_voice_text(text: str) -> str:
    """清掉 emoji/零宽字符/markdown/超长标点。

    GPT-SoVITS 的 api_v2 在遇到 emoji 时会直接 500（内部用 gbk 编码打印），
    而「……」这类连续省略号会生成只有静音的片段，所以统一在这里处理。
    """
    s = text or ""
    s = _EMOJI_RE.sub("", s)
    s = s.replace("**", "").replace("__", "").replace("`", "")
    s = re.sub(r"https?://\S+", "", s)
    s = re.sub(r"[…\.·]{2,}", "…", s)
    s = re.sub(r"[—─]{2,}", "—", s)
    s = re.sub(r"([，,。！？!?；;：:])\1+", r"\1", s)
    s = re.sub(r"\s+", " ", s)
    s = s.strip(" \t\r\n")
    s = _normalize_reading(s)
    s = _break_long_commas(s)
    # GPT-SoVITS 的 cut5 靠标点切句，句尾没有标点时尾字容易被吞，补一个句号
    if s and s[-1] not in "。！？…—，、；：,.!?;:":
        s += "。"
    return s or "嗯"


_MOOD_TAGS = {
    "soft": "gentle", "gentle": "gentle", "温柔": "gentle", "轻": "gentle",
    "low": "low", "低落": "low", "sad": "low", "低声": "low",
    "bright": "bright", "雀跃": "bright", "开心": "bright", "轻快": "bright",
    "sleepy": "sleepy", "困倦": "sleepy", "困": "sleepy", "懒": "sleepy",
}
_TAG_RE = re.compile(r"[\[【(（]\s*([A-Za-z\u4e00-\u9fa5]{1,6})\s*[\]】)）]")


def split_mood_tag(text: str):
    """从文本里剥掉情绪标记（如 [[low]]、【温柔】、(sleepy)），返回 (纯文本, mood)。

    模型不认识「用低沉的语气说」这种指令，但可以写标记——标记不发音，
    由这里取出来当参考音选择依据（这就是「隐藏情绪标记」）。
    """
    s = text or ""
    mood = None
    for m in _TAG_RE.finditer(s):
        key = m.group(1).strip().lower()
        if key in _MOOD_TAGS:
            mood = mood or _MOOD_TAGS[key]
            s = s.replace(m.group(0), " ")
    return re.sub(r"\s+", " ", s).strip(), mood


def _prosody_wav(data: bytes) -> bytes:
    """B：轻量韵律后处理——去首尾静音、轻度压缩、2.6k 提亮、限幅、淡入。

    不改音高/语速（那是参考音的事），只把「一口气念完、忽大忽小」修顺。
    """
    cfg = CFG.get("prosody") or {}
    if not isinstance(cfg, dict) or not cfg.get("enabled", True):
        return data
    if _wav_seconds(data) <= 0.2:
        return data
    import subprocess
    import tempfile

    g = float(cfg.get("presence_db", 1.6))
    comp = str(cfg.get("compressor") or "acompressor=threshold=-20dB:ratio=2.5:attack=6:release=140:makeup=2")
    chain = ",".join([
        "highpass=f=90",
        "silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.05",
        "areverse",
        "silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.05",
        "areverse",
        comp,
        f"equalizer=f=2600:t=q:w=1.2:g={g}",
        "alimiter=limit=0.95",
        "afade=t=in:d=0.03",
    ])
    with tempfile.TemporaryDirectory(prefix="voice-pro-") as td:
        src = os.path.join(td, "in.wav")
        dst = os.path.join(td, "out.wav")
        with open(src, "wb") as f:
            f.write(data)
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-af", chain, "-ar", "32000", "-ac", "1", dst]
        flags = 0x08000000 if os.name == "nt" else 0
        proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.PIPE, timeout=120, creationflags=flags)
        if proc.returncode != 0:
            _log("[voice] 韵律后处理失败，用原始音频：" + proc.stderr.decode("utf-8", "ignore")[-200:])
            return data
        with open(dst, "rb") as f:
            out = f.read()
    return out if out[:4] == b"RIFF" else data


def _wav_pcm16(data: bytes):
    """返回 (samples, rate, channels)；只处理 16bit PCM，其它返回 (None, 0, 0)。"""
    try:
        if len(data) < 44 or data[:4] != b"RIFF":
            return None, 0, 0
        rate = channels = bits = 0
        i = 12
        while i + 8 <= len(data):
            cid = data[i : i + 4]
            size = int.from_bytes(data[i + 4 : i + 8], "little")
            body = i + 8
            if cid == b"fmt ":
                channels = int.from_bytes(data[body + 2 : body + 4], "little")
                rate = int.from_bytes(data[body + 4 : body + 8], "little")
                bits = int.from_bytes(data[body + 14 : body + 16], "little")
            elif cid == b"data":
                if not (rate and channels and bits == 16):
                    return None, 0, 0
                n = min(size, max(0, len(data) - body))
                n -= n % 2
                raw = data[body : body + n]
                return array.array("h", raw), rate, channels
            i = body + size + (size & 1)
    except Exception:  # noqa: BLE001
        pass
    return None, 0, 0


def _burst_pct(data: bytes, hf_thr: float = 0.8, loud_frac: float = 0.08) -> float:
    """估算「高频爆音帧」占比：帧内一阶差分能量比过高且响度不低的帧。

    实测校准：她本人的正常语音帧 diff 比约 0.04~0.07，声码器爆音帧 > 0.8，
    阈值 0.8 对干净语音误报约 1/5400 帧。
    """
    samples, rate, channels = _wav_pcm16(data)
    if not samples:
        return -1.0
    if channels > 1:
        samples = array.array("h", samples[::channels])
    total = len(samples)
    n, hop = 512, 256
    if total < n * 2:
        return 0.0
    rms = []
    ratios = []
    for i in range(0, total - n, hop):
        seg = samples[i : i + n]
        e = 0.0
        d = 0.0
        prev = seg[0]
        for s in seg:
            e += float(s) * float(s)
            diff = float(s) - float(prev)
            d += diff * diff
            prev = s
        rms.append((e / n) ** 0.5)
        ratios.append(d / (e + 1e-9))
    peak = max(rms) if rms else 0.0
    if peak <= 0:
        return 0.0
    loud = [k for k, r in enumerate(rms) if r > peak * loud_frac]
    if not loud:
        return 0.0
    bad = sum(1 for k in loud if ratios[k] > hf_thr)
    return 100.0 * bad / len(loud)


def _wav_level(data: bytes):
    """返回 (rms, peak)，都在 0~1；解析失败返回 (-1, -1)。"""
    samples, rate, channels = _wav_pcm16(data)
    if not samples:
        return -1.0, -1.0
    if channels > 1:
        samples = array.array("h", samples[::channels])
    n = len(samples)
    if n == 0:
        return 0.0, 0.0
    total = 0.0
    peak = 0
    for s in samples:
        total += float(s) * float(s)
        a = s if s >= 0 else -s
        if a > peak:
            peak = a
    rms = (total / n) ** 0.5 / 32768.0
    return rms, peak / 32768.0


def _wav_seconds(data: bytes) -> float:
    """从 wav 头算时长；不用 ffprobe，省一次进程开销。"""
    try:
        if len(data) < 44 or data[:4] != b"RIFF":
            return 0.0
        rate = channels = bits = 0
        i = 12
        while i + 8 <= len(data):
            cid = data[i : i + 4]
            size = int.from_bytes(data[i + 4 : i + 8], "little")
            body = i + 8
            if cid == b"fmt ":
                channels = int.from_bytes(data[body + 2 : body + 4], "little")
                rate = int.from_bytes(data[body + 4 : body + 8], "little")
                bits = int.from_bytes(data[body + 14 : body + 16], "little")
            elif cid == b"data":
                if rate and channels and bits:
                    n = min(size, max(0, len(data) - body))
                    return n / float(rate * channels * bits / 8)
                return 0.0
            i = body + size + (size & 1)
    except Exception:  # noqa: BLE001
        pass
    return 0.0


def _trim_tail_silence(wav_bytes: bytes, threshold_db: int = -45, duration: float = 0.35) -> bytes:
    """剪掉尾部静音（GPT-SoVITS 偶尔会在句尾挂好几秒静音）。"""
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory(prefix="voice-trim-") as td:
        src = os.path.join(td, "in.wav")
        dst = os.path.join(td, "out.wav")
        with open(src, "wb") as f:
            f.write(wav_bytes)
        af = (
            "areverse,silenceremove=start_periods=1"
            f":start_duration={duration}:start_threshold={threshold_db}dB,areverse"
        )
        flags = 0x08000000 if os.name == "nt" else 0
        proc = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-af", af, dst],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, timeout=120, creationflags=flags,
        )
        if proc.returncode != 0 or not os.path.exists(dst):
            return wav_bytes
        with open(dst, "rb") as f:
            out = f.read()
    return out if len(out) > 4096 else wav_bytes


def _rvc_convert(wav_bytes: bytes, params: dict) -> bytes:
    """把 wav 交给本地 RVC 服务（端口 7710）转成目标音色，返回 wav。"""
    import urllib.error
    import urllib.request

    url = CFG.get("rvc_url") or "http://127.0.0.1:7710/convert"
    req = urllib.request.Request(url, data=wav_bytes, method="POST")
    req.add_header("Content-Type", "audio/wav")
    if params:
        req.add_header("X-RVC-Params", json.dumps(params, ensure_ascii=False))
    try:
        with urllib.request.urlopen(req, timeout=int(CFG.get("rvc_timeout") or 240)) as resp:
            data = resp.read()
    except urllib.error.HTTPError as e:  # noqa: PERF203
        body = e.read().decode("utf-8", "ignore")[:400]
        raise RuntimeError(f"RVC 服务报错 HTTP {e.code}: {body}") from e
    if not data:
        raise RuntimeError("RVC 服务返回空音频")
    return data


def _f5_convert(text: str, spec: dict) -> bytes:
    """把文本交给本地 F5-TTS 零样本克隆服务（端口 7720），返回 wav。"""
    import urllib.error
    import urllib.request

    url = spec.get("url") or CFG.get("f5_url") or "http://127.0.0.1:7720/tts"
    payload = {
        "text": text,
        "voice": spec.get("voice") or "mingqiannailv",
        "speed": spec.get("speed", 1.0),
        "nfe": spec.get("nfe"),
        "cfg_strength": spec.get("cfg_strength"),
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    try:
        with urllib.request.urlopen(req, timeout=int(CFG.get("f5_timeout") or 300)) as resp:
            data = resp.read()
    except urllib.error.HTTPError as e:  # noqa: PERF203
        detail = e.read().decode("utf-8", "ignore")[:400]
        raise RuntimeError(f"F5-TTS 服务报错 HTTP {e.code}: {detail}") from e
    if not data:
        raise RuntimeError("F5-TTS 服务返回空音频")
    return data


def _gsv_convert(text: str, spec: dict) -> bytes:
    """把文本交给本地 GPT-SoVITS api_v2（端口 9880）合成，返回 wav。"""
    import urllib.error
    import urllib.request

    url = spec.get("url") or CFG.get("gsv_url") or "http://127.0.0.1:9880/tts"
    payload = {
        "text": text,
        "text_lang": spec.get("text_lang", "zh"),
        "ref_audio_path": spec.get("ref_audio_path"),
        "prompt_text": spec.get("prompt_text", ""),
        "prompt_lang": spec.get("prompt_lang", "zh"),
        "text_split_method": spec.get("split", "cut0"),
        "batch_size": int(spec.get("batch_size", 1)),
        "media_type": "wav",
        "streaming_mode": False,
    }
    # 可选生成参数：温度/采样上限等，降低"念漏/念错"的概率
    for key in ("temperature", "top_k", "top_p", "repetition_penalty", "speed_factor", "fragment_interval", "seed"):
        if spec.get(key) is not None:
            payload[key] = spec[key]
    # 关键：每次都给随机 seed。GPT 半段在短句上会「提前 EOS 拖长」，同一 seed 会复现任一条坏样本；
    # 换 seed 才能让上面的重试真的换一条采样路径（实测 18 字句 6/6 合格、4 字句 3/6 但三次重试几乎必过）。
    if payload.get("seed") is None:
        payload["seed"] = random.randint(1, 10 ** 6)
    if not payload["ref_audio_path"]:
        raise RuntimeError("gsv voice 缺少 ref_audio_path")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    try:
        with urllib.request.urlopen(req, timeout=int(CFG.get("gsv_timeout") or 300)) as resp:
            data = resp.read()
    except urllib.error.HTTPError as e:  # noqa: PERF203
        detail = e.read().decode("utf-8", "ignore")[:400]
        raise RuntimeError(f"GPT-SoVITS 报错 HTTP {e.code}: {detail}") from e
    if not data:
        raise RuntimeError("GPT-SoVITS 返回空音频")
    return data


async def _edge_once(text: str, voice: str, rate: str, pitch: str, volume: str) -> bytes:
    import edge_tts

    communicate = edge_tts.Communicate(
        text, voice, rate=rate or "+0%", pitch=pitch or "+0Hz", volume=volume or "+0%"
    )
    chunks = bytearray()
    async for chunk in communicate.stream():
        if chunk.get("type") == "audio":
            chunks.extend(chunk.get("data", b""))
    return bytes(chunks)


async def _edge_synth(text: str, voice: str, rate: str, pitch: str, volume: str) -> bytes:
    """带重试：edge 的合成服务偶发返回空音频（限流/握手抖动），重试 3 次即可。"""
    last: Exception | None = None
    for attempt in range(1, 4):
        try:
            data = await _edge_once(text, voice, rate, pitch, volume)
            if data:
                return data
            last = RuntimeError("empty audio")
        except Exception as e:  # noqa: BLE001
            last = e
        _log(f"[voice] synth attempt {attempt} failed: {last}")
        if attempt < 3:
            await asyncio.sleep(0.6 * attempt)
    raise RuntimeError(f"合成失败（已重试 3 次）：{last}")


def _all_moods() -> list[str]:
    """所有音色里配置过的情绪档（去掉 default/auto）。"""
    out: set[str] = set()
    for spec in (CFG.get("gsv_voices") or {}).values():
        refs = (spec or {}).get("refs")
        if isinstance(refs, dict):
            out.update(k for k in refs.keys() if k not in ("default", "auto"))
    return sorted(out)


_MOOD_WORDS = {
    "sleepy": ("困", "睡", "累", "晚安", "熬夜", "躺", "闭眼"),
    "bright": ("哈", "笑", "开心", "好耶", "嘛", "呀"),
}


def _pick_ref(spec: dict, text: str, mood: str | None = None) -> dict | None:
    """挑参考音：显式 mood 优先，其次按语气词猜；仅当 spec 里配了 refs 时生效。"""
    refs = spec.get("refs") or {}
    if not isinstance(refs, dict) or not refs:
        return None
    want = (mood or "").strip().lower()
    if want in ("", "auto"):
        want = ""
    if want and want in refs:
        name = want
    else:
        name = "default"
        for key, words in _MOOD_WORDS.items():
            if key in refs and any(w in text for w in words):
                name = key
                break
    ref = refs.get(name) or refs.get("default")
    if isinstance(ref, str):
        return {"audio": ref, "prompt": ""}
    return ref if isinstance(ref, dict) else None


def synth(text: str, voice: str | None = None, rate: str | None = None,
          pitch: str | None = None, volume: str | None = None,
          mood: str | None = None) -> tuple[bytes, str]:
    """合成语音，返回 (音频字节, 缓存文件路径)。带磁盘缓存。"""
    text, tag_mood = split_mood_tag(text or "")
    if not text:
        raise ValueError("text 不能为空")
    if not (mood or "").strip():
        mood = tag_mood or ""
    if len(text) > int(CFG["max_text_chars"]):
        text = text[: int(CFG["max_text_chars"])]
    voice = voice or CFG["default_voice"]
    rate = rate if rate is not None else CFG["rate"]
    pitch = pitch if pitch is not None else CFG["pitch"]
    volume = volume if volume is not None else CFG["volume"]

    rvc_map = CFG.get("rvc_voices") or {}
    f5_map = CFG.get("f5_voices") or {}
    gsv_map = CFG.get("gsv_voices") or {}
    if isinstance(gsv_map, dict) and voice in gsv_map:
        spec = dict(gsv_map.get(voice) or {})
        # 先清洗文本：emoji 会让 GPT-SoVITS 直接 500，连续省略号会出大片静音
        tts_text = _clean_voice_text(text)
        n_clean = len([c for c in tts_text if c.isalnum()])
        ref = _pick_ref(spec, tts_text, mood)
        if ref:
            spec["ref_audio_path"] = ref.get("audio") or spec.get("ref_audio_path")
            if ref.get("prompt"):
                spec["prompt_text"] = ref["prompt"]
        spec_key = {k: v for k, v in spec.items() if k != "refs"}
        pro_on = bool((CFG.get("prosody") or {}).get("enabled", True))
        key = _sha1(
            f"gsv|{voice}|{mood or 'auto'}|{json.dumps(spec_key, sort_keys=True)}|v10|64k|pro={pro_on}|{tts_text}"
        )
        out_path = os.path.join(CFG["cache_dir"], f"{key}.mp3")
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            with open(out_path, "rb") as f:
                return f.read(), out_path
        base_attempts = int(spec.get("max_attempts") or CFG.get("gsv_max_attempts") or 4)
        # 短句（「笑什么呀。」「我笑了。」这类）GPT 半段容易提前 EOS 吐出 0.34s 静音，
        # 单次失败很快（1 秒内），所以短文本多给几次机会；长句仍按原次数。
        short_attempts = int(CFG.get("gsv_short_attempts") or 6)
        attempts = max(base_attempts, short_attempts) if n_clean <= 8 else base_attempts
        ratio = float(spec.get("max_sec_per_char") or CFG.get("gsv_max_sec_per_char") or 0.62)
        limit = max(3.2, len(tts_text) * ratio + 1.6)
        burst_max = float(spec.get("max_burst_pct") or CFG.get("gsv_max_burst_pct") or 1.5)
        # 正常语速 3.4~5.2 字/秒；<2.2 就是「一字一顿地拖」，听着像卡带
        min_cps = float(spec.get("min_cps") or CFG.get("gsv_min_cps") or 2.2)
        min_cps_chars = int(spec.get("min_cps_chars") or CFG.get("gsv_min_cps_chars") or 8)
        short_cps = float(spec.get("short_min_cps") or CFG.get("gsv_short_min_cps") or 1.8)
        # 情绪档用的是变形参考音，容易拖长；给它一个更狠的上限，超了就算异常
        mood_max = float(spec.get("mood_max_sec") or CFG.get("mood_max_sec") or 8.0)
        # 情绪档的 8 秒上限只该用在短句上：句子长了，时长本来就得跟着变长
        mood_cap = max(mood_max, 0.45 * len(tts_text) + 2.0)
        mood_on = bool((mood or "").strip()) and (mood or "").strip().lower() != "auto"

        def _cand(text_: str) -> bytes:
            """合成一次并立即做韵律后处理：后面所有判定都用交付给用户的那份音频。"""
            w = _gsv_convert(text_, spec)
            return _prosody_wav(w) if pro_on else w

        wav_out = b""
        best = b""
        best_secs = 0.0
        clean = b""
        clean_burst = -1.0
        clean_secs = 0.0
        t_wait = time.time()
        _GSV_LOCK.acquire()
        try:
            waited = time.time() - t_wait
            if waited > 0.3:
                _log(f"[voice] gsv 排队 {waited * 1000:.0f}ms 后开始合成 voice={voice}")
            # 极短句（「飞。」「笑。」这类）GPT 半段几乎必然提前 EOS 吐出静音，靠多试几次撞概率；
            # 实在不行交给下面的兜底（宁可退 edge 音色，也不念出加料的怪句子）。
            variant_texts = [tts_text] * attempts
            for attempt, sent_text in enumerate(variant_texts, 1):
                attempts = len(variant_texts)
                cand = _cand(sent_text)
                secs = _wav_seconds(cand)
                burst = _burst_pct(cand)
                cps = (len(tts_text) / secs) if secs > 0.05 else 0.0
                # 正常语速 3.4~5.2 字/秒；短句没有足够上下文，放宽到 1.8 字/秒，低于此就是「一字一顿」
                slow = (
                    (len(tts_text) >= min_cps_chars and cps < min_cps)
                    or (len(tts_text) < min_cps_chars and cps < short_cps)
                )
                lvl_rms, lvl_peak = _wav_level(cand)
                quiet = lvl_rms >= 0 and (lvl_rms < 0.012 or lvl_peak < 0.06)
                # 只挡「几乎没声」；正常快读 7~10 字/秒 也放行，但每字至少 0.12 秒
                min_secs = max(0.50, len(tts_text) * 0.12)
                over_mood = mood_on and secs > mood_cap
                ok_len = 0.2 <= secs <= limit and not slow and secs >= min_secs and not quiet and not over_mood
                if (not best or (secs > 0.2 and secs < best_secs)) and not over_mood:
                    best, best_secs = cand, secs
                if burst >= 0 and (clean_burst < 0 or burst < clean_burst) and ok_len:
                    clean, clean_burst, clean_secs = cand, burst, secs
                if ok_len and burst <= burst_max:
                    wav_out = cand
                    if attempt > 1:
                        _log(
                            f"[voice] gsv 重试成功 attempt={attempt} secs={secs:.2f} "
                            f"limit={limit:.2f} cps={cps:.2f} burst={burst:.2f}%"
                        )
                    break
                # 退化：短文本被 GPT 阶段无限续写（十几到几十秒乱码）、
                # 声码器高频爆音（参考音噪声被克隆）、或一个字一个字拖长
                if quiet or secs < min_secs:
                    _log(
                        f"[voice] gsv 空音/过短 attempt={attempt}/{attempts} secs={secs:.2f} "
                        f"rms={lvl_rms:.4f} peak={lvl_peak:.3f} min={min_secs:.2f} "
                        f"chars={len(tts_text)} text={tts_text[:30]!r}"
                    )
                elif slow:
                    _log(
                        f"[voice] gsv 语速异常 attempt={attempt}/{attempts} cps={cps:.2f} "
                        f"secs={secs:.2f} chars={len(tts_text)} text={tts_text[:40]!r}"
                    )
                elif over_mood:
                    _log(
                        f"[voice] gsv 情绪档过长 attempt={attempt}/{attempts} mood={mood} secs={secs:.2f} "
                        f"> {mood_max:.1f}s chars={len(tts_text)}"
                    )
                elif not (0.2 <= secs <= limit):
                    _log(
                        f"[voice] gsv 时长异常 attempt={attempt}/{attempts} secs={secs:.2f} "
                        f"limit={limit:.2f} chars={len(tts_text)} text={tts_text[:40]!r}"
                    )
                else:
                    _log(
                        f"[voice] gsv 爆音异常 attempt={attempt}/{attempts} burst={burst:.2f}% "
                        f"> {burst_max}% secs={secs:.2f} chars={len(tts_text)}"
                    )
        finally:
            _GSV_LOCK.release()
        if not wav_out and clean:
            # 时长都正常、只是爆音：取爆音最少的一次，别回退到 edge 音色
            wav_out = clean
            _log(f"[voice] gsv 用爆音最少的一次兜底 burst={clean_burst:.2f}% secs={clean_secs:.2f}")
        if not wav_out:
            # 先试「同一个人」的另一个参考音（同一副嗓子，换一段 prompt 重来）
            sibling = CFG.get("gsv_fallback_sibling") or "mingqiannailv"
            alt = (CFG.get("gsv_voices") or {}).get(sibling)
            if alt and voice != sibling:
                spec2 = dict(alt)
                spec2.pop("refs", None)
                try:
                    cand = _gsv_convert(tts_text, spec2)
                    if pro_on:
                        cand = _prosody_wav(cand)
                    secs2 = _wav_seconds(cand)
                    lvl2, pk2 = _wav_level(cand)
                    good = 0.2 <= secs2 <= limit * 1.5 and (lvl2 < 0 or (lvl2 >= 0.012 and pk2 >= 0.06))
                    if good:
                        wav_out = cand
                        _log(f"[voice] gsv 换同源音色 {sibling} 兜底 secs={secs2:.2f} rms={lvl2:.4f}")
                except Exception as e:  # noqa: BLE001
                    _log(f"[voice] gsv 同源兜底失败 ({e})")
        if not wav_out:
            # 仍不行才回退 edge 音色，保证「一定有声音」
            if best and 0.2 <= best_secs <= limit * 1.5:
                lvl_b, pk_b = _wav_level(best)
                if lvl_b < 0 or (lvl_b >= 0.012 and pk_b >= 0.06 and best_secs >= max(0.45, len(tts_text) * 0.14)):
                    wav_out = best
                    _log(f"[voice] gsv 用最短一次兜底 secs={best_secs:.2f} rms={lvl_b:.4f}")
            if not wav_out:
                fb = spec.get("fallback_voice") or CFG.get("gsv_fallback_voice") or CFG["default_voice"]
                _log(f"[voice] gsv 连续 {attempts} 次异常，回退 edge 音色 voice={fb} best={best_secs:.2f}s")
                _note_action(
                    "voice-fallback",
                    f"GPT-SoVITS 连续 {attempts} 次异常（时长/静音/爆音），这条回退成 {fb}；chars={len(tts_text)} best={best_secs:.2f}s",
                    False,
                )
                try:
                    data = asyncio.run(_edge_synth(tts_text, fb, rate, pitch, volume))
                except Exception as e:  # noqa: BLE001
                    _log(f"[voice] gsv 回退 edge 失败 ({e})，改用子进程")
                    data = _synth_subprocess(tts_text, fb, rate, pitch, volume)
                tmp = out_path + ".part"
                with open(tmp, "wb") as f:
                    f.write(data)
                os.replace(tmp, out_path)
                return data, out_path
        _log(f"[voice] gsv clone voice={voice} wav_bytes={len(wav_out)} secs={_wav_seconds(wav_out):.2f}")
        data = _ffmpeg_bytes(wav_out, "wav", "mp3")
        tmp = out_path + ".part"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, out_path)
        return data, out_path
    if isinstance(f5_map, dict) and voice in f5_map:
        spec = dict(f5_map.get(voice) or {})
        key = _sha1(f"f5|{voice}|{json.dumps(spec, sort_keys=True)}|{text}")
        out_path = os.path.join(CFG["cache_dir"], f"{key}.mp3")
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            with open(out_path, "rb") as f:
                return f.read(), out_path
        wav_out = _f5_convert(text, spec)
        _log(f"[voice] f5 clone voice={voice} spec={spec} wav_bytes={len(wav_out)}")
        data = _ffmpeg_bytes(wav_out, "wav", "mp3")
        tmp = out_path + ".part"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, out_path)
        return data, out_path

    if isinstance(rvc_map, dict) and voice in rvc_map:
        spec = dict(rvc_map.get(voice) or {})
        base_voice = spec.get("base") or CFG["default_voice"]
        params = {k: spec[k] for k in ("f0up_key", "index_rate", "protect", "f0method", "rms_mix_rate", "resample_sr") if k in spec}
        key = _sha1(f"rvc|{voice}|{json.dumps(params, sort_keys=True)}|{base_voice}|{rate}|{pitch}|{volume}|{text}")
        out_path = os.path.join(CFG["cache_dir"], f"{key}.mp3")
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            with open(out_path, "rb") as f:
                return f.read(), out_path
        try:
            seed_mp3 = asyncio.run(_edge_synth(text, base_voice, rate, pitch, volume))
        except Exception as e:  # noqa: BLE001
            _log(f"[voice] rvc seed synth failed ({e}); falling back to subprocess")
            seed_mp3 = _synth_subprocess(text, base_voice, rate, pitch, volume)
        if not seed_mp3:
            raise RuntimeError("TTS 返回空音频（RVC 种子）")
        wav_in = _ffmpeg_bytes(seed_mp3, "mp3", "wav")
        _log(f"[voice] rvc convert voice={voice} params={params} wav_bytes={len(wav_in)}")
        wav_out = _rvc_convert(wav_in, params)
        data = _ffmpeg_bytes(wav_out, "wav", "mp3")
        tmp = out_path + ".part"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, out_path)
        return data, out_path

    key = _sha1(f"{CFG['engine']}|{voice}|{rate}|{pitch}|{volume}|{text}")
    out_path = os.path.join(CFG["cache_dir"], f"{key}.{CFG['format']}")
    if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
        with open(out_path, "rb") as f:
            return f.read(), out_path

    try:
        data = asyncio.run(_edge_synth(text, voice, rate, pitch, volume))
    except Exception as e:  # noqa: BLE001
        _log(f"[voice] in-process synth failed ({e}); falling back to subprocess")
        data = _synth_subprocess(text, voice, rate, pitch, volume)
    if not data:
        raise RuntimeError("TTS 返回空音频")
    tmp = out_path + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, out_path)
    return data, out_path


def _synth_subprocess(text: str, voice: str, rate: str, pitch: str, volume: str) -> bytes:
    """兜底：用独立进程跑 CLI 合成（常驻进程里的 edge-tts 偶发拿不到音频）。"""
    import subprocess
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tf:
        out = tf.name
    exe = os.path.join(HERE, ".venv", "Scripts", "python.exe")
    if not os.path.exists(exe):
        exe = sys.executable
    cmd = [exe, os.path.abspath(__file__), "--raw", "--text", text, "--out=" + out, "--voice", voice]
    if rate:
        cmd += ["--rate=" + rate]
    if pitch:
        cmd += ["--pitch=" + pitch]
    if volume:
        cmd += ["--volume=" + volume]
    try:
        flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
        errfile = os.path.join(CFG["cache_dir"], "_subproc_err.txt")
        with open(errfile, "wb") as ef:
            proc = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=ef,
                timeout=120,
                creationflags=flags,
                cwd=HERE,
            )
        if proc.returncode != 0:
            raise RuntimeError("子进程合成失败 rc=%s" % proc.returncode)
        with open(out, "rb") as f:
            data = f.read()
        if not data:
            raise RuntimeError("子进程没有产出音频")
        return data
    finally:
        try:
            os.remove(out)
        except OSError:
            pass


async def _voices() -> list[dict]:
    import edge_tts

    items = await edge_tts.list_voices()
    out = []
    for v in items:
        if not v.get("Locale", "").startswith("zh"):
            continue
        out.append(
            {
                "id": v["ShortName"],
                "name": v.get("FriendlyName", ""),
                "gender": v.get("Gender", ""),
                "locale": v.get("Locale", ""),
            }
        )
    return out


VOICE_CACHE: dict | None = None
VOICE_CACHE_AT = 0.0


class Handler(BaseHTTPRequestHandler):
    server_version = "VoiceServer/1.0"

    def log_message(self, fmt, *args):  # noqa: A003
        sys.stderr.write("[voice] " + (fmt % args) + "\n")

    def _json(self, code: int, obj: dict):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _audio(self, data: bytes, extra: dict | None = None):
        self.send_response(200)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra or {}).items():
            self.send_header(k, str(v))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        global VOICE_CACHE, VOICE_CACHE_AT
        path = self.path.split("?")[0]
        if path == "/health":
            return self._json(200, {
                "ok": True,
                "engine": CFG["engine"],
                "default_voice": CFG["default_voice"],
                "rate": CFG["rate"],
                "pitch": CFG["pitch"],
                "cache_dir": CFG["cache_dir"],
                "rvc_url": CFG.get("rvc_url"),
                "rvc_voices": sorted((CFG.get("rvc_voices") or {}).keys()),
            "f5_voices": sorted((CFG.get("f5_voices") or {}).keys()),
            "gsv_voices": sorted((CFG.get("gsv_voices") or {}).keys()),
                "gsv_moods": _all_moods(),
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            })
        if path == "/voices":
            if VOICE_CACHE is None or time.time() - VOICE_CACHE_AT > 3600:
                VOICE_CACHE = asyncio.run(_voices())
                VOICE_CACHE_AT = time.time()
            return self._json(200, {"ok": True, "count": len(VOICE_CACHE), "voices": VOICE_CACHE})
        return self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self):  # noqa: N802
        path = self.path.split("?")[0]
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            return self._json(400, {"ok": False, "error": "bad json"})

        if path in ("/tts", "/v1/audio/speech"):
            text = payload.get("text") or payload.get("input") or ""
            voice = payload.get("voice")
            if path == "/v1/audio/speech" and voice in (None, "", "alloy", "nova", "shimmer", "echo", "fable", "onyx"):
                voice = CFG["default_voice"]
            try:
                _log(f"[voice] tts voice={voice or CFG['default_voice']} chars={len(text)}")
                data, out_path = synth(
                    text,
                    voice=voice,
                    rate=payload.get("rate"),
                    pitch=payload.get("pitch"),
                    volume=payload.get("volume"),
                    mood=payload.get("mood"),
                )
            except Exception as e:
                self.log_message("synth failed: %s", e)
                return self._json(500, {"ok": False, "error": str(e)})
            return self._audio(data, {"X-Voice": voice or CFG["default_voice"], "X-Cache-File": out_path})
        return self._json(404, {"ok": False, "error": "not found"})


def _log(text: str) -> None:
    """写日志文件（不用 dup2，避免脱离控制台时句柄异常）。"""
    try:
        log_dir = os.path.abspath(os.path.join(HERE, "..", "..", ".cache", "voice-run"))
        os.makedirs(log_dir, exist_ok=True)
        with open(os.path.join(log_dir, "server.log"), "a", encoding="utf-8") as f:
            f.write(text.rstrip("\n") + "\n")
    except Exception:
        pass
    try:
        print(text, flush=True)
    except Exception:
        pass


def _cache_prune(max_files: int = 2000, max_age_days: int = 30) -> None:
    """清缓存：30 天没再用过的删掉，总数超过上限时按 mtime 删最旧的。"""
    d = CFG["cache_dir"]
    removed = 0
    try:
        files = [os.path.join(d, n) for n in os.listdir(d) if n.endswith((".mp3", ".wav"))]
        now = time.time()
        for p in files:
            try:
                if now - os.path.getmtime(p) > max_age_days * 86400:
                    os.remove(p)
                    removed += 1
            except OSError:
                pass
        alive = [p for p in files if os.path.exists(p)]
        if len(alive) > max_files:
            alive.sort(key=os.path.getmtime)
            for p in alive[: len(alive) - max_files]:
                try:
                    os.remove(p)
                    removed += 1
                except OSError:
                    pass
        if removed:
            _log(f"[voice] 缓存清理 {removed} 个（上限 {max_files} 个 / {max_age_days} 天）")
    except Exception:  # noqa: BLE001
        traceback.print_exc()


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    import socket

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _warmup_loop() -> None:
    """后台预热 + 定期清缓存：先合成一句把模型/CUDA 初始化，用户第一条就不用等 20 秒。"""
    gsv_map = CFG.get("gsv_voices") or {}
    voice = sorted(gsv_map.keys())[0] if gsv_map else None
    if voice:
        for _ in range(120):
            if _port_open("127.0.0.1", 9880):
                break
            time.sleep(1)
        else:
            _log("[voice] 预热跳过：9880 没有起来")
        if _port_open("127.0.0.1", 9880):
            t0 = time.time()
            try:
                synth("嗯，我在。", voice)
                _log(f"[voice] 预热完成 voice={voice} 耗时 {time.time() - t0:.1f}s")
            except Exception as e:  # noqa: BLE001
                _log(f"[voice] 预热失败（{e}），第一条语音会慢一些")
    while True:
        _cache_prune()
        time.sleep(6 * 3600)


def serve():
    host, port = CFG["host"], int(CFG["port"])
    httpd = ThreadingHTTPServer((host, port), Handler)
    _log(f"[voice] listening http://{host}:{port}  engine={CFG['engine']} default_voice={CFG['default_voice']}")
    threading.Thread(target=_warmup_loop, daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


def main():
    ap = argparse.ArgumentParser(description="本地语音合成服务 / CLI")
    ap.add_argument("--text", help="直接合成一句话（CLI 模式）")
    ap.add_argument("--out", help="CLI 输出文件路径")
    ap.add_argument("--voice")
    ap.add_argument("--rate")
    ap.add_argument("--pitch")
    ap.add_argument("--volume")
    ap.add_argument("--serve", action="store_true", help="常驻 HTTP 服务")
    ap.add_argument("--raw", action="store_true", help="（内部）只做一次 edge 合成，不兜底")
    args = ap.parse_args()

    if args.text and args.raw:
        data = asyncio.run(_edge_synth(args.text, args.voice, args.rate or CFG["rate"], args.pitch or CFG["pitch"], args.volume or CFG["volume"]))
        out = args.out or os.path.join(CFG["cache_dir"], _sha1(args.text) + ".mp3")
        with open(out, "wb") as f:
            f.write(data)
        print(json.dumps({"ok": True, "out": os.path.abspath(out), "bytes": len(data)}, ensure_ascii=False))
        return
    if args.text:
        data, path = synth(args.text, args.voice, args.rate, args.pitch, args.volume)
        out = args.out or path
        if args.out:
            with open(out, "wb") as f:
                f.write(data)
        print(json.dumps({"ok": True, "out": os.path.abspath(out), "bytes": len(data)}, ensure_ascii=False))
        return
    serve()


if __name__ == "__main__":
    main()
