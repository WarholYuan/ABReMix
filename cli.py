#!/usr/bin/env python3
"""肥猫复刻版 · 命令行入口(小红书部分)。

用法示例见 README.md。所有子命令通用约定:
  -i/--input 主视频;-a/--aux 辅轨/背景视频;-o/--output 输出文件
  --seed 随机种子(涉及随机抖动的模式可复现)
退出码:0 成功;1 失败(错误信息含命令与 stderr 摘要)。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.core.probe import probe  # noqa: E402
from app.media import background_gen  # noqa: E402
from app.modes import bl, heiwu, xhs_hist, xhs_new  # noqa: E402
from app.modes import baoshan, longmen_120  # noqa: E402
from app.postprocess import fingerprint  # noqa: E402


def _add_io(p: argparse.ArgumentParser, aux: bool = True,
            output: bool = True) -> None:
    p.add_argument("-i", "--input", required=True, help="主视频")
    if aux:
        p.add_argument("-a", "--aux", required=True, help="辅轨/背景视频")
    if output:
        p.add_argument("-o", "--output", required=True, help="输出文件")


def _ensure_parent(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="feimao", description="肥猫复刻版(小红书)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # ---- 交织骨架家族 ----
    p = sub.add_parser("pure-ab", help="新 纯AB:90fps 隔帧交织叠加【注意:90fps 与平台 60fps 非整数倍,平台抽帧相位漂移、两路乱序被抽中会发闪;正式发布推荐 ab-180(180fps 为 60 的整数倍,只整齐抽中一路)】")
    _add_io(p)
    p = sub.add_parser("interleave-105-35", help="105:35 交织变体(原文滤镜)")
    _add_io(p)
    p = sub.add_parser("ab-180", help="新 180小:180fps,前 4 帧主轨 + 每 3 帧 1 辅(已按真品校准)")
    p.add_argument("-i", "--input", required=True, help="主视频")
    p.add_argument("-a", "--aux", default=None,
                   help="辅轨视频(缺省时用 --aux-gen 自动生成)")
    p.add_argument("-o", "--output", required=True, help="输出文件")
    p.add_argument("--aux-gen", default=None, choices=["circles"],
                   help="无辅轨文件时自动生成的辅轨类型(circles=同心圆图案)")
    p.add_argument("--seed", type=int, default=0)
    p = sub.add_parser("fanzhuan-ab", help="新 反转AB:60+60 交错成 120fps")
    _add_io(p)
    p = sub.add_parser("new-120", help="新120帧:底图 shape.png + 120:60 交错")
    _add_io(p, aux=False)
    p.add_argument("--seed", type=int, default=0)

    # ---- 帧序列通道 ----
    p = sub.add_parser("xin-fanzhuan", help="新反转:图片序列逐帧交替成 120fps")
    _add_io(p)
    p = sub.add_parser("one-b-three-a", help="1B3A:图片序列 1 主 3 辅交错")
    _add_io(p)

    # ---- 叠加家族 ----
    p = sub.add_parser("frame-gap", help="新 帧间隔:周期叠帧 + 特效链")
    _add_io(p)
    p.add_argument("--period", type=int, default=3)
    p.add_argument("--count", type=int, default=2)
    p.add_argument("--effect", default="none",
                   choices=list(xhs_new.EFFECT_PRESETS))
    p.add_argument("--custom-filter", default="")
    p = sub.add_parser("random-frame-ab", help="新 随机帧AB:随机抽帧/半透明周期叠加")
    _add_io(p)
    p.add_argument("--method", default="single", choices=["single", "periodic"])
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--period", type=int, default=3)
    p.add_argument("--count", type=int, default=2)
    p.add_argument("--opacity", type=float, default=0.5)
    p.add_argument("--effect", default="none",
                   choices=list(xhs_new.EFFECT_PRESETS))
    p = sub.add_parser("single-frame-ab", help="新9-08 单帧AB:辅轨一帧半透明叠加")
    _add_io(p)
    p.add_argument("--frame-time", type=float, default=0.0)
    p.add_argument("--opacity", type=float, default=0.5)
    p.add_argument("--swap-orient", action="store_true", help="横竖屏互换")
    p = sub.add_parser("realshot-ab", help="新 实拍AB:实拍+动效半透明叠加")
    _add_io(p)
    p.add_argument("--opacity", type=float, default=0.5)
    p.add_argument("--duration-from-aux", action="store_true",
                   help="以动效时长为准")
    p = sub.add_parser("double-frame", help="新9-08 双帧:双轨逐帧混合")
    _add_io(p)
    p.add_argument("--opacity", type=float, default=0.5)
    p = sub.add_parser("overlay-120", help="新 120fps 隔帧叠加:主轨 120fps,辅轨只在奇数帧整帧叠加(与 new-120 的交织不同)")
    _add_io(p)
    p = sub.add_parser("magic-frame", help="新 魔术帧:90:30 交织(每 3 帧 2 主 1 辅);--audio-mode 1-5 可叠 AB 音频隐藏(B 取辅轨音轨);输出同为 90fps,平台发闪风险同 pure-ab 说明")
    _add_io(p)
    p.add_argument("--audio-bitrate", default="192k")
    p.add_argument("--audio-mode", type=int, default=0, choices=[0, 1, 2, 3, 4, 5],
                   help="AB 音频隐藏模式(0=不隐藏,原声直通)")
    p = sub.add_parser("ab-audio", help="黑五 AB 音频:声道隐藏 5 模式(1 机审听B人听A / 2 反 / 3,4 +超声波 / 5 纯反相静音)")
    p.add_argument("-i", "--input", required=True, help="视频A")
    p.add_argument("-a", "--aux", default=None, help="音频B(媒体文件;模式1-4 必需,模式5 不用)")
    p.add_argument("-o", "--output", required=True, help="输出视频")
    p.add_argument("--mode", type=int, required=True, choices=[1, 2, 3, 4, 5])
    p.add_argument("--alpha", type=float, default=1.0, help="原片A音量")
    p.add_argument("--deep", action="store_true", help="全片深度(粉红噪+Haas,需配合 --interfere-vol)")
    p.add_argument("--interfere-vol", type=float, default=0.0, help="干扰音量")
    p.add_argument("--time-mode", default="full",
                   choices=["full", "first_half", "last_half"])
    p = sub.add_parser("flash-frame", help="新 闪帧:辅轨首帧与主轨逐帧交替成 120fps")
    _add_io(p, aux=False)
    p.add_argument("-a", "--aux", default=None, help="辅轨(可缺省,缺省自动生成图案)")
    p.add_argument("--seed", type=int, default=0)
    p = sub.add_parser("three-frame-bg", help="三帧叠背景:第 4 帧起隔帧叠背景")
    _add_io(p)
    p.add_argument("--opacity", type=float, default=0.5)

    # ---- 9-08 AB 批量 / 动态A / life ----
    p = sub.add_parser("batch-908", help="新9-08 AB批量:文件夹批量三步流水线")
    p.add_argument("--input-dir", required=True)
    p.add_argument("--aux-dir", default=None)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--auto-aux", default=None,
                   choices=["grid", "life", "blocks", "noise", "checker",
                            "wave", "dots", "color"],
                   help="无辅轨时自动生成类型")
    p.add_argument("--effect", default="none",
                   choices=list(xhs_new.EFFECT_PRESETS))
    p.add_argument("--seed", type=int, default=0)
    p = sub.add_parser("dongtai-a", help="新9-08 动态A:动态背景 A 轨合成")
    _add_io(p, aux=False)
    p.add_argument("--bg", default=None, help="背景视频(缺省则程序化生成)")
    p.add_argument("--bg-gen", default="grid",
                   choices=["grid", "life", "blocks", "noise", "checker",
                            "wave", "dots", "color"])
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--margin", type=float, default=0.92)
    p = sub.add_parser("life-blend", help="life 生命游戏背景按行 blend 复合(原文滤镜)")
    _add_io(p, aux=False)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--a", type=float, default=0.7)
    p.add_argument("--b", type=float, default=0.3)
    p.add_argument("--hue", type=float, default=5.0)

    # ---- 卡时长(精确版) ----
    p = sub.add_parser("card-duration", help="新 卡时长(精确版):伪显示时长 25–60 分钟(全口径对齐,ffprobe 可读)")
    _add_io(p, aux=False)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--target-seconds", type=float, default=None)
    p.add_argument("--keep-elst-real", action="store_true",
                   help="cvj 兼容口径:elst 保持真实时长(手机正常播,但 ffmpeg 系读取器读真实时长)")

    # ---- 生成背景 ----
    p = sub.add_parser("gen-bg", help="单独生成一段程序化背景视频")
    p.add_argument("--kind", required=True,
                   choices=["grid", "life", "circles", "noise", "checker",
                            "wave", "dots", "color", "blocks"])
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=720)
    p.add_argument("--duration", type=float, default=5.0)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--fps", type=int, default=30)

    # ---- 黑五专区 ----
    p = sub.add_parser("heiwu-batch", help="黑五·批处理:三模式转码(参数冻结:模式一 720x1280 pad+scale;模式二 720x1280+CRF17+baseline;模式三 496x864+ultrafast+6000k+AAC 195247)")
    p.add_argument("-i", "--input", required=True, help="主视频")
    p.add_argument("-o", "--output", required=True, help="输出文件")
    p.add_argument("--mode", type=int, required=True, choices=[1, 2, 3])
    p = sub.add_parser("yuan-data", help="黑五·元数据:SKT4 式伪装时长(命名 {stem}_元数据_{n}.mp4)")
    p.add_argument("-i", "--input", required=True, help="主视频")
    p.add_argument("--out-dir", required=True, help="输出目录")
    p.add_argument("--duration", default=None,
                   help="伪装时长:03:18:00 / 3:18 / 秒数(缺省 03:18:00)")
    p.add_argument("--mode", type=int, default=1, choices=[1, 2],
                   help="编码档:1=High@4.1 1000k;2=Main@3.1 2000k")
    p = sub.add_parser("yuan-data2", help="黑五·元数据2:四档命令 + 伪装时长 + 后置校验(解3秒+关键帧;命名 {stem}_元数据2_{n}.mp4)")
    p.add_argument("-i", "--input", required=True, help="主视频")
    p.add_argument("--out-dir", required=True, help="输出目录")
    p.add_argument("--duration", default=None,
                   help="伪装时长:03:18:00 / 3:18 / 秒数(缺省 03:18:00)")
    p.add_argument("--mode", type=int, default=1, choices=[1, 2],
                   help="编码档:1=High@4.1 1000k·Wxmm;2=Main@3.1 2000k")
    p = sub.add_parser("short-duration", help="伪短时长:长片显示成短时长(mp4box 等效实现,Bento4 缺位回退直出)")
    p.add_argument("-i", "--input", required=True, help="主视频")
    p.add_argument("-o", "--output", required=True, help="输出文件")
    p.add_argument("--display-seconds", type=float, required=True,
                   help="显示时长(秒)")
    p = sub.add_parser("fingerprint", help="指纹后处理:cvj / weird4 / weird8(方案 §7.2)")
    p.add_argument("-i", "--input", required=True, help="已编码 MP4(原地改写,先备份)")
    p.add_argument("--mode", required=True, choices=["cvj", "weird4", "weird8"])
    p.add_argument("--target-seconds", type=float, default=None,
                   help="cvj/weird4 的假时长(秒)")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--sar", default=None,
                   help="weird8 的 SAR(如 2671:51675,缺省用原版值)")
    p.add_argument("--elst-fake", action="store_true",
                   help="cvj:elst 一并对齐假时长(默认保持真实=口径分裂)")

    # ---- 新功能(2026-10-17):龙门120 & 爆闪 ----
    p = sub.add_parser("longmen-120", help="龙门 xhs_lxkt:zoompan+hue 色相旋转动态网格底图(60fps)")
    p.add_argument("-i", "--input", required=True, help="主视频")
    p.add_argument("-o", "--output", required=True, help="输出文件")
    p.add_argument("--duration", type=float, default=None,
                   help="指定输出时长(秒;缺省时取主视频时长)")
    
    p = sub.add_parser("baoshan", help="爆闪:colorchannelmixer 提亮 + overlay 叠加(60fps)")
    p.add_argument("-i", "--input", required=True, help="主视频")
    p.add_argument("-a", "--aux", required=True, help="闪光素材视频")
    p.add_argument("-o", "--output", required=True, help="输出文件")
    p.add_argument("--duration", type=float, default=None,
                   help="指定输出时长(秒;缺省时取主视频时长)")

    # ---- BL 骨架(样例) ----
    for name in ("bl-1", "bl-2"):
        p = sub.add_parser(name, help=f"{name.upper()}:BL 冻结流水线")
        p.add_argument("-i", "--input", required=True)
        p.add_argument("-a", "--aux", required=True, help="背景视频")
        p.add_argument("--out-dir", required=True)
        p.add_argument("--artist", default="")
        p.add_argument("--title", default="")
        p.add_argument("--date", default="")

    args = parser.parse_args(argv)
    try:
        result = _dispatch(args)
    except Exception as exc:
        print(f"失败: {exc}", file=sys.stderr)
        return 1
    if result:
        print(result)
    return 0


def _dispatch(args: argparse.Namespace) -> str | None:
    cmd = args.cmd
    if cmd in ("pure-ab", "interleave-105-35", "fanzhuan-ab"):
        _ensure_parent(args.output)
        fn = {
            "pure-ab": xhs_new.pure_ab,
            "interleave-105-35": xhs_new.interleave_105_35,
            "fanzhuan-ab": xhs_new.fanzhuan_ab,
        }[cmd]
        return fn(args.input, args.aux, args.output)
    if cmd == "ab-180":
        _ensure_parent(args.output)
        return xhs_hist.ab_180(args.input, args.output, aux=args.aux,
                               aux_gen=args.aux_gen, seed=args.seed)
    if cmd == "new-120":
        _ensure_parent(args.output)
        return xhs_new.new_120(args.input, args.output, seed=args.seed)
    if cmd == "xin-fanzhuan":
        _ensure_parent(args.output)
        stem = Path(args.input).stem
        out = str(Path(args.output).parent / f"{stem}_新反转_.mp4")
        xhs_new.xin_fanzhuan(args.input, args.aux, out)
        if out != args.output:
            Path(out).replace(args.output)
        return args.output
    if cmd == "one-b-three-a":
        _ensure_parent(args.output)
        return xhs_hist.one_b_three_a(args.input, args.aux, args.output)
    if cmd == "frame-gap":
        _ensure_parent(args.output)
        return xhs_new.frame_gap(args.input, args.aux, args.output,
                                  period=args.period, count=args.count,
                                  effect=args.effect,
                                  custom_filter=args.custom_filter)
    if cmd == "random-frame-ab":
        _ensure_parent(args.output)
        return xhs_hist.random_frame_ab(
            args.input, args.aux, args.output, method=args.method,
            seed=args.seed, period=args.period, count=args.count,
            opacity=args.opacity, effect=args.effect)
    if cmd == "single-frame-ab":
        _ensure_parent(args.output)
        return xhs_hist.single_frame_ab(
            args.input, args.aux, args.output, frame_time=args.frame_time,
            opacity=args.opacity, swap_orient=args.swap_orient)
    if cmd == "realshot-ab":
        _ensure_parent(args.output)
        return xhs_hist.realshot_ab(
            args.input, args.aux, args.output, opacity=args.opacity,
            duration_from_aux=args.duration_from_aux)
    if cmd == "double-frame":
        _ensure_parent(args.output)
        return xhs_hist.double_frame(args.input, args.aux, args.output,
                                     opacity=args.opacity)
    if cmd == "overlay-120":
        _ensure_parent(args.output)
        return xhs_new.overlay_120(args.input, args.aux, args.output)
    if cmd == "magic-frame":
        _ensure_parent(args.output)
        return xhs_new.magic_frame(args.input, args.aux, args.output,
                                   audio_bitrate=args.audio_bitrate,
                                   audio_mode=args.audio_mode)
    if cmd == "ab-audio":
        _ensure_parent(args.output)
        from app.media import ab_audio
        params = ab_audio.ABParams(
            mode=args.mode, alpha=args.alpha, deep=args.deep,
            interfere_vol=args.interfere_vol, time_mode=args.time_mode)
        stats = ab_audio.process_video_ab_hide(
            args.input, args.output, params, audio_b=args.aux)
        return (f"{args.output} (Mid_RMS={stats['mid_rms']:.4f}, "
                f"Side_RMS={stats['side_rms']:.4f}, corr={stats['corr']:.3f})")
    if cmd == "flash-frame":
        _ensure_parent(args.output)
        return xhs_new.flash_frame(args.input, args.output, aux=args.aux,
                                   seed=args.seed)
    if cmd == "three-frame-bg":
        _ensure_parent(args.output)
        stem = Path(args.input).stem
        out = str(Path(args.output).parent / f"{stem}_三帧.mp4")
        xhs_hist.three_frame_bg(args.input, args.aux, out,
                                opacity=args.opacity)
        if out != args.output:
            Path(out).replace(args.output)
        return args.output
    if cmd == "batch-908":
        outs = xhs_hist.batch_908(args.input_dir, args.out_dir,
                                  aux_dir=args.aux_dir,
                                  auto_aux=args.auto_aux,
                                  effect=args.effect, seed=args.seed)
        return "\n".join(outs) if outs else None
    if cmd == "dongtai-a":
        _ensure_parent(args.output)
        return xhs_new.dongtai_a(args.input, args.output, bg=args.bg,
                                 bg_gen=args.bg_gen, seed=args.seed,
                                 margin=args.margin)
    if cmd == "life-blend":
        _ensure_parent(args.output)
        return xhs_new.life_blend(args.input, args.output, seed=args.seed,
                                  a=args.a, b=args.b, hue=args.hue)
    if cmd == "card-duration":
        _ensure_parent(args.output)
        return xhs_hist.card_duration(
            args.input, args.output, seed=args.seed,
            target_seconds=args.target_seconds,
            elst_mode="real" if args.keep_elst_real else "fake")
    if cmd == "gen-bg":
        _ensure_parent(args.output)
        background_gen.generate(args.kind, args.output, args.width,
                                args.height, args.duration, seed=args.seed,
                                fps=args.fps)
        info = probe(args.output)
        return (f"{args.output} ({info.width}x{info.height}, "
                f"{info.duration:.1f}s)")
    if cmd == "heiwu-batch":
        _ensure_parent(args.output)
        return heiwu.batch_heiwu(args.input, args.output, mode=args.mode)
    if cmd in ("yuan-data", "yuan-data2"):
        sec = heiwu.parse_hms(args.duration) if args.duration else None
        fn = heiwu.yuan_data if cmd == "yuan-data" else heiwu.yuan_data2
        return fn(args.input, args.out_dir, disguise_sec=sec, mode=args.mode)
    if cmd == "short-duration":
        _ensure_parent(args.output)
        return heiwu.short_duration(args.input, args.output,
                                    args.display_seconds)
    if cmd == "fingerprint":
        if args.mode == "weird8":
            sar = None
            if args.sar:
                w, h = args.sar.split(":")
                sar = (int(w), int(h))
            rep = fingerprint.patch_weird8(
                args.input, sar=sar or fingerprint.WEIRD8_SAR)
        else:
            if args.target_seconds is None:
                raise ValueError("cvj/weird4 需要 --target-seconds")
            if args.mode == "cvj":
                rep = fingerprint.patch_cvj(
                    args.input, args.target_seconds, seed=args.seed,
                    keep_elst_real=not args.elst_fake)
            else:
                rep = fingerprint.patch_weird4(
                    args.input, args.target_seconds, seed=args.seed)
        return f"{args.input} [{rep.mode}] {rep.details}"
    if cmd in ("bl-1", "bl-2"):
        return bl.run_bl(args.input, args.aux, args.out_dir,
                         mode="BL-1" if cmd == "bl-1" else "BL-2",
                         artist=args.artist, title=args.title,
                         date=args.date)
    if cmd == "longmen-120":
        _ensure_parent(args.output)
        duration = args.duration
        if duration is None:
            info = probe(args.input)
            duration = info.duration
        return longmen_120.longmen_120(args.input, args.output, duration=duration)
    if cmd == "baoshan":
        _ensure_parent(args.output)
        duration = args.duration
        if duration is None:
            info = probe(args.input)
            duration = info.duration
        return baoshan.baoshan(args.input, args.aux, args.output, duration=duration)
    return None


if __name__ == "__main__":
    raise SystemExit(main())
