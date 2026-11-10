"""配音 + BGM：edge-tts 逐句合成，按时间轴摆位，BGM 自动闪避，loudnorm 收口。

对应之前手工验证过的 audio.ps1，逻辑一样，只是换成 Python 调 ffmpeg，参数好传。

关键点（踩过的坑都在这）：
  * 每条 TTS 首尾都有 0.4~0.6s 静音，必须 silenceremove 之后再量时长，
    否则会误判"语速太慢"而把声音 atempo 压成机器音；
  * sidechaincompress 在较短一路结束时就会截断，配音轨必须先 apad 到片长再 asplit；
  * 混流用 -c:v copy，视频不重编码。
"""

from __future__ import annotations

import json
import socket
import subprocess
import time
from pathlib import Path

from .paths import (
    FFMPEG, FFPROBE, GSV_PORT, GSV_START, POWERSHELL, VOICE_PY, VOICE_SERVER, gsv_voices,
)
from .spec import Spec

from . import sfx

TRIM = (
    "aformat=sample_rates=44100:channel_layouts=mono,"
    "silenceremove=start_periods=1:start_threshold=-45dB:"
    "stop_periods=-1:stop_duration=0.12:stop_threshold=-45dB"
)


class AudioError(RuntimeError):
    pass


def probe_duration(path: str | Path) -> float:
    out = subprocess.run(
        [str(FFPROBE), "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.6) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def prepare_voice(voice: str, fallback: str = "zh-CN-XiaoxiaoNeural") -> tuple[str, list[str]]:
    """把面板/配置里的音色解析成"现在真能用的那个"。

    自训练音色（apps/voice/config.json 的 gsv_voices，如明前奶绿）靠 GPT-SoVITS 的
    api_v2 出声，服务没起就先拉 start-gsv.ps1（它自己会等到端口就绪，最多 300s）。
    拉不起来就退回 edge 音色，并把原因作为 warning 交给上层展示——不要静默降级，
    否则用户会以为自己的音色没生效。
    """
    warns: list[str] = []
    voices = gsv_voices()
    if voice not in voices:
        return voice, warns
    if _port_open(GSV_PORT):
        return voice, warns
    if not Path(GSV_START).exists():
        warns.append(f"音色「{voice}」需要 GPT-SoVITS，但找不到 {GSV_START}，已退回 {fallback}")
        return fallback, warns
    try:
        subprocess.Popen(
            [str(POWERSHELL), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(GSV_START)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as exc:
        warns.append(f"拉起 GPT-SoVITS 失败（{exc}），已退回 {fallback}")
        return fallback, warns
    deadline = time.time() + 300
    while time.time() < deadline:
        if _port_open(GSV_PORT):
            return voice, warns
        time.sleep(2)
    warns.append(f"GPT-SoVITS 300 秒内没起来（{GSV_PORT}），本次改用 {fallback}")
    return fallback, warns


def tts_line(text: str, out_mp3: str | Path, voice: str, rate: str, pitch: str) -> None:
    """调 apps/voice 的 CLI 模式（edge-tts 或 GPT-SoVITS 克隆音色，由 --voice 决定）。"""
    voice = str(voice or "").strip()
    timeout = 300 if voice in gsv_voices() else 120
    cmd = [
        str(VOICE_PY), str(VOICE_SERVER),
        f"--text={text}", f"--out={out_mp3}",
        f"--voice={voice}", f"--rate={rate}", f"--pitch={pitch}",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if r.returncode != 0 or not Path(out_mp3).exists():
        raise AudioError(f"配音失败「{text}」：{(r.stderr or r.stdout or '')[-300:]}")


def prep_clip(mp3: Path, wav: Path) -> float:
    r = subprocess.run(
        [str(FFMPEG), "-y", "-v", "error", "-i", str(mp3), "-af", TRIM,
         "-ac", "1", "-ar", "44100", str(wav)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if r.returncode != 0 or not wav.exists():
        raise AudioError(f"裁静音失败：{wav.name} {r.stderr[-200:]}")
    return probe_duration(wav)


def mix(
    video: Path,
    clips: list[dict],
    out: Path,
    spec: Spec,
    progress=None,
) -> Path:
    """clips: [{text, at, wav, dur}]，按 spec 的 BGM/响度参数混流。"""
    video = Path(video)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    vid_dur = probe_duration(video)
    if vid_dur <= 0:
        raise AudioError("读不到视频时长")

    parts: list[str] = []
    mix_ins: list[str] = []
    # 没有配音（--no-audio / 全部配音失败）时也要能出 BGM 和音效：补一路静音当人声床
    sources = clips or [
        {"i": 0, "at": 0.0, "dur": vid_dur, "slot": vid_dur, "wav": None, "silent": True}
    ]
    for c in sources:
        idx = c["i"] + 1  # 输入 0 是视频
        if c.get("silent"):
            parts.append(
                f"[{idx}:a]aformat=sample_rates=44100:channel_layouts=mono,"
                f"apad=whole_dur={vid_dur:.3f}[a0]"
            )
            mix_ins.append("[a0]")
            continue
        tempo = ""
        if c["dur"] > c["slot"]:
            factor = min(1.6, c["dur"] / max(0.05, c["slot"] * 0.95))
            if factor > 1.02:
                tempo = f"atempo={factor:.3f},"
        ms = int(round(c["at"] * 1000))
        parts.append(
            f"[{idx}:a]aformat=sample_rates=44100:channel_layouts=mono,{tempo}"
            f"aresample=44100,adelay={ms}:all=1[a{c['i']}]"
        )
        mix_ins.append(f"[a{c['i']}]")
    if len(mix_ins) > 1:
        parts.append("".join(mix_ins) + f"amix=inputs={len(mix_ins)}:normalize=0:duration=longest[voice]")
    else:
        parts.append(f"{mix_ins[0]}anull[voice]")

    want_bgm = bool(spec.bgm_enabled) and bool(spec.bgm) and Path(spec.bgm).exists()
    bgm_idx = max(1, len(clips)) + 1
    if want_bgm:
        bgm_end = spec.bgm_start + vid_dur
        fade_out = max(0.1, vid_dur - 1.2)
        parts.append(
            f"[{bgm_idx}:a]atrim=start={spec.bgm_start:.3f}:end={bgm_end:.3f},"
            f"asetpts=PTS-STARTPTS,volume={spec.bgm_volume},"
            f"afade=t=in:st=0:d=0.35,afade=t=out:st={fade_out:.3f}:d=1.2,"
            f"aformat=sample_rates=44100:channel_layouts=stereo[bgm]"
        )
        parts.append(f"[voice]apad=whole_dur={vid_dur:.3f}[padv]")
        parts.append("[padv]asplit=2[vmain][vkey]")
        parts.append("[bgm][vkey]sidechaincompress=threshold=0.05:ratio=6:attack=15:release=320:makeup=1[duck]")
        parts.append("[vmain]aformat=channel_layouts=stereo[vst]")
        # 人声 + 被闪避的 BGM = 底床（音效稍后再叠，不参与闪避）
        parts.append("[vst][duck]amix=inputs=2:normalize=0:duration=longest[bed]")
    else:
        parts.append(f"[voice]apad=whole_dur={vid_dur:.3f},aformat=channel_layouts=stereo[bed]")

    # ---- 音效轨：每条事件一路输入，各自 adelay 后合成，最后整体压音量 ----
    sfx_events: list[dict] = []
    sfx_paths: list[Path] = []
    if getattr(spec, "sfx_enabled", False):
        try:
            events = sfx.events_for(spec)
        except Exception:  # 音效不该拖垮整条片子
            events = []
        for e in events:
            try:
                p = sfx.path_for(e["name"])
            except Exception:
                continue
            if Path(p).exists():
                sfx_events.append(e)
                sfx_paths.append(Path(p))

    if sfx_events:
        base_idx = bgm_idx + (1 if want_bgm else 0)
        ins = []
        for i, (e, p) in enumerate(zip(sfx_events, sfx_paths)):
            idx = base_idx + i
            ms = int(round(max(0.0, e["at"]) * 1000))
            lin = 10 ** (float(e.get("gain_db", 0.0)) / 20.0)
            parts.append(
                f"[{idx}:a]aformat=sample_rates=48000:channel_layouts=mono,"
                f"volume={lin:.4f},adelay={ms}:all=1[s{i}]"
            )
            ins.append(f"[s{i}]")
        parts.append("".join(ins) + f"amix=inputs={len(ins)}:normalize=0:duration=longest[sfxmix]")
        parts.append(
            f"[sfxmix]highpass=90,volume={spec.sfx_volume},"
            f"aformat=channel_layouts=stereo[sfx]"
        )
        parts.append("[bed][sfx]amix=inputs=2:normalize=0:duration=longest[premix]")
    else:
        parts.append("[bed]anull[premix]")

    parts.append(f"[premix]loudnorm=I={spec.target_lufs}:TP=-1.5:LRA=11,aresample=48000[outa]")

    tmp_wav = out.with_name(out.stem + "_mix.wav")
    cmd = [str(FFMPEG), "-y", "-v", "error", "-i", str(video)]
    for c in clips:
        cmd += ["-i", str(c["wav"])]
    if not clips:
        # 无声人声床：无限长的静音源，靠后面的 -t 截断
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=mono:sample_rate=44100"]
    if want_bgm:
        cmd += ["-i", spec.bgm]
    for p in sfx_paths:
        cmd += ["-i", str(p)]
    cmd += ["-filter_complex", ";".join(parts), "-map", "[outa]",
            "-ac", "2", "-ar", "48000", "-t", f"{vid_dur:.3f}", str(tmp_wav)]
    if progress:
        progress("混音")
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    if r.returncode != 0 or not tmp_wav.exists():
        raise AudioError("ffmpeg 混音失败：" + (r.stderr or "")[-400:])

    cmd = [
        str(FFMPEG), "-y", "-v", "error", "-i", str(video), "-i", str(tmp_wav),
        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
        "-movflags", "+faststart", "-t", f"{vid_dur:.3f}", str(out),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    try:
        tmp_wav.unlink(missing_ok=True)
    except OSError:
        pass
    if r.returncode != 0 or not out.exists():
        raise AudioError("ffmpeg 混流失败：" + (r.stderr or "")[-400:])
    return out


def probe_streams(path: str | Path) -> dict:
    r = subprocess.run(
        [str(FFPROBE), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    try:
        return json.loads(r.stdout or "{}")
    except ValueError:
        return {}
