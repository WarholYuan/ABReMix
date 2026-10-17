"""小红书·历史区模式。

参数来源:《肥猫 3.3 逆向分析报告》§5.2、《肥猫 V2.7 逆向分析》其他模式节;
方案 §5.2。每个函数头部标注来源与复刻组装说明。
"""
from __future__ import annotations

import random
from pathlib import Path

from ..core.ffmpeg_resolve import ffmpeg_path
from ..core.probe import probe
from ..core.runner import run_ffmpeg
from ..postprocess.mp4box import patch_fake_duration
from ..media import background_gen, frame_seq
from .common import AUDIO_AAC, ENCODE_X264_CRF18, make_temp_dir, run_interleave
from .xhs_new import EFFECT_PRESETS

_SHORT_GOP = ["-g", "12"]  # 短 GOP(报告 §5.2:短 GOP + yuv420p + faststart)


# ---------------------------------------------------------------------------
# 卡时长(精确版)
# ---------------------------------------------------------------------------

def card_duration(main: str, out: str, seed: int = 0,
                  target_seconds: float | None = None,
                  elst_mode: str = "fake") -> str:
    """新 卡时长(小红书版,精确实现)。
    来源:报告 §5.2——成片时长“卡”到随机 25–60 分钟;两阶段处理
    (第一阶段编码到单个临时文件、第二阶段“参数优化”写入伪时长)。
    阶段二走 app.postprocess.mp4box.patch_fake_duration:
    mvhd/tkhd/mdhd + dirty stts 全部对齐目标时长,elst 默认全口径
    一并对齐(elst_mode="fake";实测 ffmpeg 系读取器以 elst 为准,
    不对齐则读真实时长且丢帧),或选 "real" 走原版 cvj 口径分裂
    (elst 保持真实、手机正常播,但 ffmpeg 系读真实时长)。
    """
    info = probe(main)
    rng = random.Random(seed)
    target = target_seconds or rng.uniform(25 * 60, 60 * 60)
    tmp = make_temp_dir(out)
    stage1 = str(tmp / "card_stage1.mp4")
    run_ffmpeg(
        [ffmpeg_path(), "-y", "-i", main,
         "-c:v", "libx264", "-preset", "fast", "-crf", "18",
         "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-movflags", "+faststart", stage1],
        total_duration=info.duration, label="card-stage1",
    )
    Path(stage1).replace(out)
    patch_fake_duration(out, target, seed=seed, elst_mode=elst_mode)
    return out


# ---------------------------------------------------------------------------
# 随机帧 AB
# ---------------------------------------------------------------------------

def random_frame_ab(main: str, aux: str, out: str, method: str = "single",
                    seed: int = 0, period: int = 3, count: int = 2,
                    opacity: float = 0.5, effect: str = "none") -> str:
    """新 随机帧AB。
    来源:报告 §5.2「视频批量处理(随机帧叠加)」——随机抽取 1 帧叠加,
    或用辅助视频半透明叠加(每 period 帧叠前 count 帧);叠加层特效
    预渲染为短循环片段(“把重特效先烤进短循环片段”)。
    """
    info = probe(main)
    aux_info = probe(aux)
    rng = random.Random(seed)
    tmp = make_temp_dir(out)
    ff = ffmpeg_path()

    if method == "single":
        t = rng.uniform(0, max(0.0, aux_info.duration - 0.1))
        frame_png = str(tmp / "random_frame.png")
        frame_seq.extract_single_frame(aux, frame_png, t, info.width,
                                       info.height, label="random-grab")
        fc = (
            f"[0:v]fps={info.fps:g},format=yuv420p[main];"
            f"[1:v]scale={info.width}:{info.height},format=rgba,"
            f"colorchannelmixer=aa={opacity}[fr];"
            f"[main][fr]overlay=0:0,format=yuv420p[v]"
        )
        cmd = [ff, "-y", "-i", main, "-loop", "1", "-i", frame_png,
               "-filter_complex", fc, "-map", "[v]", "-map", "0:a?",
               *ENCODE_X264_CRF18, *AUDIO_AAC, "-movflags", "+faststart",
               "-t", f"{info.duration}", out]
        run_ffmpeg(cmd, total_duration=info.duration, label="random-frame-single")
        return out

    # periodic:半透明周期叠加;有特效时先把特效烤进 3 秒短循环片段
    aux_input = aux
    if effect != "none":
        eff = EFFECT_PRESETS[effect]
        loop_clip = str(tmp / "random_fx_loop.mp4")
        run_ffmpeg(
            [ff, "-y", "-i", aux,
             "-vf", (f"scale={info.width}:{info.height}:"
                     f"force_original_aspect_ratio=disable,setsar=1,"
                     f"fps={info.fps:g},{eff},format=yuv420p"),
             "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
             "-an", "-t", "3", loop_clip],
            total_duration=3.0, label="random-fx-prerender",
        )
        aux_input = loop_clip
    fc = (
        f"[0:v]fps={info.fps:g},format=yuv420p[main];"
        f"[1:v]scale={info.width}:{info.height}:force_original_aspect_ratio=disable,"
        f"setsar=1,fps={info.fps:g},format=rgba,"
        f"colorchannelmixer=aa={opacity}[aux];"
        f"[main][aux]overlay=0:0:enable='lt(mod(n\\,{period})\\,{count})',"
        f"format=yuv420p[v]"
    )
    run_ffmpeg(
        [ff, "-y", "-i", main, "-stream_loop", "-1", "-i", aux_input,
         "-filter_complex", fc, "-map", "[v]", "-map", "0:a?",
         *ENCODE_X264_CRF18, *AUDIO_AAC, "-movflags", "+faststart",
         "-t", f"{info.duration}", out],
        total_duration=info.duration, label="random-frame-periodic",
    )
    return out


# ---------------------------------------------------------------------------
# 9-08 AB 批量
# ---------------------------------------------------------------------------

_VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm"}


def batch_908(input_dir: str, out_dir: str, aux_dir: str | None = None,
              auto_aux: str | None = None, effect: str = "none",
              seed: int = 0) -> list[str]:
    """新9-08 AB批量。
    来源:报告 §5.2——文件夹批量、多选辅助视频循环分配;可自动生成辅助
    视频(乱色块/杂乱形状或动态 grid 背景);三步流水线:1/3 合成主片 →
    2/3 辅助+特效 → 3/3 交错合成(音轨取自主视频)。
    单条失败不中断整批,失败原因打印到 stderr。
    """
    import sys

    from ..core.templates import time_tag_now

    in_dir, out_root = Path(input_dir), Path(out_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    mains = sorted(p for p in in_dir.iterdir()
                   if p.suffix.lower() in _VIDEO_EXTS)
    auxes: list[Path] = []
    if aux_dir:
        auxes = sorted(p for p in Path(aux_dir).iterdir()
                       if p.suffix.lower() in _VIDEO_EXTS)
    if not mains:
        raise RuntimeError(f"输入目录无视频文件: {input_dir}")
    if not auxes and not auto_aux:
        raise RuntimeError("未提供辅助目录且未启用 --auto-aux,无辅轨可用")

    results: list[str] = []
    for i, main_path in enumerate(mains):
        try:
            info = probe(str(main_path))
            tmp = out_root / "temp" / main_path.stem
            tmp.mkdir(parents=True, exist_ok=True)
            # 1/3 合成主片:归一化到 30fps / yuv420p,保留主音轨
            prep_main = str(tmp / "main.mp4")
            run_ffmpeg(
                [ffmpeg_path(), "-y", "-i", str(main_path),
                 "-vf", "fps=30,format=yuv420p",
                 "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
                 "-c:a", "aac", prep_main],
                total_duration=info.duration, label=f"908-{main_path.stem}-step1",
            )
            # 辅轨来源:池内循环分配,或按主片尺寸自动生成
            if auxes:
                aux_src = str(auxes[i % len(auxes)])
            else:
                aux_src = str(tmp / "aux_auto.mp4")
                background_gen.generate(auto_aux or "blocks", aux_src,
                                         info.width, info.height,
                                         info.duration, seed=seed + i)
            # 2/3 辅助+特效:缩放对齐主画幅,挂特效链(默认无)
            eff = EFFECT_PRESETS.get(effect, "")
            aux_vf = (f"scale={info.width}:{info.height}:"
                      f"force_original_aspect_ratio=disable,setsar=1,"
                      f"fps=30" + (f",{eff}" if eff else "") + ",format=yuv420p")
            prep_aux = str(tmp / "aux.mp4")
            run_ffmpeg(
                [ffmpeg_path(), "-y", "-stream_loop", "-1", "-i", aux_src,
                 "-vf", aux_vf,
                 "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
                 "-an", "-t", f"{info.duration}", prep_aux],
                total_duration=info.duration, label=f"908-{main_path.stem}-step2",
            )
            # 3/3 交错合成(音轨在主片 prep 中,骨架取输入 0 音轨)
            out_path = str(out_root / f"{main_path.stem}_908AB_{time_tag_now()}.mp4")
            run_interleave(prep_main, prep_aux, out_path, "ab60",
                           probe(prep_main), aux_loop=False,
                           label=f"908-{main_path.stem}-step3")
            results.append(out_path)
        except Exception as exc:  # 单条失败不中断整批(方案 §9 批量语义)
            print(f"[batch-908] 跳过 {main_path.name}: {exc}", file=sys.stderr)
    return results


# ---------------------------------------------------------------------------
# 单帧AB / 实拍AB / 双帧
# ---------------------------------------------------------------------------

def single_frame_ab(main: str, aux: str, out: str, frame_time: float = 0.0,
                    opacity: float = 0.5, swap_orient: bool = False) -> str:
    """新9-08 单帧AB。
    来源:报告 §5.2——单帧叠加;可选“合成后格式工厂式横竖屏互换”;
    短 GOP + yuv420p + faststart(注释:避免小红书拖进度条黑屏)。
    组装:取辅轨一帧静图,半透明叠加于主轨全程。
    """
    info = probe(main)
    cw, ch = (info.height, info.width) if swap_orient else (info.width, info.height)
    tmp = make_temp_dir(out)
    frame_png = str(tmp / "single_frame.png")
    frame_seq.extract_single_frame(aux, frame_png, frame_time, cw, ch,
                                   label="single-grab")
    fc = (
        f"[0:v]scale={cw}:{ch}:force_original_aspect_ratio=decrease,"
        f"pad={cw}:{ch}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,"
        f"fps={info.fps:g},format=yuv420p[main];"
        f"[1:v]format=rgba,colorchannelmixer=aa={opacity}[fr];"
        f"[main][fr]overlay=0:0,format=yuv420p[v]"
    )
    run_ffmpeg(
        [ffmpeg_path(), "-y", "-i", main, "-loop", "1", "-i", frame_png,
         "-filter_complex", fc, "-map", "[v]", "-map", "0:a?",
         *ENCODE_X264_CRF18, *_SHORT_GOP, *AUDIO_AAC,
         "-movflags", "+faststart", "-t", f"{info.duration}", out],
        total_duration=info.duration, label="single-frame-ab",
    )
    return out


def realshot_ab(main: str, aux: str, out: str, opacity: float = 0.5,
                duration_from_aux: bool = False) -> str:
    """新 实拍AB。
    来源:报告 §5.2——“实拍+动效”叠加(动效时长规则与注意事项文案同 2.7 的
    1B3A 页:高于 3 分钟的动效勾选以动效时长为准);短 GOP + faststart。
    组装:动效轨半透明全程叠加于实拍轨;时长默认取主轨,可切换为动效轨。
    """
    info = probe(main)
    target = probe(aux).duration if duration_from_aux else info.duration
    fc = (
        f"[0:v]fps={info.fps:g},format=yuv420p[main];"
        f"[1:v]scale={info.width}:{info.height}:force_original_aspect_ratio=disable,"
        f"setsar=1,fps={info.fps:g},format=rgba,"
        f"colorchannelmixer=aa={opacity}[aux];"
        f"[main][aux]overlay=0:0,format=yuv420p[v]"
    )
    run_ffmpeg(
        [ffmpeg_path(), "-y", "-i", main, "-stream_loop", "-1", "-i", aux,
         "-filter_complex", fc, "-map", "[v]", "-map", "0:a?",
         *ENCODE_X264_CRF18, *_SHORT_GOP, *AUDIO_AAC,
         "-movflags", "+faststart", "-t", f"{target}", out],
        total_duration=target, label="realshot-ab",
    )
    return out


def double_frame(main: str, aux: str, out: str, opacity: float = 0.5) -> str:
    """新9-08 双帧(tab_shuangzhen)。
    来源:报告 §5.2——双帧叠加,细节从略。按双轨叠加框架组装:
    主/辅双帧逐帧线性混合(blend),混合比由 opacity 控制。
    """
    info = probe(main)
    fc = (
        f"[0:v]fps={info.fps:g},format=yuv420p[m];"
        f"[1:v]scale={info.width}:{info.height}:force_original_aspect_ratio=disable,"
        f"setsar=1,fps={info.fps:g},format=yuv420p[a];"
        f"[m][a]blend=all_expr='A*{1 - opacity:.3f}+B*{opacity:.3f}'[v]"
    )
    run_ffmpeg(
        [ffmpeg_path(), "-y", "-i", main, "-stream_loop", "-1", "-i", aux,
         "-filter_complex", fc, "-map", "[v]", "-map", "0:a?",
         *ENCODE_X264_CRF18, *AUDIO_AAC, "-movflags", "+faststart",
         "-t", f"{info.duration}", out],
        total_duration=info.duration, label="double-frame",
    )
    return out


# ---------------------------------------------------------------------------
# 180小 / 1B3A / 三帧叠背景
# ---------------------------------------------------------------------------

def ab_180(main: str, out: str, aux: str | None = None,
           aux_gen: str | None = None, seed: int = 0) -> str:
    """新 180小(tab_hongshu180)——已按原版真品出片逐帧校准。

    来源:报告 §5.2「60+120 模式」+ 原版真品 hongshu180.mov 实测结构
    (720×1280 / 180fps / 9539 帧 / 53 秒):帧图案严格周期——**前 4 帧
    纯主轨,第 4 帧起每 3 帧插 1 帧辅轨**(辅轨位置 n=4,7,10,…,
    即 n≥4 且 n≡1 mod 3),等价于主 120fps + 辅 60fps 交织再带一个
    4 帧主轨片头;主轨按时间采样(片头处实测有 fps 转换的整帧重复)。
    组装:双轨各归一化到 180fps 网格,辅轨经 scale2ref 对齐主画幅后,
    以 overlay 在辅轨槽位整帧覆盖主轨
    (enable='gte(n,4)*eq(mod(n-4,3),0)'),槽位取帧按时间戳对齐,
    与「主 120 槽位 / 辅 60 槽位」的时间采样等价。
    校准前的旧实现(templates 的 interleave「180」变体)辅轨从 n=1 起
    (n=1,4,7,…),比例/相位虽同,片头多了 1 帧辅轨、少了主轨片头,
    已弃用;对照数据见 TESTING.md「180 校准对照」。
    辅轨内容:真品为一张紫色底同心圆生成图案;aux 缺省时传
    aux_gen='circles' 即自动生成同心圆图案视频作辅轨。
    """
    info = probe(main)
    if aux is None:
        if aux_gen != "circles":
            raise ValueError("ab_180 需要辅轨文件(-a),或 --aux-gen circles 自动生成")
        tmp = make_temp_dir(out)
        aux = str(tmp / "aux_circles.mp4")
        background_gen.gen_circles(aux, info.width, info.height,
                                   info.duration, seed=seed)
    # 帧时间结构(2026-10-14 按真品 hongshu180.mov 的 stts 实测修正):
    # 真品并非均匀 180fps CFR,而是 120Hz 主轨网格 + 辅轨帧紧贴其后:
    # 每组 3 帧中,辅轨前一帧主轨显示时长被压到 1µs(实际不可见),
    # 辅轨帧占 8332µs,另一主轨帧占 8334µs(时间基 1/1000000)。
    # 平台/播放器按时间取帧时几乎抽不到被压帧,故不闪;旧实现把每帧
    # 都排成均匀 5556µs,平台抽帧相位漂移即闪。另真品关键帧间隔约
    # 250 帧(x264 默认 keyint 且 scenecut 不触发),旧实现 scenecut 在
    # 主/辅硬切处每 ~26 帧打关键帧,一并修正(scenecut=0 + g=250)。
    fc = (
        "[0:v]setsar=1,fps=180,format=yuv420p[base];"
        "[1:v]setsar=1,fps=180[auxraw];"
        "[auxraw][base]scale2ref[auxs][baseref];"
        "[baseref][auxs]overlay=0:0:"
        "enable='gte(n\\,4)*eq(mod(n-4\\,3)\\,0)'[ovl];"
        "[ovl]settb=1/1000000,"
        "setpts='if(lt(N\\,3)\\,N*8333\\,"
        "floor(N/3)*16667+if(eq(mod(N\\,3)\\,0)\\,8333\\,"
        "if(eq(mod(N\\,3)\\,1)\\,8334\\,16666)))',format=yuv420p[v]"
    )
    run_ffmpeg(
        [ffmpeg_path(), "-y", "-i", main, "-stream_loop", "-1", "-i", aux,
         "-filter_complex", fc, "-map", "[v]", "-map", "0:a?",
         *ENCODE_X264_CRF18, *AUDIO_AAC,
         "-g", "250", "-sc_threshold", "0", "-vsync", "vfr",
         "-video_track_timescale", "1000000",
         "-t", f"{info.duration}", out],
        total_duration=info.duration, label="ab-180",
    )
    # 封装级时间重排(2026-10-14 晚定版):实测编码+mux 会把上一步
    # setpts 在滤镜端排好的时间戳压回均匀 5556µs 网格,1µs 压帧无法
    # 经编码链存活;改在成片后用 setts 比特流滤镜按包重写时间戳
    # (-c copy 免重编码),结果与真品逐项一致:压帧位于 n=3,6,9,…、
    # 时长 1µs,辅轨帧 8332µs、主轨帧 8334µs,连 ffmpeg 解码真品与
    # 本输出时的 dts 提示数值都相同(6>=6、10>=10、14>=14)。
    timed = out + ".timed.mov"
    run_ffmpeg(
        [ffmpeg_path(), "-y", "-i", out, "-map", "0", "-c", "copy",
         "-video_track_timescale", "1000000",
         "-bsf:v",
         "setts=ts='if(lt(N\\,3)\\,N*8333\\,"
         "floor(N/3)*16667+if(eq(mod(N\\,3)\\,0)\\,8333\\,"
         "if(eq(mod(N\\,3)\\,1)\\,8334\\,16666)))'"
         ":duration='if(lt(N\\,3)\\,8333\\,"
         "if(eq(mod(N\\,3)\\,0)\\,1\\,"
         "if(eq(mod(N\\,3)\\,1)\\,8332\\,8334)))'",
         timed],
        total_duration=info.duration, label="ab-180-timing",
    )
    Path(timed).replace(out)
    return out


def one_b_three_a(main: str, aux: str, out: str) -> str:
    """1B3A。
    来源:报告 §5.2(实现走图片序列交错回退)+ 2.7 报告(「1帧实拍 +
    3帧搬运」配比)。组装:双轨按 30fps 抽帧,按 [主,辅,辅,辅] 图案
    编排,40fps 回拼(双轨同帧率同长度时成片时长与原片一致),音轨取主轨。
    """
    info = probe(main)
    tmp = make_temp_dir(out)
    frames_a = frame_seq.extract_frames(main, str(tmp / "b3a_a"), 30,
                                        info.width, info.height,
                                        label="1b3a-extract-a")
    frames_b = frame_seq.extract_frames(aux, str(tmp / "b3a_b"), 30,
                                        info.width, info.height,
                                        label="1b3a-extract-b")
    seq_dir = str(tmp / "b3a_seq")
    frame_seq.assemble_alternating(frames_a, frames_b, seq_dir, [0, 1, 1, 1])
    frame_seq.encode_sequence(seq_dir, 40, out, audio_from=main,
                              duration=info.duration, label="1b3a")
    frame_seq.cleanup_dir(tmp / "b3a_a")
    frame_seq.cleanup_dir(tmp / "b3a_b")
    frame_seq.cleanup_dir(seq_dir)
    return out


def three_frame_bg(main: str, bg: str, out: str, opacity: float = 0.5) -> str:
    """三帧叠背景(2.7 沿用功能,3.3 仍在)。
    来源:2.7 报告其他模式节——「从第 4 帧起每隔一帧叠背景 · 输出保持
    原分辨率 · 文件名加『_三帧』」;脱壳补充:内部实现为背景流 split=2
    分两路 + trim 片段拼接,滤镜图由分片常量运行时拼装。
    组装:主轨 split 为头部 3 帧与主体两段(主体段内从首帧起隔帧叠加
    背景),trim 对齐后 concat 回拼;分辨率不变,音轨取主轨。
    """
    info = probe(main)
    fc = (
        "[0:v]split=2[ma][mb];"
        "[ma]trim=end_frame=3,setpts=PTS-STARTPTS[head];"
        "[mb]trim=start_frame=3,setpts=PTS-STARTPTS[body];"
        f"[1:v]scale={info.width}:{info.height}:force_original_aspect_ratio=disable,"
        "setsar=1,trim=start_frame=3,setpts=PTS-STARTPTS,"
        f"format=rgba,colorchannelmixer=aa={opacity}[bgb];"
        "[body][bgb]overlay=0:0:enable='eq(mod(n\\,2)\\,0)'[body2];"
        "[head][body2]concat=n=2:v=1:a=0,format=yuv420p[v]"
    )
    run_ffmpeg(
        [ffmpeg_path(), "-y", "-i", main, "-stream_loop", "-1", "-i", bg,
         "-filter_complex", fc, "-map", "[v]", "-map", "0:a?",
         *ENCODE_X264_CRF18, *AUDIO_AAC, "-movflags", "+faststart",
         "-t", f"{info.duration}", out],
        total_duration=info.duration, label="three-frame-bg",
    )
    return out
