"""小红书·新区模式。

参数来源:《肥猫 3.3 逆向分析报告》§5.1(各模式要点)、§5.6(grid/life 原文);
方案 §5.1。每个函数头部标注来源与复刻组装说明。
"""
from __future__ import annotations

import random
from pathlib import Path

from ..core.ffmpeg_resolve import ffmpeg_path
from ..core.probe import probe
from ..core.runner import run_ffmpeg
from ..core.templates import FILTER_LIFE_BLEND, render
from ..media import background_gen, frame_seq
from .common import (
    AUDIO_AAC, ENCODE_X264_CRF18, canvas_for, make_temp_dir, run_interleave,
)


def interleave_105_35(main: str, aux: str, out: str) -> str:
    """105:35 交织变体。
    来源:报告 §5.1(@0x49fac10)——滤镜串逐字原文,见 templates.FILTER_105_35,
    主轨 fps=105、辅轨 fps=35、interleave 后 select='not(eq(n,1))'。
    """
    info = probe(main)
    run_interleave(main, aux, out, "105:35", info, label="105-35")
    return out


def pure_ab(main: str, aux: str, out: str) -> str:
    """新 纯AB。
    来源:报告 §5.1「XHS 新区 · 6. 纯AB」——主片 + 循环辅助层,90fps /
    自动匹配主片画幅(scale2ref)/ 隔帧叠加;编码 CPU libx264 CRF18。
    组装:骨架双轨各 45fps 交织(隔帧即辅帧),重编号到 90fps 时间轴。
    """
    info = probe(main)
    run_interleave(main, aux, out, "pure90", info, label="pure-ab")
    return out


def new_120(main: str, out: str, seed: int = 0) -> str:
    """新120帧。
    来源:报告 §5.1「新120帧 — 龙门 xhs_lxkt 明文抓取还原」——流水线五段:
    探测源片 → 生成底图 shape.png → 生成辅轨 → 交错合成 → 封装成片;
    合成段为 120:60 变体(主 120fps / 辅 60fps + tpad clone,剔 n=1 帧)。
    底图取源片首帧(其确切生成方式未在字符串中展开,此为复刻选择);
    辅轨 = 底图静帧循环成轨。
    """
    info = probe(main)
    tmp = make_temp_dir(out)
    shape_png = str(tmp / "shape.png")
    frame_seq.extract_single_frame(main, shape_png, 0.0, label="new120-shape")
    aux_track = str(tmp / "new120_aux.mp4")
    run_ffmpeg(
        [ffmpeg_path(), "-y", "-loop", "1", "-i", shape_png,
         "-vf", "fps=60,format=yuv420p",
         "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
         "-t", f"{info.duration}", aux_track],
        total_duration=info.duration, label="new120-aux",
    )
    composite = str(tmp / "new120_composite.mp4")
    run_interleave(main, aux_track, composite, "120:60", info,
                   aux_loop=False, label="new120-composite")
    # 第五段:封装成片(无损重封装 + faststart)
    run_ffmpeg(
        [ffmpeg_path(), "-y", "-i", composite, "-c", "copy",
         "-movflags", "+faststart", out],
        label="new120-package",
    )
    return out


def fanzhuan_ab(main: str, aux: str, out: str) -> str:
    """新 反转AB(滤镜版)。
    来源:报告 §5.1——主+辅各 60fps 交错成 120fps(偶=A、奇=B),
    横 1280×720 / 竖 720×1280。组装:先把双轨统一到方向画布,再走骨架交织。
    """
    info = probe(main)
    w, h = canvas_for(info)
    tmp = make_temp_dir(out)
    prep_main = str(tmp / "fz_main.mp4")
    prep_aux = str(tmp / "fz_aux.mp4")
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
          f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps=60,format=yuv420p")
    run_ffmpeg(
        [ffmpeg_path(), "-y", "-i", main, "-vf", vf,
         "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-t", f"{info.duration}", prep_main],
        total_duration=info.duration, label="fanzhuan-prep-main",
    )
    run_ffmpeg(
        [ffmpeg_path(), "-y", "-stream_loop", "-1", "-i", aux, "-vf", vf,
         "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
         "-an", "-t", f"{info.duration}", prep_aux],
        total_duration=info.duration, label="fanzhuan-prep-aux",
    )
    run_interleave(prep_main, prep_aux, out, "fanzhuan120",
                   probe(prep_main), aux_loop=False, label="fanzhuan-ab")
    return out


def xin_fanzhuan(main: str, aux: str, out: str) -> str:
    """新反转(帧序列版)。
    来源:报告 §5.1——走拆帧图片序列逐帧交替(不用 interleave 滤镜),
    “用过的帧马上删”,输出后缀 _新反转_。双轨各 60fps 抽帧、严格交替
    编排、120fps 回拼,音轨取主视频。
    """
    info = probe(main)
    w, h = canvas_for(info)
    tmp = make_temp_dir(out)
    frames_a = frame_seq.extract_frames(main, str(tmp / "fz_seq_a"), 60, w, h,
                                        label="xin-fanzhuan-extract-a")
    frames_b = frame_seq.extract_frames(aux, str(tmp / "fz_seq_b"), 60, w, h,
                                        label="xin-fanzhuan-extract-b")
    seq_dir = str(tmp / "fz_seq_out")
    frame_seq.assemble_alternating(frames_a, frames_b, seq_dir, [0, 1])
    frame_seq.encode_sequence(seq_dir, 120, out, audio_from=main,
                              duration=info.duration, label="xin-fanzhuan")
    frame_seq.cleanup_dir(tmp / "fz_seq_a")
    frame_seq.cleanup_dir(tmp / "fz_seq_b")
    frame_seq.cleanup_dir(seq_dir)
    return out


# 帧间隔特效链预设(报告 §5.1:旋转/变色/镜像/模糊/闪烁/边缘检测 + 自定义)
EFFECT_PRESETS = {
    "none": "",
    "rotate": "rotate=angle=0.06:ow=iw:oh=ih:fillcolor=black",
    "color": "hue=h=45:s=1.2",
    "mirror": "hflip",
    "blur": "boxblur=luma_radius=6:luma_power=1",
    "blink": "eq=brightness='0.15*sin(2*PI*t*3)':eval=frame",
    "edge": "edgedetect=low=0.1:high=0.4",
}


def frame_gap(main: str, aux: str, out: str, period: int = 3, count: int = 2,
              effect: str = "none", custom_filter: str = "") -> str:
    """新 帧间隔。
    来源:报告 §5.1——周期叠帧:每 period 帧叠加前 count 帧(说明文案
    「每 3 帧叠 2 帧 = 叠叠空」),可配特效链预设 + 自定义滤镜。
    组装:辅轨缩放到主画幅、挂特效链后,用 overlay 的 timeline 表达式
    enable='lt(mod(n,period),count)' 做周期叠加。
    """
    info = probe(main)
    eff = custom_filter or EFFECT_PRESETS[effect]
    aux_chain = (
        f"scale={info.width}:{info.height}:force_original_aspect_ratio=disable,"
        f"setsar=1,fps={info.fps:g}"
    )
    if eff:
        aux_chain += f",{eff}"
    aux_chain += ",format=yuv420p[aux]"
    fc = (
        f"[0:v]fps={info.fps:g},format=yuv420p[main];"
        f"[1:v]{aux_chain};"
        f"[main][aux]overlay=0:0:enable='lt(mod(n\\,{period})\\,{count})'[v]"
    )
    run_ffmpeg(
        [ffmpeg_path(), "-y", "-i", main, "-stream_loop", "-1", "-i", aux,
         "-filter_complex", fc, "-map", "[v]", "-map", "0:a?",
         *ENCODE_X264_CRF18, *AUDIO_AAC, "-movflags", "+faststart",
         "-t", f"{info.duration}", out],
        total_duration=info.duration, label="frame-gap",
    )
    return out


def flash_frame(main: str, out: str, aux: str | None = None,
                seed: int = 0) -> str:
    """新 闪帧。
    来源:报告 §5.1「XHS 新区 · 8. 闪帧」——辅助视频只取首帧、铺成 120 帧
    与主视频逐帧交替(偶主奇辅);无辅助素材时自动生成随机图案作辅助首帧。
    组装:主轨 60fps + 静帧轨 60fps 走闪帧骨架交织成 120fps。
    """
    info = probe(main)
    tmp = make_temp_dir(out)
    frame_png = str(tmp / "flash_frame.png")
    if aux:
        frame_seq.extract_single_frame(aux, frame_png, 0.0,
                                       label="flash-grab")
    else:
        background_gen.make_pattern_image(frame_png, info.width, info.height,
                                          seed=seed)
    still_track = str(tmp / "flash_still.mp4")
    run_ffmpeg(
        [ffmpeg_path(), "-y", "-loop", "1", "-framerate", "60",
         "-i", frame_png, "-vf", "fps=60,format=yuv420p",
         "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
         "-t", f"{info.duration}", still_track],
        total_duration=info.duration, label="flash-still",
    )
    run_interleave(main, still_track, out, "flash120", info,
                   aux_loop=False, label="flash-frame")
    return out


def dongtai_a(main: str, out: str, bg: str | None = None,
              bg_gen: str = "grid", seed: int = 0, margin: float = 0.92) -> str:
    """新9-08 动态A。
    来源:报告 §5.1「动态背景 A 轨」+ §5.6 生成背景。字符串证据仅一行,
    复刻组装为:动态背景(文件或程序化生成)作 A 轨全画布,主视频等比
    缩小居中叠于其上(画中画式动态边框),音轨取主视频。此组装方式为
    复刻解释,见 README 已知差异。
    """
    info = probe(main)
    tmp = make_temp_dir(out)
    bg_path = bg
    if bg_path is None:
        bg_path = str(tmp / "dongtai_bg.mp4")
        background_gen.generate(bg_gen, bg_path, info.width, info.height,
                                info.duration, seed=seed)
    fg_w = int(info.width * margin) // 2 * 2
    fg_h = int(info.height * margin) // 2 * 2
    fc = (
        f"[1:v]scale={info.width}:{info.height}:force_original_aspect_ratio=disable,"
        f"setsar=1,fps={info.fps:g},format=yuv420p[bg];"
        f"[0:v]scale={fg_w}:{fg_h}:force_original_aspect_ratio=decrease,"
        f"setsar=1,fps={info.fps:g},format=yuv420p[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2[v]"
    )
    run_ffmpeg(
        [ffmpeg_path(), "-y", "-i", main, "-stream_loop", "-1", "-i", bg_path,
         "-filter_complex", fc, "-map", "[v]", "-map", "0:a?",
         *ENCODE_X264_CRF18, *AUDIO_AAC, "-movflags", "+faststart",
         "-t", f"{info.duration}", out],
        total_duration=info.duration, label="dongtai-a",
    )
    return out


def magic_frame(main: str, aux: str, out: str,
                audio_bitrate: str = "192k", audio_mode: int = 0) -> str:
    """新 魔术帧。
    来源:3.3 脱壳镜像字符串二次挖掘(静态报告 §5.1 曾标“未确认”,
    2026-10-14 在 dump 的 ffmpeg_command_catcher 模块中恢复全流程,
    偏移 0x0a01434f 起)。原版四步:
      1) 生成 a1.mp4:主视频标准化——libx264 preset fast、profile
         high level 4.2、bf 0、yuv420p、faststart,
         vf = scale={W}:{H},setsar=1,setpts=N/FRAME_RATE/TB,fps=30;
      2) 生成 b1.mp4:辅视频循环,scale:force_original_aspect_ratio=
         increase + crop={W}:{H} 填满、fps=30,preset medium、无音频;
      3) 合成 ab.mp4:[a] fps=90 与 [b] fps=30 经 interleave,
         fps=90,scale,select='not(eq(n,0)+eq(n,1))' 剔头 2 帧,无音频;
      4) 合并:视频 copy + 音频经 AB 音频隐藏(tab_ab_audio 的
         process_audio_hide)后以 aac / -aac_ms / -aac_is 封装。
    第 4 步音频(2026-10-14 M4 落地后更新):
      - audio_mode=0(默认):音频按原版封装参数直通(原音轨 → aac
        -aac_ms 0 -aac_is 0 -shortest;值 0 依据 tab_ab_audio
        remux 常量中 -aac_ms 后紧跟的 0,关 AAC 声道联合编码);
      - audio_mode=1–5:接 AB 音频隐藏(app.media.ab_audio),
        A = 主视频音轨,B = 辅视频音轨(模式5 不需要 B);
      - a1 的 -crf 与 -g 数值在转储字符串中被切分丢失,按本工程
        小红书口径取 crf 18 / 短 GOP 12;b1 循环以 -t 主时长收尾
        (原版循环计数为运行时变量,未见常量)。
    """
    info = probe(main)
    w, h, dur = info.width, info.height, info.duration
    tmp = make_temp_dir(out)
    ff = ffmpeg_path()

    a1 = str(tmp / "magic_a1.mp4")
    run_ffmpeg(
        [ff, "-y", "-i", main,
         "-vf", (f"scale={w}:{h},setsar=1,setpts=N/FRAME_RATE/TB,"
                 f"fps=30,format=yuv420p"),
         "-c:v", "libx264", "-preset", "fast", "-crf", "18",
         "-profile:v", "high", "-level", "4.2", "-pix_fmt", "yuv420p",
         "-bf", "0", "-g", "12", "-movflags", "+faststart", "-an", a1],
        total_duration=dur, label="magic-a1",
    )

    b1 = str(tmp / "magic_b1.mp4")
    run_ffmpeg(
        [ff, "-y", "-stream_loop", "-1", "-i", aux,
         "-vf", (f"scale={w}:{h}:force_original_aspect_ratio=increase,"
                 f"crop={w}:{h},fps=30,format=yuv420p"),
         "-c:v", "libx264", "-preset", "medium", "-crf", "18",
         "-pix_fmt", "yuv420p", "-an", "-threads", "0",
         "-t", f"{dur}", b1],
        total_duration=dur, label="magic-b1",
    )

    ab = str(tmp / "magic_ab.mp4")
    fc = (f"[0:v]scale={w}:{h},setsar=1,setpts=N/FRAME_RATE/TB,fps=90[a];"
          f"[1:v]scale={w}:{h},setsar=1,setpts=N/FRAME_RATE/TB,fps=30[b];"
          f"[a][b]interleave,fps=90,scale={w}:{h},"
          f"select='not(eq(n,0)+eq(n,1))'[v]")
    run_ffmpeg(
        [ff, "-y", "-i", a1, "-stream_loop", "-1", "-i", b1,
         "-filter_complex", fc, "-map", "[v]", "-an",
         "-c:v", "libx264", "-preset", "fast", "-crf", "18",
         "-profile:v", "high", "-pix_fmt", "yuv420p", "-bf", "0",
         "-t", f"{dur}", ab],
        total_duration=dur, label="magic-ab",
    )

    if audio_mode:
        from ..media import ab_audio
        params = ab_audio.ABParams(mode=audio_mode,
                                   audio_bitrate=audio_bitrate)
        a_wav = str(tmp / "magic_ab_in.wav")
        ab_audio.extract_pcm_wav(main, a_wav, params.sample_rate)
        b_wav = None
        if audio_mode != 5:
            b_wav = str(tmp / "magic_ab_b.wav")
            ab_audio.extract_pcm_wav(aux, b_wav, params.sample_rate)
        out_wav = str(tmp / "magic_ab_out.wav")
        ab_audio.process_audio_hide(a_wav, out_wav, params, b_wav=b_wav)
        ab_audio.remux_video_with_audio(ab, out_wav, out, audio_bitrate)
        return out
    if info.has_audio:
        run_ffmpeg(
            [ff, "-y", "-i", ab, "-i", main,
             "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
             "-c:a", "aac", "-b:a", audio_bitrate,
             "-aac_ms", "0", "-aac_is", "0", "-shortest",
             "-movflags", "+faststart", out],
            total_duration=dur, label="magic-final",
        )
    else:
        run_ffmpeg(
            [ff, "-y", "-i", ab, "-c", "copy",
             "-movflags", "+faststart", out],
            label="magic-final",
        )
    return out


def life_blend(main: str, out: str, seed: int = 0,
               a: float = 0.7, b: float = 0.3, hue: float = 5.0) -> str:
    """life 生命游戏复合(背景生成与主轨按行 blend 隔行混合)。
    来源:报告 §5.6(@0x4a332e8)——滤镜串逐字原文,见 templates.FILTER_LIFE_BLEND。
    {orient} 在原文中为主轨方向适配链(具体内容未恢复),复刻置空;
    life 细胞尺寸取输出画幅 1/2(原文 {lw}/{lh} 占位,复刻选择)。
    """
    info = probe(main)
    rng = random.Random(seed)
    fc = render(
        FILTER_LIFE_BLEND,
        lw=max(2, info.width // 2), lh=max(2, info.height // 2),
        ratio=round(rng.uniform(0.2, 0.4), 3), seed=seed,
        death="black", life="white",
        ow=info.width, oh=info.height, hue=hue, orient="",
        a=a, b=b,
    )
    run_ffmpeg(
        [ffmpeg_path(), "-y", "-i", main, "-filter_complex", fc,
         "-map", "[vout]", "-map", "0:a?",
         *ENCODE_X264_CRF18, *AUDIO_AAC, "-movflags", "+faststart",
         "-t", f"{info.duration}", out],
        total_duration=info.duration, label="life-blend",
    )
    return out


# ---------------------------------------------------------------------------
# 120fps 隔帧叠加(3.3 独立模式,与新120帧的 120:60 交织不同)
# ---------------------------------------------------------------------------

def overlay_120(main: str, aux: str, out: str) -> str:
    """新 120fps 隔帧叠加。
    来源:报告 §5.1 末——主轨升 120fps,辅轨只在奇数帧以 overlay
    整帧叠加(横 1280×720 / 竖 720×1280);与 new_120 的 120:60
    双流交织是两个模式。3.3 另有 256×256 overlay_cuda 变体,
    属小众,未做(见 OPTIMIZE-FROM-3.3.md P1-3)。
    组装:画布取 canvas_for(横 1280×720 / 竖 720×1280);主轨
    scale 填满 + fps=120;辅轨循环、scale increase+crop 填满 +
    fps=120;overlay enable='eq(mod(n,2),1)' 只在奇数帧生效。
    """
    info = probe(main)
    w, h = canvas_for(info)
    fc = (
        f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},setsar=1,fps=120,format=yuv420p[main];"
        f"[1:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},setsar=1,fps=120,format=yuv420p[aux];"
        f"[main][aux]overlay=0:0:enable='eq(mod(n\\,2)\\,1)'[v]"
    )
    run_ffmpeg(
        [ffmpeg_path(), "-y", "-i", main, "-stream_loop", "-1", "-i", aux,
         "-filter_complex", fc, "-map", "[v]", "-map", "0:a?",
         *ENCODE_X264_CRF18, *AUDIO_AAC, "-movflags", "+faststart",
         "-t", f"{info.duration}", out],
        total_duration=info.duration, label="overlay-120",
    )
    return out
