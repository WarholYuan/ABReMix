#!/usr/bin/env python3
"""魔术帧专项验证。

用法: python3 tests/test_magic_frame.py
夹具: lavfi 纯红主轨(带正弦音轨)+ 纯蓝辅轨,各 3s / 640x360@30。
断言(依据 3.3 脱壳恢复的配方,见 app/modes/xhs_new.magic_frame):
  1. 输出 90fps、时长 ≈3s;
  2. 视频帧数 = 90*3 - 2 = 268(剔头 2 帧,select='not(eq(n,0)+eq(n,1))');
  3. 逐帧颜色分类呈严格规律:n>=1 时 n≡1 (mod 3) 为辅轨蓝帧,
     其余为主轨红帧(即每 3 帧 2 主 1 辅);
  4. 音轨存在且全片解码无错误;
  5. 真实素材冒烟(main_land + aux_land):90fps、时长 ≈10s、解码无错误;
  6. 无音轨主素材不报错。
注:原版第 4 步 AB 音频隐藏(M4)未落地,音频为原声直通,本测试不验隐藏层。
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.modes.xhs_new import magic_frame  # noqa: E402


def sh(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def probe_json(path: Path) -> dict:
    out = sh(["ffprobe", "-v", "error", "-show_entries",
              "format=duration", "-show_entries",
              "stream=codec_type,avg_frame_rate", "-of", "json", str(path)])
    return json.loads(out.stdout)


def count_frames(path: Path) -> dict[str, int]:
    out = sh(["ffprobe", "-v", "error", "-count_frames", "-show_entries",
              "stream=codec_type,nb_read_frames", "-of", "json", str(path)])
    return {s["codec_type"]: int(s["nb_read_frames"])
            for s in json.loads(out.stdout)["streams"]}


def classify(path: Path) -> str:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vf",
         "scale=8:8,format=rgb24", "-f", "rawvideo", "-"],
        capture_output=True).stdout
    frames = [raw[i:i + 192] for i in range(0, len(raw) - 191, 192)]
    out = []
    for f in frames:
        r = sum(f[0::3]) / 64
        b = sum(f[2::3]) / 64
        out.append("R" if r > b else "B")
    return "".join(out)


def main() -> int:
    failures = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        print(f"{'✓' if cond else '✗ FAIL'} {name} {detail}")
        if not cond:
            failures.append(name)

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        red, blue = td / "red.mp4", td / "blue.mp4"
        sh(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
            "color=c=red:s=640x360:r=30:d=3", "-f", "lavfi", "-i",
            "sine=frequency=440:d=3", "-c:v", "libx264", "-pix_fmt",
            "yuv420p", "-c:a", "aac", "-shortest", str(red)])
        sh(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
            "color=c=blue:s=640x360:r=30:d=3", "-c:v", "libx264",
            "-pix_fmt", "yuv420p", "-an", str(blue)])

        out = td / "magic.mp4"
        magic_frame(str(red), str(blue), str(out))
        data = probe_json(out)
        v = next(s for s in data["streams"] if s["codec_type"] == "video")
        fps = eval(v["avg_frame_rate"])  # 形如 "90/1",受控输入
        check("90fps", abs(fps - 90) < 0.5, f"fps={fps}")
        check("时长≈3s", abs(float(data["format"]["duration"]) - 3) < 0.15,
              data["format"]["duration"])
        frames = count_frames(out)
        check("视频帧数=268(270-剔头2)", frames.get("video") == 268,
              f"{frames}")
        check("音轨存在", "audio" in frames and frames["audio"] > 100)
        pattern = classify(out)
        ok_pattern = all(
            (ch == "B") == (n % 3 == 1) for n, ch in enumerate(pattern) if n >= 1)
        check("交织规律(n>=1: n≡1 mod3 为辅帧)", ok_pattern,
              f"前30帧={pattern[:30]}")
        check("主辅帧数比≈2:1",
              abs(pattern.count("R") - 2 * pattern.count("B")) <= 2,
              f"R={pattern.count('R')} B={pattern.count('B')}")
        dec = sh(["ffmpeg", "-v", "error", "-i", str(out), "-f", "null", "-"])
        check("全片解码无错误", dec.returncode == 0 and not dec.stderr.strip(),
              dec.stderr.strip()[:150])

        # 无音轨冒烟
        out2 = td / "magic_noaudio.mp4"
        magic_frame(str(blue), str(red), str(out2))
        dec2 = sh(["ffmpeg", "-v", "error", "-i", str(out2),
                   "-f", "null", "-"])
        check("无音轨主素材可跑", dec2.returncode == 0 and out2.exists())

        # 真实素材冒烟
        samples = ROOT / "tests" / "samples"
        out3 = td / "magic_real.mp4"
        magic_frame(str(samples / "main_land.mp4"),
                    str(samples / "aux_land.mp4"), str(out3))
        data3 = probe_json(out3)
        check("真实素材时长≈10s",
              abs(float(data3["format"]["duration"]) - 10) < 0.15,
              data3["format"]["duration"])
        dec3 = sh(["ffmpeg", "-v", "error", "-i", str(out3),
                   "-f", "null", "-"])
        check("真实素材解码无错误", dec3.returncode == 0)

    print("\n结果:", "全部通过" if not failures else f"{len(failures)} 项失败: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
