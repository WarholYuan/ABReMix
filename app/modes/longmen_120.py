"""龙门 xhs_lxkt 模式：zoompan + hue 动态网格底图 + 120:60 交织。

对应原版 tab17 "新120帧 — 龙门 xhs_lxkt 明文抓取还原"。
滤镜模板来源：feimao33-fresh-REPORT.md § 2.3.2 "tab17"。

核心特征：
- 网格底图用 zoompan 做动态缩放平移（正弦波参数驱动）
- hue 滤镜按时间旋转色相（彩虹循环效果）
- 主视频居中缩放叠加在动态底图上
- 最终 180fps（120:60 交织）
"""
from __future__ import annotations

import random
from pathlib import Path

from ..core.ffmpeg_resolve import ffmpeg_path
from ..core.probe import probe
from ..core.runner import run_ffmpeg


def longmen_120(
    main_video: str,
    output: str,
    *,
    duration: float | None = None,
    seed: int | None = None,
    grid_size: tuple[int, int] = (640, 360),
    zoom_speed: float = 0.005,
    amplitude_x: int = 50,
    amplitude_y: int = 50,
    period_x: int = 80,
    period_y: int = 60,
    hue_speed: float = 10.0,
    hue_base: int = 0,
) -> dict:
    """龙门 xhs_lxkt 模式：zoompan + hue 动态网格底图。

    Args:
        main_video: 主视频路径
        output: 输出文件路径
        duration: 输出时长（秒），None时取主视频时长
        seed: 随机种子（用于抖动参数）
        grid_size: 网格底图初始尺寸（默认 640x360）
        zoom_speed: 缩放速率（默认 0.005）
        amplitude_x: X轴振幅（默认 50px）
        amplitude_y: Y轴振幅（默认 50px）
        period_x: X轴周期（默认 80 帧）
        period_y: Y轴周期（默认 60 帧）
        hue_speed: 色相旋转速度（默认 10.0 度/秒）
        hue_base: 色相基准偏移（默认 0）

    Returns:
        元数据字典（分辨率、帧率、时长）
    """
    if seed is not None:
        random.seed(seed)
        # 参数抖动（原版注释："数值在抓取附近随机抖动"）
        zoom_speed += random.uniform(-0.002, 0.002)
        amplitude_x += random.randint(-10, 10)
        amplitude_y += random.randint(-10, 10)
        period_x += random.randint(-10, 10)
        period_y += random.randint(-10, 10)
        hue_speed += random.uniform(-2.0, 2.0)
        hue_base += random.randint(-30, 30)

    info = probe(main_video)
    w, h = info.width, info.height
    if duration is None:
        duration = info.duration


    # 网格底图尺寸与输出尺寸
    gw, gh = grid_size
    ow, oh = w, h

    # 构造 zoompan + hue 滤镜（原文模板）
    # [0:v]scale={gw}:{gh}:flags=lanczos,
    #      zoompan=z='min(zoom+{zs},2.0)'
    #             :x='iw/2-(iw/zoom/2)+{ax}*sin(on/{bx})'
    #             :y='ih/2-(ih/zoom/2)+{ay}*cos(on/{by})'
    #             :d=1:fps=60:s={ow}x{oh},
    #      hue=h='{hs}*t+{hb}',
    #      format=yuv420p[grid]
    filter_complex = (
        f"color=c=0x2E3440:s={gw}x{gh}:d={duration}:r=30[base];"
        f"[base]zoompan="
        f"z='min(zoom+{zoom_speed},2.0)'"
        f":x='iw/2-(iw/zoom/2)+{amplitude_x}*sin(on/{period_x})'"
        f":y='ih/2-(ih/zoom/2)+{amplitude_y}*cos(on/{period_y})'"
        f":d=1:fps=60:s={ow}x{oh},"
        f"hue=h='{hue_speed}*t+{hue_base}',"
        f"format=yuv420p[grid];"
        f"[1:v]scale={ow}:{oh}:force_original_aspect_ratio=decrease,"
        f"pad={ow}:{oh}:(ow-iw)/2:(oh-ih)/2:black,"
        f"setpts=PTS-STARTPTS,fps=120,format=yuv420p[main];"
        f"[grid]fps=60,format=yuv420p[grid60];"
        f"[main][grid60]interleave,format=yuv420p[v]"
    )

    cmd = [
        ffmpeg_path(), "-y",
        "-f", "lavfi", "-i", f"color=c=0x2E3440:s={gw}x{gh}:d={duration}:r=30",
        "-i", main_video,
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-map", "1:a?",
        "-c:v", "libx264", "-preset", "fast", "-profile:v", "main",
        "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-fps_mode", "passthrough",
        "-movflags", "+faststart",
        "-t", str(duration),
        output,
    ]

    result = run_ffmpeg(cmd, total_duration=duration, label="龙门120")

    out_info = probe(output)
    return {
        "width": out_info.width,
        "height": out_info.height,
        "fps": out_info.fps,
        "duration": out_info.duration,
        "frames": getattr(out_info, "nb_frames", None),
    }
