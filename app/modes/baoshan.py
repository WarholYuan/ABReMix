"""爆闪模式：colorchannelmixer 提亮 + overlay 全程叠加。

对应原版快手区 "老 爆闪" 功能。
滤镜模板来源：feimao33-fresh-REPORT.md § 2.3.2 "闪光/爆闪"。

核心特征：
- 主视频 60fps 作为基底
- 辅轨（闪光素材）用 colorchannelmixer 提亮（alpha 通道乘以 1.1）
- overlay 全程叠加，产生闪光效果
"""
from __future__ import annotations

from ..core.ffmpeg_resolve import ffmpeg_path
from ..core.probe import probe
from ..core.runner import run_ffmpeg


def baoshan(
    main_video: str,
    flash_video: str,
    output: str,
    *,
    duration: float | None = None,
    alpha_gain: float = 1.1,
    fps: int = 60,
) -> dict:
    """爆闪模式（闪光叠加）。

    Args:
        main_video: 主视频路径
        flash_video: 闪光素材路径（辅轨）
        output: 输出文件路径
        duration: 输出时长（秒），None时取主视频时长
        alpha_gain: alpha 通道增益（默认 1.1，提亮闪光）
        fps: 输出帧率（默认 60）

    Returns:
        元数据字典（分辨率、帧率、时长）
    """
    info = probe(main_video)
    if duration is None:
        duration = info.duration

    # 原文滤镜模板：
    # [0:v]setpts=PTS-STARTPTS,fps=60[base];
    # [1:v]setpts=PTS-STARTPTS,format=rgba,colorchannelmixer=aa={alpha_gain}[flash];
    # [base][flash]overlay[outv]
    filter_complex = (
        f"[0:v]setpts=PTS-STARTPTS,fps={fps}[base];"
        f"[1:v]setpts=PTS-STARTPTS,format=rgba,colorchannelmixer=aa={alpha_gain}[flash];"
        f"[base][flash]overlay[outv]"
    )

    cmd = [
        ffmpeg_path(), "-y",
        "-i", main_video,
        "-i", flash_video,
        "-filter_complex", filter_complex,
        "-map", "[outv]",
        "-map", "0:a?",
        "-c:v", "libx264", "-preset", "fast", "-profile:v", "main",
        "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-movflags", "+faststart",
        "-t", str(duration),
        output,
    ]

    result = run_ffmpeg(cmd, total_duration=duration, label="爆闪")

    out_info = probe(output)
    return {
        "width": out_info.width,
        "height": out_info.height,
        "fps": out_info.fps,
        "duration": out_info.duration,
        "frames": getattr(out_info, "nb_frames", None),
    }
