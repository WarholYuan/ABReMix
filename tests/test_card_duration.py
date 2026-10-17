#!/usr/bin/env python3
"""卡时长精确版专项验证(独立于 run_all 的全量实跑)。

用法: python3 tests/test_card_duration.py
素材: tests/samples/main_land.mp4 (1280x720@30fps, 10s, 300 帧)
断言:
  1. box 级: mvhd/tkhd/mdhd/dirty stts/elst 全口径对齐目标 1800s,
     stts Σcount 不变(视频 300 / 音频原始采样数), elst 首段=目标;
  2. ffprobe: format 与每条 stream 的 duration 均 ≈1800s;
  3. 帧数: -count_frames 视频 300 帧与原片一致, 音频帧数与原片一致;
  4. 全片 ffmpeg 解码无错误;
  5. cvj 兼容口径 (--keep-elst-real 等价 elst_mode="real"):
     box 级 elst 保持 10000(真实 10s), 其余口径仍对齐目标。
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.modes.xhs_hist import card_duration          # noqa: E402
from app.postprocess.mp4box import inspect_durations  # noqa: E402

MAIN = ROOT / "tests" / "samples" / "main_land.mp4"
TARGET = 1800.0


def ffprobe(path: Path, *entries: str) -> dict:
    cmd = ["ffprobe", "-v", "error", "-of", "json"]
    for e in entries:
        cmd += ["-show_entries", e]
    cmd.append(str(path))
    return json.loads(subprocess.run(cmd, capture_output=True, text=True,
                                     check=True).stdout)


def count_frames(path: Path) -> dict[str, int]:
    data = ffprobe(path, "stream=codec_type,nb_read_frames")
    # -count_frames 需要单独跑
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-show_entries",
         "stream=codec_type,nb_read_frames", "-of", "json", str(path)],
        capture_output=True, text=True, check=True)
    data = json.loads(proc.stdout)
    return {s["codec_type"]: int(s["nb_read_frames"])
            for s in data["streams"]}


def main() -> int:
    failures = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        print(f"{'✓' if cond else '✗ FAIL'} {name} {detail}")
        if not cond:
            failures.append(name)

    base_info = inspect_durations(str(MAIN))
    base_frames = count_frames(MAIN)
    print(f"原片: tracks={[(t['samples'], t['stts_entries']) for t in base_info['tracks']]}, "
          f"frames={base_frames}")

    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "card.mp4"
        card_duration(str(MAIN), str(out), seed=42, target_seconds=TARGET)

        info = inspect_durations(str(out))
        check("mvhd 对齐", info["mvhd_duration"] ==
              int(TARGET * info["movie_timescale"]),
              f"mvhd={info['mvhd_duration']}")
        for i, t in enumerate(info["tracks"]):
            bt = base_info["tracks"][i]
            tgt_units = int(round(TARGET * t["timescale"]))
            check(f"轨道{i} mdhd 对齐", t["mdhd_duration"] == tgt_units)
            check(f"轨道{i} tkhd 对齐",
                  t["tkhd_duration"] == int(TARGET * info["movie_timescale"]))
            check(f"轨道{i} stts Σ时长对齐", t["stts_units"] == tgt_units,
                  f"stts_units={t['stts_units']}")
            check(f"轨道{i} stts Σcount 不变", t["samples"] == bt["samples"],
                  f"{t['samples']} vs {bt['samples']}")
            check(f"轨道{i} elst 首段=目标",
                  t["elst_segment_durations"][:1] ==
                  [int(TARGET * info["movie_timescale"])],
                  f"elst={t['elst_segment_durations']}")

        probe = ffprobe(out, "format=duration", "stream=codec_type,duration")
        fmt_dur = float(probe["format"]["duration"])
        check("ffprobe format 时长≈1800", abs(fmt_dur - TARGET) < 1.0,
              f"{fmt_dur:.3f}")
        for s in probe["streams"]:
            d = float(s["duration"])
            check(f"ffprobe {s['codec_type']} 流时长≈1800",
                  abs(d - TARGET) < 1.0, f"{d:.3f}")

        frames = count_frames(out)
        check("视频帧数不变", frames.get("video") == base_frames.get("video"),
              f"{frames} vs {base_frames}")
        check("音频帧数不变", frames.get("audio") == base_frames.get("audio"))

        dec = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out),
                              "-f", "null", "-"],
                             capture_output=True, text=True)
        check("全片解码无错误", dec.returncode == 0 and not dec.stderr.strip(),
              dec.stderr.strip()[:200])

        # cvj 兼容口径:elst 段时长应与阶段一编码产物(=card 的输入
        # 基线)一致,而非源素材的——阶段一重编码会重写 elst。
        stage1 = Path(td) / "stage1.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(MAIN), "-c:v", "libx264",
             "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-movflags", "+faststart", str(stage1)],
            capture_output=True, check=True)
        stage_info = inspect_durations(str(stage1))
        out2 = Path(td) / "card_real_elst.mp4"
        card_duration(str(MAIN), str(out2), seed=42,
                      target_seconds=TARGET, elst_mode="real")
        info2 = inspect_durations(str(out2))
        for i, t in enumerate(info2["tracks"]):
            check(f"[cvj] 轨道{i} elst 保持真实",
                  t["elst_segment_durations"] ==
                  stage_info["tracks"][i]["elst_segment_durations"],
                  f"elst={t['elst_segment_durations']} "
                  f"vs stage1={stage_info['tracks'][i]['elst_segment_durations']}")
            check(f"[cvj] 轨道{i} stts 仍对齐目标",
                  t["stts_units"] == int(round(TARGET * t["timescale"])))

    print("\n结果:", "全部通过" if not failures else f"{len(failures)} 项失败: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
