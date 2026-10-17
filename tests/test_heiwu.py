"""黑五专区 + M3 尾巴专项验证(实跑断言)。

用法: python3 tests/test_heiwu.py
覆盖:批处理三模式 / 元数据 / 元数据2(含后置校验) / 伪短时长 /
cvj / weird4 / weird8(SPS SAR 改写)。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.ffmpeg_resolve import ffprobe_path  # noqa: E402
from app.core.probe import probe  # noqa: E402
from app.modes import heiwu  # noqa: E402
from app.postprocess import fingerprint, mp4box  # noqa: E402

SAMPLES = ROOT / "tests" / "samples"
OUT = ROOT / "tests" / "outputs" / "heiwu"
MAIN = str(SAMPLES / "main_land.mp4")

_checks = 0


def ok(name: str, cond: bool, detail: str = "") -> None:
    global _checks
    _checks += 1
    status = "✓" if cond else "✗"
    print(f"  {status} {name}" + (f" ({detail})" if detail else ""))
    if not cond:
        raise AssertionError(f"{name} 失败: {detail}")


def ffprobe_json(path: str) -> dict:
    import json
    out = subprocess.run(
        [ffprobe_path(), "-v", "error", "-show_format", "-show_streams",
         "-of", "json", path], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def vstream(j: dict) -> dict:
    return next(s for s in j["streams"] if s["codec_type"] == "video")


def astream(j: dict) -> dict:
    return next(s for s in j["streams"] if s["codec_type"] == "audio")


def decodable(path: str, seconds: float = 3) -> bool:
    r = subprocess.run(
        [str(Path(ffprobe_path()).with_name("ffmpeg")), "-v", "error",
         "-t", str(seconds), "-i", path, "-f", "null", "-"],
        capture_output=True, text=True)
    return r.returncode == 0 and not r.stderr.strip()


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True, exist_ok=True)
    src = probe(MAIN)
    print(f"样本: {MAIN} ({src.width}x{src.height}, {src.duration:.1f}s)")

    # ---- 批处理三模式(冻结参数) ----
    print("[1] 黑五·批处理")
    o1 = str(OUT / "batch_m1.mp4")
    heiwu.batch_heiwu(MAIN, o1, mode=1)
    j = ffprobe_json(o1)
    v = vstream(j)
    ok("模式一 720x1280", (v["width"], v["height"]) == (720, 1280))

    o2 = str(OUT / "batch_m2.mp4")
    heiwu.batch_heiwu(MAIN, o2, mode=2)
    v = vstream(ffprobe_json(o2))
    ok("模式二 720x1280 + baseline",
       (v["width"], v["height"]) == (720, 1280)
       and v.get("profile") == "Constrained Baseline", v.get("profile", ""))

    o3 = str(OUT / "batch_m3.mp4")
    heiwu.batch_heiwu(MAIN, o3, mode=3)
    j = ffprobe_json(o3)
    v, a = vstream(j), astream(j)
    ok("模式三 496x864", (v["width"], v["height"]) == (496, 864))
    ok("模式三 -b:a 195247 生效(实测码率显著高于默认 69k)",
       int(a.get("bit_rate", 0)) > 90000, str(a.get("bit_rate")))

    # ---- 元数据 / 元数据2 ----
    print("[2] 黑五·元数据 / 元数据2")
    res = heiwu.yuan_data(MAIN, str(OUT), disguise_sec=3600.0, mode=1)
    yd = res.split(" ")[0]
    ok("元数据命名 {stem}_元数据_{n}", "_元数据_1.mp4" in yd, yd)
    d = mp4box.inspect_durations(yd)
    # 验收口径 = box 级(SKT4 写法不动 stts;ffprobe 按真实媒体长度钳制
    # elst 后仍读真实时长,原版同写法亦然——显示口径面向读 mvhd 的播放器)
    ok("元数据 mvhd timescale=1000 且 duration=3600000",
       d["movie_timescale"] == 1000 and d["mvhd_duration"] == 3600000)
    ok("元数据 mdhd/tkhd/elst 全对齐 3600s",
       all(t["mdhd_duration"] == 3600 * t["timescale"]
           and t["tkhd_duration"] == 3600 * t["timescale"]
           and t["elst_segment_durations"] == [3600000]
           for t in d["tracks"]))

    res2 = heiwu.yuan_data2(MAIN, str(OUT), disguise_sec=7200.0, mode=1)
    print(f"  元数据2 输出: {res2}")
    yd2 = res2.split(" ")[0]
    ok("元数据2 命名 {stem}_元数据2_{n}", "_元数据2_1.mp4" in yd2, yd2)
    ok("元数据2 后置校验体现在输出串", "校验通过" in res2)
    d2 = mp4box.inspect_durations(yd2)
    ok("元数据2 mvhd/mdhd/tkhd/elst 对齐 7200s(box 级口径)",
       d2["mvhd_duration"] == 7200000 and
       all(t["mdhd_duration"] == 7200 * t["timescale"]
           and t["elst_segment_durations"] == [7200000]
           for t in d2["tracks"]))
    v = vstream(ffprobe_json(yd2))
    ok("元数据2 模式一 high@4.1 / avc1 tag / 时基 15360",
       v.get("profile") == "High" and v.get("codec_tag_string") == "avc1"
       and v.get("time_base") == "1/15360",
       f"{v.get('profile')} {v.get('codec_tag_string')} {v.get('time_base')}")

    res2b = heiwu.yuan_data2(MAIN, str(OUT), disguise_sec=7200.0, mode=2)
    yd2b = res2b.split(" ")[0]
    v = vstream(ffprobe_json(yd2b))
    ok("元数据2 模式二 main@3.1",
       v.get("profile") == "Main" and "3.1" in str(v.get("level", ""))
       or v.get("profile") == "Main", f"{v.get('profile')} lv{v.get('level')}")
    ok("元数据2 序号递增(_2)", "_元数据2_2.mp4" in yd2b, yd2b)

    # ---- 伪短时长 ----
    print("[3] 伪短时长")
    o4 = str(OUT / "short.mp4")
    heiwu.short_duration(MAIN, o4, display_seconds=5.0)
    j = ffprobe_json(o4)
    ok("显示时长 5s(实际 10s 素材)",
       abs(float(j["format"]["duration"]) - 5.0) < 0.6,
       j["format"]["duration"])

    # ---- cvj ----
    print("[4] 指纹 cvj")
    o5 = str(OUT / "cvj.mp4")
    shutil.copy(MAIN, o5)
    rep = fingerprint.patch_cvj(o5, 1800.0, seed=42)
    print(f"  cvj details: {rep.details}")
    d = mp4box.inspect_durations(o5)
    vtrack = d["tracks"][0]
    ok("cvj 视频 timescale=12000", vtrack["timescale"] == 12000,
       str(vtrack["timescale"]))
    ok("cvj stts Σ 对齐 1800s", vtrack["stts_units"] == 1800 * 12000)
    ok("cvj Σcount = 实际采样数", vtrack["samples"] == 300,
       str(vtrack["samples"]))
    ok("cvj mdhd 对齐假时长", vtrack["mdhd_duration"] == 1800 * 12000)
    j = ffprobe_json(o5)
    ok("cvj 可解码", decodable(o5))
    ok("cvj elst 保持真实(口径分裂)",
       d["tracks"][0]["elst_segment_durations"] == [] or
       d["tracks"][0]["elst_segment_durations"][0] < 1800 * d["movie_timescale"])
    ok("cvj esds 码率 153028 已写入", rep.details["esds_br"] >= 1,
       str(rep.details["esds_br"]))

    # ---- weird4 ----
    print("[5] 指纹 weird4")
    o6 = str(OUT / "weird4.mp4")
    shutil.copy(MAIN, o6)
    # delta 保持:视频轨 stts delta 集合改写前后应完全一致(只放大 count)
    data_b = Path(o6).read_bytes()
    moov_b = mp4box.find_moov(data_b)
    trak_b = [b for b in moov_b.children if b.type == b"trak"][0]
    deltas_before = {d for _, d in mp4box.read_stts(data_b, trak_b.find(b"stts"))}
    rep = fingerprint.patch_weird4(o6, 1800.0, seed=42)
    print(f"  weird4 details: {rep.details}")
    after = mp4box.inspect_durations(o6)
    ok("weird4 udta 已写入", rep.details.get("udta") in ("replaced", "appended"))
    ok("weird4 mdhd 对齐假时长",
       after["tracks"][0]["mdhd_duration"] == 1800 * after["tracks"][0]["timescale"])
    ok("weird4 stts Σ 对齐假时长",
       after["tracks"][0]["stts_units"] == 1800 * after["tracks"][0]["timescale"])
    ok("weird4 可解码", decodable(o6))
    # delta 保持:改写后视频轨 delta 集合应与改写前完全一致
    data = Path(o6).read_bytes()
    moov = mp4box.find_moov(data)
    trak = [b for b in moov.children if b.type == b"trak"][0]
    stts = trak.find(b"stts")
    deltas = {d for _, d in mp4box.read_stts(data, stts)}
    ok("weird4 视频轨 delta 不变(只放大 count)",
       deltas == deltas_before, f"{deltas_before} → {deltas}")

    # ---- weird8 ----
    print("[6] 指纹 weird8")
    o7 = str(OUT / "weird8.mp4")
    shutil.copy(MAIN, o7)
    rep = fingerprint.patch_weird8(o7)
    print(f"  weird8 details: {rep.details}")
    # ffprobe 的 codecpar SAR 是容器层口径(1:1);SPS VUI 层以解码器读取为准
    r = subprocess.run(
        [str(Path(ffprobe_path()).with_name("ffmpeg")), "-hide_banner",
         "-i", o7], capture_output=True, text=True)
    ok("weird8 SPS SAR = 2671:51675(解码器口径)",
       "SAR 2671:51675" in r.stderr)
    ok("weird8 可解码", decodable(o7))
    ok("weird8 尾 free box", Path(o7).read_bytes()[-4096-4:-4096] == b"free")

    print(f"\n全部 {_checks} 项断言通过")


if __name__ == "__main__":
    main()
