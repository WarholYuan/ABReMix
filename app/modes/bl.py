"""BL-1 / BL-2 流水线(方案 §4 参数冻结区)。

四步模板全部逐字取自《肥猫 3.3 逆向分析报告》§4(偏移 @0x4a1b406 /
@0x4a1b5dd / @0x4a1b829 / @0x4a1ba50 / @0x4a1bbf2),存在
core/templates.py 中,本模块只负责按顺序渲染执行 + Step 4 后把
meta_temp.mkv 落到最终文件名(原版 Step 4 输出即在 temp/,后续处理
未在字符串中明示;复刻选择把重写元数据后的文件移为最终成片)。
本轮范围以小红书为主,BL 为骨架样例一并提供(CLI 子命令 bl-1 / bl-2)。
"""
from __future__ import annotations

import shutil
from pathlib import Path

from ..core.ffmpeg_resolve import ffmpeg_path
from ..core.probe import probe
from ..core.runner import run_ffmpeg
from ..core.templates import (
    BL1_STEP2, BL2_STEP2, BL_STEP1, BL_STEP3, BL_STEP4,
    render_argv, time_tag_now,
)


def run_bl(main: str, bg: str, out_dir: str, mode: str = "BL-1",
           artist: str = "", title: str = "", date: str = "") -> str:
    """跑 BL-1 或 BL-2 全流水线,返回最终成片路径。"""
    info = probe(main)
    out = Path(out_dir)
    (out / "temp").mkdir(parents=True, exist_ok=True)
    filename = Path(main).stem
    tag = time_tag_now()
    values = dict(
        ffmpeg_path=ffmpeg_path(),
        input_path=str(Path(main).resolve()),
        bg_video_path=str(Path(bg).resolve()),
        output_path=str(out.resolve()),
        duration=f"{info.duration}",
        width=info.width,
        height=info.height,
        filename=filename,
        time_tag=tag,
        mode=mode,
        artist=artist or filename,
        title=title or filename,
        date=date,
    )
    run_ffmpeg(render_argv(BL_STEP1, **values),
               total_duration=info.duration, label=f"{mode}-step1")
    step2_tpl = BL1_STEP2 if mode == "BL-1" else BL2_STEP2
    run_ffmpeg(render_argv(step2_tpl, **values),
               total_duration=info.duration, label=f"{mode}-step2")
    run_ffmpeg(render_argv(BL_STEP3, **values),
               total_duration=info.duration, label=f"{mode}-step3")
    run_ffmpeg(render_argv(BL_STEP4, **values), label=f"{mode}-step4")
    final = out / f"{filename}_{mode}_{tag}.mkv"
    shutil.move(str(out / "temp" / "meta_temp.mkv"), str(final))
    return str(final)
