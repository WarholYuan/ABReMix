"""小红书各模式的公共件:交织骨架运行器与通用编码参数。

编码参数依据:《肥猫 3.3 逆向分析报告》§5.1 纯AB「编码 CPU libx264 CRF18 /
GPU h264_nvenc cq18」与 §4 BL 家族的 profile high / -bf 0 口径;
统一走 CPU 路径(GPU 择优曾于 2026-10-14 短暂加入,同日按
用户决定取消——其机器均为苹果电脑、无 NVIDIA 显卡)。
"""
from __future__ import annotations

from pathlib import Path

from ..core.ffmpeg_resolve import ffmpeg_path
from ..core.probe import MediaInfo
from ..core.runner import run_ffmpeg
from ..core.templates import build_interleave_filter

# 交织/合成类模式的统一编码尾(CRF18 与原版纯AB/BL 口径一致)
ENCODE_X264_CRF18 = [
    "-c:v", "libx264", "-preset", "fast", "-profile:v", "high",
    "-bf", "0", "-crf", "18", "-pix_fmt", "yuv420p",
]
AUDIO_AAC = ["-c:a", "aac"]


def make_temp_dir(out_path: str) -> Path:
    """在输出文件旁建 temp/ 工作目录(与原版 {output}/temp 习惯一致)。"""
    tmp = Path(out_path).parent / "temp"
    tmp.mkdir(parents=True, exist_ok=True)
    return tmp


def run_interleave(main: str, aux: str, out: str, variant: str,
                   main_info: MediaInfo, aux_loop: bool = True,
                   label: str = "") -> None:
    """交织骨架家族的统一运行器。

    输入 0 = 主轨,输入 1 = 辅轨;滤镜串由 core.templates 的变体参数表组装
    (有原文的变体逐字使用原文)。音轨取主轨,-t 以主轨时长为准。
    """
    fc = build_interleave_filter(variant)
    cmd = [ffmpeg_path(), "-y", "-i", main]
    if aux_loop:
        cmd += ["-stream_loop", "-1"]
    cmd += ["-i", aux, "-filter_complex", fc,
            "-map", "[v]", "-map", "0:a?",
            *ENCODE_X264_CRF18, *AUDIO_AAC,
            # 交织图的帧率元数据与实际帧流不一致,ffmpeg 8 默认 fps_mode(CFR)
            # 会按元数据丢帧(实测 900 帧被丢到 835);passthrough 全量保留。
            "-fps_mode", "passthrough",
            "-movflags", "+faststart",
            "-t", f"{main_info.duration}", out]
    run_ffmpeg(cmd, total_duration=main_info.duration,
               label=label or f"interleave-{variant}")


def canvas_for(info: MediaInfo) -> tuple[int, int]:
    """反转AB 家族的画布规则(报告 §5.1:横 1280×720 / 竖 720×1280)。"""
    return (720, 1280) if info.is_portrait else (1280, 720)
