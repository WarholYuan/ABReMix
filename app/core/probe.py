"""ffprobe 封装:时长 / 宽高 / 帧率 / 编码信息。

对应方案 §3 的 core/probe.py。所有模式在组装流水线前都先 probe 主素材,
拿到 duration / width / height / fps 再填模板占位符。
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from fractions import Fraction

from .ffmpeg_resolve import ffprobe_path


@dataclass
class MediaInfo:
    path: str
    duration: float
    width: int
    height: int
    fps: float
    video_codec: str = ""
    has_audio: bool = False

    @property
    def is_portrait(self) -> bool:
        return self.height > self.width


def _frac_to_float(text: str | None) -> float:
    if not text:
        return 0.0
    try:
        return float(Fraction(text))
    except (ValueError, ZeroDivisionError):
        try:
            return float(text)
        except ValueError:
            return 0.0


def probe(path: str) -> MediaInfo:
    """对媒体文件跑 ffprobe 并返回结构化信息。失败时抛 RuntimeError。"""
    cmd = [
        ffprobe_path(),
        "-v", "error",
        "-show_entries",
        "stream=codec_type,codec_name,width,height,avg_frame_rate,r_frame_rate",
        "-show_entries", "format=duration",
        "-of", "json",
        path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe 失败({path}): {proc.stderr.strip()[:500]}")
    data = json.loads(proc.stdout)
    vstream = None
    has_audio = False
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video" and vstream is None:
            vstream = stream
        elif stream.get("codec_type") == "audio":
            has_audio = True
    if vstream is None:
        raise RuntimeError(f"未找到视频流: {path}")
    fps = _frac_to_float(vstream.get("avg_frame_rate")) or _frac_to_float(
        vstream.get("r_frame_rate")
    )
    duration = float(data.get("format", {}).get("duration") or 0.0)
    return MediaInfo(
        path=path,
        duration=duration,
        width=int(vstream.get("width") or 0),
        height=int(vstream.get("height") or 0),
        fps=fps,
        video_codec=vstream.get("codec_name", ""),
        has_audio=has_audio,
    )
