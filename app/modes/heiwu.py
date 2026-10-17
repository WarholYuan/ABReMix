"""黑五专区模式(方案 §5.5 / §7;批处理三模式参数为逆向实抓冻结值)。

- batch_heiwu:黑五·批处理三模式转码(方案附录 A 冻结参数);
- yuan_data / yuan_data2:黑五·元数据 / 元数据2(SKT4 式时长伪装;
  元数据2 的四档命令与元数据页参数均来自 3.3 脱壳镜像 tab_skt4 的
  抓包字符串,逐字录入);
- short_duration:伪短时长(方案 §7.3;原版走随包 Bento4
  mp4fragment+mp4edit 二次封装,本机无 Bento4,复刻用 mp4box 字节级
  等效实现并保留「失败回退 ffmpeg 直出」语义)。
"""
from __future__ import annotations

import re
from pathlib import Path

from ..core.ffmpeg_resolve import ffmpeg_path, ffprobe_path
from ..core.probe import probe
from ..core.runner import run_ffmpeg, run_checked
from ..postprocess import mp4box
from .common import make_temp_dir


# ---------------------------------------------------------------------------
# 黑五·批处理(三模式,参数冻结:方案 §5.5 / 附录 A)
# ---------------------------------------------------------------------------

def batch_heiwu(main: str, out: str, mode: int = 1) -> str:
    """黑五·批处理三模式。

    冻结参数(逆向实抓,不“修正”):
      模式一:pad 到 720x1280 后再 scale=720:1280;
      模式二:720x1280 pad + CRF17 + baseline profile;
      模式三:496x864 pad + ultrafast + 6000k + AAC 采样率 195247(怪值照抄)。
    """
    info = probe(main)
    if mode == 1:
        # 冻结配方「pad 到 720x1280 后再 scale=720:1280」;
        # pad 不接受比输入小的画布,前置等比缩小使 pad 合法(顺序不动)。
        vf = ("scale=720:1280:force_original_aspect_ratio=decrease,"
              "pad=720:1280:(ow-iw)/2:(oh-ih)/2:black,scale=720:1280,"
              "setsar=1")
        tail = ["-c:v", "libx264", "-preset", "fast", "-crf", "18",
                "-pix_fmt", "yuv420p"]
    elif mode == 2:
        vf = ("scale=720:1280:force_original_aspect_ratio=decrease,"
              "pad=720:1280:(ow-iw)/2:(oh-ih)/2:black,setsar=1")
        tail = ["-c:v", "libx264", "-preset", "fast", "-crf", "17",
                "-profile:v", "baseline", "-pix_fmt", "yuv420p"]
    elif mode == 3:
        vf = ("scale=496:864:force_original_aspect_ratio=decrease,"
              "pad=496:864:(ow-iw)/2:(oh-ih)/2:black,setsar=1")
        tail = ["-c:v", "libx264", "-preset", "ultrafast", "-b:v", "6000k",
                "-pix_fmt", "yuv420p"]
    else:
        raise ValueError(f"未知批处理模式: {mode}")
    cmd = [ffmpeg_path(), "-y", "-i", main, "-vf", vf,
           *tail, "-c:a", "aac"]
    if mode == 3:
        # AAC 195247(怪值照抄):方案曾注“采样率怪值”,实测 16.16 定点
        # 采样率字段装不下 195247(>65535),且标准 aac 编码器拒绝;
        # 码率 195247bps(≈190k)是唯一自洽解读,按码率冻结。
        cmd += ["-b:a", "195247"]
    cmd += ["-movflags", "+faststart", "-t", f"{info.duration}", out]
    run_ffmpeg(cmd, total_duration=info.duration, label=f"heiwu-batch-m{mode}")
    return out


# ---------------------------------------------------------------------------
# 黑五·元数据 / 元数据2(SKT4 式时长伪装)
# ---------------------------------------------------------------------------

# 公共头/尾(脱壳镜像 tab_skt4 抓包字符串,逐字):
# -tag:v avc1 -video_track_timescale 15360 -color_* bt709;
# 尾:-metadata:g encoding_tool=Wxmm_9020230808 -brand isom +faststart。
# SKT4_VF 的具体滤镜串在脱壳字符串中未恢复(常量引用),复刻留空
# 并显式标注——不发明未知滤镜。
_SKT4_HEAD_COLOR = [
    "-pix_fmt", "yuv420p", "-fps_mode", "cfr",
    "-tag:v", "avc1", "-video_track_timescale", "15360",
    "-color_primaries", "bt709", "-color_trc", "bt709",
    "-colorspace", "bt709",
]
_SKT4_TAIL = [
    "-metadata:g", "encoding_tool=Wxmm_9020230808",
    "-brand", "isom", "-movflags", "+faststart",
]

# 四档命令(CPU·模式一/二 逐字;GPU 两档仅存档,用户机器无 N 卡不接)
_SKT4_CPU_MODE1 = [
    "-c:v", "libx264", "-preset", "medium", "-refs", "4",
    "-b:v", "1000k", "-maxrate:v", "1200k", "-bufsize:v", "2000k",
    "-bf", "3", "-profile:v", "high", "-level:v", "4.1",
    "-c:a", "aac", "-profile:a", "aac_low", "-ar", "44100",
    "-ac", "2", "-b:a", "128k",
]
_SKT4_CPU_MODE2 = [
    "-c:v", "libx264", "-preset", "medium", "-refs", "4",
    "-bf", "2", "-g", "250", "-keyint_min", "250", "-sc_threshold", "0",
    "-b_strategy", "0", "-x264-params", "nal-hrd=vbr:force-cfr=1",
    "-b:v", "2000k", "-maxrate:v", "6000k", "-bufsize:v", "4000k",
    "-profile:v", "main", "-level:v", "3.1",
    "-c:a", "aac", "-profile:a", "aac_low", "-ar", "44100",
    "-ac", "2", "-b:a", "192k",
]
# GPU 档(存档备查,不接):h264_nvenc rc vbr;模式一 refs 2/bf 3/high@4.1
# 1000k-1200k/2000k aac 128k;模式二 refs 4/dpb 4/bf 2/g 250 no-scenecut
# b_adapt 0 forced-idr main@3.1 2000k-6000k/4000k aac 192k。


def parse_hms(text: str) -> float:
    """伪装时长解析:03:18:00 / 3:18 / 11880(秒)。"""
    text = text.strip()
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return float(text)
    parts = text.split(":")
    if len(parts) == 3:
        h, m, s = (float(x) for x in parts)
        return h * 3600 + m * 60 + s
    if len(parts) == 2:
        m, s = (float(x) for x in parts)
        return m * 60 + s
    raise ValueError(f"格式应为 时:分:秒,例如 03:18:00(收到: {text!r})")


def _next_indexed(out_dir: Path, stem: str, tag: str) -> Path:
    """{stem}_{tag}_{n}.mp4,n 取目录内未用的最小序号(元数据2 命名规则)。"""
    used = set()
    pat = re.compile(re.escape(f"{stem}_{tag}_") + r"(\d+)\.mp4$")
    for p in out_dir.iterdir():
        m = pat.match(p.name)
        if m:
            used.add(int(m.group(1)))
    n = 1
    while n in used:
        n += 1
    return out_dir / f"{stem}_{tag}_{n}.mp4"


def _post_verify(path: str) -> dict:
    """元数据2 后置校验:解 3 秒可播 + 关键帧检测(与原版同名检查)。"""
    run_checked([ffmpeg_path(), "-v", "error", "-t", "3", "-i", path,
                 "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"],
                label="skt4-verify-3s")
    out = run_checked([ffprobe_path(), "-v", "error", "-select_streams", "v:0",
                       "-show_entries", "frame=key_frame", "-of", "csv=p=0",
                       path], label="skt4-verify-keyframe")
    keyframes = sum(1 for ln in out.splitlines() if ln.strip() == "1")
    if keyframes < 1:
        raise RuntimeError("关键帧检测失败:成片无关键帧")
    return {"decode_3s": True, "keyframes": keyframes}


def encode_skt4(src: str, out_dir: str, mode: int = 1,
                disguise_sec: float | None = None, tag: str = "元数据2",
                verify: bool = True) -> str:
    """黑五·元数据2(tab_skt4):四档命令转码 + SKT4 式伪装时长 + 后置校验。

    输出 {stem}_{tag}_{n}.mp4;disguise_sec 缺省用原版 UI 默认 03:18:00。
    tag="元数据" 时即黑五·元数据页(同 SKT4 写法,命名不同)。
    """
    src_info = probe(src)
    fake_sec = disguise_sec if disguise_sec is not None else 3 * 3600 + 18 * 60
    out_root = Path(out_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    stem = Path(src).stem
    tmp = make_temp_dir(str(out_root / stem))
    part = str(tmp / f"{stem}_{tag}.part.mp4")

    venc = _SKT4_CPU_MODE1 if mode == 1 else _SKT4_CPU_MODE2
    cmd = [ffmpeg_path(), "-y", "-i", src,
           "-map", "0:0", "-map", "0:1?", "-map_metadata", "-1",
           "-map_chapters", "-1",
           *venc, *_SKT4_HEAD_COLOR, *_SKT4_TAIL,
           "-t", f"{src_info.duration}", part]
    run_ffmpeg(cmd, total_duration=src_info.duration,
               label=f"skt4-encode-m{mode}")
    if not Path(part).is_file():
        raise RuntimeError("转码未生成文件")

    stats = mp4box.disguise_duration_skt4(part, fake_sec)
    out_path = _next_indexed(out_root, stem, tag)
    Path(part).replace(out_path)
    result = str(out_path)
    if verify:
        v = _post_verify(result)
        result += (f" [校验通过: 解3秒 OK, 关键帧 {v['keyframes']} 个, "
                   f"伪装时长 {fake_sec:.0f}s, mvhd={stats['mvhd_duration']}]")
    return result


def yuan_data(src: str, out_dir: str, disguise_sec: float | None = None,
              mode: int = 1) -> str:
    """黑五·元数据(tab 元数据):SKT4 式时长伪装(命名 {stem}_元数据_{n})。"""
    return encode_skt4(src, out_dir, mode=mode, disguise_sec=disguise_sec,
                       tag="元数据", verify=False)


def yuan_data2(src: str, out_dir: str, disguise_sec: float | None = None,
               mode: int = 1) -> str:
    """黑五·元数据2(tab_skt4):四档命令 + 伪装时长 + 后置校验。"""
    return encode_skt4(src, out_dir, mode=mode, disguise_sec=disguise_sec,
                       tag="元数据2", verify=True)


# ---------------------------------------------------------------------------
# 伪短时长(方案 §7.3;Bento4 缺位时的 mp4box 等效实现)
# ---------------------------------------------------------------------------

def short_duration(main: str, out: str, display_seconds: float) -> str:
    """伪短时长:让长片显示成短时长。

    原版流程:ffmpeg 出片 → Bento4 mp4fragment 分片 + mp4edit 改写。
    本机无 Bento4,复刻用 mp4box 字节级等效实现:mvhd/mdhd/tkhd 与
    elst 首段时长全部写为显示值(与卡时长方向相反,口径同 SKT4)。
    Bento4 阶段失败的「回退 ffmpeg 直出」语义保留:任何后处理异常
    时保留 ffmpeg 直出结果并打印警告。
    """
    info = probe(main)
    tmp = make_temp_dir(out)
    stage1 = str(tmp / "short_stage1.mp4")
    run_ffmpeg(
        [ffmpeg_path(), "-y", "-i", main,
         "-c:v", "libx264", "-preset", "fast", "-crf", "18",
         "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-movflags", "+faststart", "-t", f"{info.duration}", stage1],
        total_duration=info.duration, label="short-stage1",
    )
    Path(stage1).replace(out)
    try:
        mp4box.disguise_duration_skt4(out, display_seconds)
    except Exception as exc:  # 原版同款回退:保留 ffmpeg 直出
        import sys
        print(f"[short-duration] 后处理失败,回退直出: {exc}", file=sys.stderr)
    return out
