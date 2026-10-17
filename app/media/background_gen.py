"""程序化背景生成。

来源:方案 §6;《肥猫 3.3 逆向分析报告》§5.6(grid @0x49f7478、life @0x4a332e8)、
《肥猫 V2.7 逆向分析》「自动背景」(噪声/棋盘/波纹/点阵轮换 + 纯色动态背景、
乱色块/杂乱形状)。grid 的滤镜串逐字用 core/templates.py 的 FILTER_GRID 原文,
仅把随机抖动占位符按 seed 填充。

所有生成器统一签名:generate(kind, out, width, height, duration, seed, fps=…)
CLI 入口见 cli.py 的 gen-bg 子命令,可单独生成一段背景视频。
"""
from __future__ import annotations

import random
from pathlib import Path

from ..core.ffmpeg_resolve import ffmpeg_path
from ..core.runner import run_ffmpeg
from ..core.templates import FILTER_GRID, render

_X264 = ["-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p"]


def _run(cmd: list[str], duration: float, label: str) -> None:
    run_ffmpeg(cmd, total_duration=duration, label=label)


def gen_grid(out: str, width: int, height: int, duration: float,
             seed: int = 0, base_image: str | None = None) -> dict:
    """grid 动态网格背景(报告 §5.6 原文滤镜;输出中间文件原版为 grid.nut)。

    基底图:未给 base_image 时先生成一张棋盘格 PNG 作 zoompan 的输入
    (原版基底未在字符串中明示,此为复刻选择,见 README 已知差异)。
    抖动参数(报告注释:「数值在抓取附近随机抖动」)由 seed 决定,可复现。
    返回实际使用的抖动参数字典。
    """
    rng = random.Random(seed)
    params = {
        "zs": round(rng.uniform(0.02, 0.08), 3),    # zoom 步长
        "ax": round(rng.uniform(4.0, 14.0), 2),     # x 漂移幅度
        "bx": round(rng.uniform(25.0, 60.0), 2),    # x 漂移频率除数
        "ay": round(rng.uniform(4.0, 14.0), 2),     # y 漂移幅度
        "by": round(rng.uniform(25.0, 60.0), 2),    # y 漂移频率除数
        "hs": round(rng.uniform(1.0, 4.0), 2),      # hue 旋转速度
        "hb": round(rng.uniform(0.0, 360.0), 1),    # hue 初相位
    }
    tmp_base = base_image
    cleanup: list[str] = []
    if tmp_base is None:
        tmp_base = str(Path(out).with_suffix(".grid_base.png"))
        cleanup.append(tmp_base)
        _run(
            [ffmpeg_path(), "-y", "-f", "lavfi",
             "-i", f"color=c=0x202020:s={width}x{height}",
             "-vf", "drawgrid=w=64:h=64:t=3:color=0xC0C0C0",
             "-frames:v", "1", tmp_base],
            0.1, "grid-base",
        )
    fc = render(
        FILTER_GRID,
        gw=width, gh=height, ow=width, oh=height,
        zs=params["zs"], ax=params["ax"], bx=params["bx"],
        ay=params["ay"], by=params["by"], hs=params["hs"], hb=params["hb"],
    )
    _run(
        [ffmpeg_path(), "-y", "-loop", "1", "-i", tmp_base,
         "-filter_complex", fc, "-map", "[grid]",
         *_X264, "-t", f"{duration}", out],
        duration, "gen-grid",
    )
    for path in cleanup:
        Path(path).unlink(missing_ok=True)
    return params


def gen_life(out: str, width: int, height: int, duration: float,
             seed: int = 0, hue_speed: float = 5.0) -> None:
    """life 生命游戏背景(纯背景轨;与主轨的按行 blend 混合在 modes 里
    用 FILTER_LIFE_BLEND 原文完成)。参数对齐报告 §5.6 的 [bg] 分支:
    mold=10:rate=30:stitch=0 + scale flags=neighbor + hue 随时间旋转。"""
    rng = random.Random(seed)
    ratio = round(rng.uniform(0.2, 0.4), 3)
    lw, lh = max(2, width // 2), max(2, height // 2)
    src = (
        f"life=size={lw}x{lh}:mold=10:rate=30:ratio={ratio:.3f}"
        f":random_seed={seed}:death_color=black:life_color=white:stitch=0"
    )
    vf = (
        f"scale={width}:{height}:flags=neighbor,format=yuv420p,"
        f"hue=h='{hue_speed:.1f}*t'"
    )
    _run(
        [ffmpeg_path(), "-y", "-f", "lavfi", "-i", src,
         "-vf", vf, *_X264, "-t", f"{duration}", out],
        duration, "gen-life",
    )


# 经典自动背景(2.7 沿用,方案 §6:噪声/棋盘/波纹/点阵 + 纯色动态)。
# geq 表达式为常规实现(原版滤镜串未恢复)。
_GEQ_KINDS = {
    "noise": None,  # 走 color+noise 路线,不用 geq
    "checker": "if(eq(mod(floor(X/40)+floor(Y/40),2),0),255,32)",
    "wave": "128+120*sin((X+Y)/12+T*3)",
    "dots": "if(lt(hypot(mod(X,36)-18,mod(Y,36)-18),7),255,20)",
    "blocks": None,  # 走 cellauto 路线(乱色块/杂乱形状,方案 §6)
}


def gen_classic(kind: str, out: str, width: int, height: int,
                duration: float, seed: int = 0, fps: int = 30) -> None:
    """经典背景:noise / checker / wave / dots / color / blocks。"""
    ff = ffmpeg_path()
    if kind == "noise":
        cmd = [ff, "-y", "-f", "lavfi",
               "-i", f"color=c=0x808080:s={width}x{height}:r={fps}",
               "-vf", "noise=alls=60:allf=t+u,format=yuv420p",
               *_X264, "-t", f"{duration}", out]
    elif kind == "color":
        cmd = [ff, "-y", "-f", "lavfi",
               "-i", f"color=c=0x3366CC:s={width}x{height}:r={fps}",
               "-vf", "hue=h='20*t':s=1.2,format=yuv420p",
               *_X264, "-t", f"{duration}", out]
    elif kind == "blocks":
        # 乱色块/杂乱形状:cellauto 细胞自动机出块状随机图,邻近放大 + hue 旋转
        cmd = [ff, "-y", "-f", "lavfi",
               "-i", f"cellauto=size={max(2, width // 16)}x{max(2, height // 16)}"
                     f":rate={fps}:random_seed={seed}:ratio=0.4",
               "-vf", f"scale={width}:{height}:flags=neighbor,"
                      f"hue=h='15*t':s=1.3,format=yuv420p",
               *_X264, "-t", f"{duration}", out]
    elif kind in _GEQ_KINDS:
        expr = _GEQ_KINDS[kind]
        cmd = [ff, "-y", "-f", "lavfi",
               "-i", f"nullsrc=s={width}x{height}:r={fps}",
               "-vf", f"geq=lum='{expr}':cb=128:cr=128,format=yuv420p",
               *_X264, "-t", f"{duration}", out]
    else:
        raise ValueError(f"未知背景类型: {kind}(可选: grid/life/noise/checker/wave/dots/color/blocks)")
    _run(cmd, duration, f"gen-{kind}")


def gen_circles(out: str, width: int, height: int, duration: float,
                seed: int = 0, fps: int = 30, rings: int = 14,
                color_a: tuple[int, int, int] = (178, 0, 232),
                color_b: tuple[int, int, int] = (116, 0, 168)) -> None:
    """同心圆生成图案(紫色底 + 多层同心圆环)。

    用途:180 模式原版真品出片(hongshu180.mov)实测的辅轨就是一张紫色底
    同心圆生成图案(近似静止,个别帧有轻微变化),不是另一段视频。
    实现:geq 按像素到圆心的半径分环,两档紫色交替;半径带极慢的正弦
    呼吸偏移(±2.5px),使图案近似静止、仅个别帧有轻微变化,与参照物
    观感一致。圆心默认画布中心(seed 决定 ±2% 抖动),rings 为环数。
    """
    import math

    rng = random.Random(seed)
    cx = width / 2.0 + rng.uniform(-0.02, 0.02) * width
    cy = height / 2.0 + rng.uniform(-0.02, 0.02) * height
    rings = max(3, rings + rng.randint(-2, 2))
    max_r = math.hypot(max(cx, width - cx), max(cy, height - cy))
    rw = max_r / rings
    dist = f"(hypot(X-{cx:.1f},Y-{cy:.1f})+2.5*sin(T*0.9))"
    band = f"mod(floor({dist}/{rw:.2f}),2)"

    def _ch(a: int, b: int) -> str:
        return f"if(eq({band},0),{a},{b})"

    geq = (f"geq=r='{_ch(color_a[0], color_b[0])}'"
           f":g='{_ch(color_a[1], color_b[1])}'"
           f":b='{_ch(color_a[2], color_b[2])}'")
    _run(
        [ffmpeg_path(), "-y", "-f", "lavfi",
         "-i", f"nullsrc=s={width}x{height}:r={fps}",
         "-vf", f"{geq},format=yuv420p",
         *_X264, "-t", f"{duration}", out],
        duration, "gen-circles",
    )


def make_pattern_image(out: str, width: int, height: int, seed: int = 0) -> None:
    """生成一张随机图案静图(闪帧模式无辅轨时用,报告 §5.1:
    「或自动生成随机图案作辅助首帧」)。实现:棋盘格 + 噪声 + 色块,seed 可复现。"""
    rng = random.Random(seed)
    c1 = rng.choice(["0xC0392B", "0x2471A3", "0x1E8449", "0x7D3C98", "0xB7950B"])
    _run(
        [ffmpeg_path(), "-y", "-f", "lavfi",
         "-i", f"color=c={c1}:s={width}x{height}",
         "-vf", "drawgrid=w=48:h=48:t=4:color=white@0.8,noise=alls=25:allf=u",
         "-frames:v", "1", out],
        0.1, "pattern-image",
    )


def generate(kind: str, out: str, width: int, height: int, duration: float,
             seed: int = 0, fps: int = 30) -> None:
    """统一入口:kind ∈ grid/life/circles/noise/checker/wave/dots/color/blocks。"""
    if kind == "grid":
        gen_grid(out, width, height, duration, seed=seed)
    elif kind == "life":
        gen_life(out, width, height, duration, seed=seed)
    elif kind == "circles":
        gen_circles(out, width, height, duration, seed=seed, fps=fps)
    else:
        gen_classic(kind, out, width, height, duration, seed=seed, fps=fps)
