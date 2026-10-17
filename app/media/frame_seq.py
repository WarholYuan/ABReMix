"""帧序列通道:导出帧图 → 按规则交错编排 → 回拼成片。

来源:《肥猫 3.3 逆向分析报告》§5.1(新反转「拆帧图片序列逐帧交替,
用过的帧马上删」@0x4ae01e0)、§5.2(1B3A「跳过直接 interleave,改用图片
序列交错回退」@0x4a0e2b8);§8 的 temp/extracted_frames 残留目录与此互证。

「用过的帧马上删」的实现:编排时用 os.rename 把源帧**移动**进序列目录,
源目录随编排推进被掏空;回拼成功后整目录清理。
"""
from __future__ import annotations

import shutil
from pathlib import Path

from ..core.ffmpeg_resolve import ffmpeg_path
from ..core.runner import run_ffmpeg

_X264 = ["-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p"]


def extract_frames(video: str, out_dir: str, fps: float,
                   width: int | None = None, height: int | None = None,
                   label: str = "extract") -> list[Path]:
    """把视频按 fps 导出为 PNG 帧序列,返回排序后的文件列表。

    给出 width/height 时先 scale+pad 到目标画布(不足补黑边、过盈等比缩放)。
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    vf = f"fps={fps}"
    if width and height:
        vf += (
            f",scale={width}:{height}:force_original_aspect_ratio=decrease"
            f",pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
        )
    vf += ",format=rgb24"
    run_ffmpeg(
        [ffmpeg_path(), "-y", "-i", video, "-vf", vf,
         str(out / "f%06d.png")],
        label=label,
    )
    return sorted(out.glob("f*.png"))


def assemble_alternating(frames_a: list[Path], frames_b: list[Path],
                         seq_dir: str, pattern: list[int]) -> int:
    """按 pattern(如 [0,1] 交替、[0,1,1,1] 即 1B3A)把两轨帧移动编排进 seq_dir。

    pattern 元素 0 = 取 A 轨下一帧,1 = 取 B 轨下一帧;任一轨耗尽即停。
    返回编排出的总帧数。源帧文件被 move 走(“用过的帧马上删”)。
    """
    seq = Path(seq_dir)
    seq.mkdir(parents=True, exist_ok=True)
    ia = ib = out_idx = 0
    while True:
        progressed = False
        for src in pattern:
            if src == 0 and ia < len(frames_a):
                frames_a[ia].rename(seq / f"s{out_idx:06d}.png")
                ia += 1
                out_idx += 1
                progressed = True
            elif src == 1 and ib < len(frames_b):
                frames_b[ib].rename(seq / f"s{out_idx:06d}.png")
                ib += 1
                out_idx += 1
                progressed = True
        if not progressed:
            break
    return out_idx


def encode_sequence(seq_dir: str, fps: float, out: str,
                    audio_from: str | None = None,
                    duration: float | None = None,
                    label: str = "encode-seq") -> None:
    """把序列目录回拼为视频;audio_from 给出时把音轨取自该文件。"""
    cmd = [ffmpeg_path(), "-y", "-framerate", f"{fps}", "-i",
           str(Path(seq_dir) / "s%06d.png")]
    if audio_from:
        cmd += ["-i", audio_from]
    cmd += ["-map", "0:v"]
    if audio_from:
        cmd += ["-map", "1:a?"]
    cmd += [*_X264, "-profile:v", "high", "-bf", "0", "-crf", "18"]
    if audio_from:
        cmd += ["-c:a", "aac"]
    cmd += ["-movflags", "+faststart"]
    if duration:
        cmd += ["-t", f"{duration}"]
    cmd += [out]
    run_ffmpeg(cmd, total_duration=duration, label=label)


def extract_single_frame(video: str, out_png: str, time_offset: float = 0.0,
                         width: int | None = None, height: int | None = None,
                         label: str = "frame-grab") -> None:
    """抽取一帧为 PNG(闪帧取首帧、单帧AB、随机帧AB 用)。"""
    cmd = [ffmpeg_path(), "-y", "-ss", f"{time_offset}", "-i", video]
    vf = ""
    if width and height:
        vf = (f"scale={width}:{height}:force_original_aspect_ratio=disable,"
              f"setsar=1,")
    cmd += ["-vf", vf + "format=rgb24", "-frames:v", "1", out_png]
    run_ffmpeg(cmd, label=label)


def cleanup_dir(path: str | Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
