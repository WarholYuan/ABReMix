#!/usr/bin/env python3
"""120fps 隔帧叠加(overlay-120)专项验证。

用法: python3 tests/test_overlay_120.py
夹具:主 = 红色 320x240@30 + 440Hz 音轨,辅 = 蓝色 320x240@30,各 2s。
断言:
  输出帧率 120fps、画布按横屏规则 1280x720
  帧数 ≈ 240(2s x 120,容差 ±3)
  逐帧分类:偶数帧为红(主)、奇数帧为蓝(辅整帧叠加),符合率 >= 95%
  竖屏主轨时画布为 720x1280
  音轨保留(主轨 440Hz 经 AAC 后可检出)
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.modes.xhs_new import overlay_120  # noqa: E402


def sh(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def make_clip(path: Path, color: str, size: str, with_audio: bool) -> None:
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
           f"color=c={color}:s={size}:r=30:d=2"]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440:d=2",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                "-shortest", str(path)]
    else:
        cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(path)]
    sh(cmd)


def probe_field(path: Path, entry: str) -> str:
    r = sh(["ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", f"stream={entry}", "-of", "csv=p=0", str(path)])
    return r.stdout.strip()


def frames_rgb(path: Path, w: int, h: int) -> np.ndarray:
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo",
         "-pix_fmt", "rgb24", "-"],
        capture_output=True)
    arr = np.frombuffer(r.stdout, dtype=np.uint8)
    n = arr.size // (w * h * 3)
    return arr[:n * w * h * 3].reshape(n, h, w, 3)


def main() -> int:
    failures = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        print(f"{'✓' if cond else '✗ FAIL'} {name} {detail}")
        if not cond:
            failures.append(name)

    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        main_land = td / "main_land.mp4"
        aux = td / "aux.mp4"
        make_clip(main_land, "red", "320x240", True)
        make_clip(aux, "blue", "320x240", False)
        out = td / "overlay120.mp4"
        overlay_120(str(main_land), str(aux), str(out))

        rate = probe_field(out, "avg_frame_rate")
        check("帧率 120fps", rate in ("120/1", "120/1.0"), rate)
        wh = probe_field(out, "width,height")
        check("横屏画布 1280x720", wh.replace(",", "x") in ("1280x720",),
              wh)
        frames = frames_rgb(out, 1280, 720)
        check("帧数 ≈240", abs(frames.shape[0] - 240) <= 3,
              f"n={frames.shape[0]}")
        means = frames.reshape(frames.shape[0], -1, 3).mean(axis=1)
        is_red = (means[:, 0] > 150) & (means[:, 2] < 100)
        is_blue = (means[:, 2] > 150) & (means[:, 0] < 100)
        n = frames.shape[0]
        ok = sum(1 for i in range(n)
                 if (is_red[i] if i % 2 == 0 else is_blue[i]))
        check("奇数帧叠辅/偶数帧主轨 符合率>=95%", ok >= 0.95 * n,
              f"{ok}/{n}")
        a = sh(["ffprobe", "-v", "error", "-select_streams", "a",
                "-show_entries", "stream=codec_type", "-of", "csv=p=0",
                str(out)])
        check("音轨保留", "audio" in a.stdout, a.stdout.strip())

        main_port = td / "main_port.mp4"
        make_clip(main_port, "red", "240x320", False)
        out_p = td / "overlay120_p.mp4"
        overlay_120(str(main_port), str(aux), str(out_p))
        wh = probe_field(out_p, "width,height")
        check("竖屏画布 720x1280", wh.replace(",", "x") == "720x1280", wh)

    print("\n结果:", "全部通过" if not failures
          else f"{len(failures)} 项失败: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
