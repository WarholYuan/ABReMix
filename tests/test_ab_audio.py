#!/usr/bin/env python3
"""AB 音频隐藏专项验证。

用法: python3 tests/test_ab_audio.py
夹具:A = 红色视频 + 440Hz 正弦(立体声同相,幅 0.5);
      B = 880Hz 正弦 WAV(幅 0.5);时长 2s / 44100Hz。
测量:对输出音频的单声道下混(Mid=(L+R)/2)与差分(Side=(L-R)/2)
分别做 Goertzel 单频能量检测(440 vs 880),以及 RMS/corr。
断言:
  模式1: Mid 被 880(B)主导(>10x),Side 被 440(A)主导(>10x)
  模式2: Mid 被 440(A)主导,Side 被 880(B)主导
  模式3: 同模式1 的主导关系 + 19kHz 超声载波可检出
  模式5: WAV 层 Mid RMS 严格为 0;AAC 封装后残余 < 3%(原生 AAC
    量化噪声,实测 ≈-35dB,与原版同链同量级);corr(L,R) < -0.99
  防重复: 模式5 产物再喂 process_audio_hide → AlreadyProcessedError
  魔术帧集成: magic-frame --audio-mode 5 等价调用后,
    输出音轨单声道 RMS ≈ 0 且视频仍为 90fps
注:AAC 封装会引入频谱泄漏,主导阈值取 10x(实测通常远高于此)。
"""
from __future__ import annotations

import math
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.media import ab_audio                     # noqa: E402
from app.media.ab_audio import (ABParams,          # noqa: E402
                                AlreadyProcessedError,
                                extract_pcm_wav, process_audio_hide,
                                read_wav_stereo)
from app.modes.xhs_new import magic_frame          # noqa: E402

SR = 44100


def sh(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def goertzel(x: np.ndarray, sr: int, freq: float) -> float:
    n = x.shape[0]
    k = 2 * math.pi * freq / sr
    coeff = 2 * math.cos(k)
    s1 = s2 = 0.0
    for v in x:
        s0 = float(v) + coeff * s1 - s2
        s2, s1 = s1, s0
    power = s1 * s1 + s2 * s2 - coeff * s1 * s2
    return power / (n * n)


def out_wav(video: Path, td: Path, name: str) -> Path:
    wav = td / name
    extract_pcm_wav(str(video), str(wav), SR)
    return wav


def main() -> int:
    failures = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        print(f"{'✓' if cond else '✗ FAIL'} {name} {detail}")
        if not cond:
            failures.append(name)

    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        a_video = td / "a.mp4"
        sh(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
            "color=c=red:s=320x240:r=30:d=2", "-f", "lavfi", "-i",
            "sine=frequency=440:sample_rate=44100:d=2",
            "-filter_complex", "[1:a]volume=0.5,pan=stereo|c0=c0|c1=c0[a]",
            "-map", "0:v", "-map", "[a]", "-c:v", "libx264",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(a_video)])
        b_wav = td / "b.wav"
        sh(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
            "sine=frequency=880:sample_rate=44100:d=2",
            "-filter_complex", "[0:a]volume=0.5,pan=stereo|c0=c0|c1=c0[a]",
            "-map", "[a]", "-acodec", "pcm_s16le", str(b_wav)])
        a_wav = out_wav(a_video, td, "a_in.wav")
        a_left, _, _ = read_wav_stereo(str(a_wav))
        a_mono_rms = float(np.sqrt(np.mean(
            ((a_left) ** 2).astype(np.float64))))

        def run_mode(mode: int) -> tuple[np.ndarray, np.ndarray]:
            out_video = td / f"out_mode{mode}.mp4"
            ab_audio.process_video_ab_hide(
                str(a_video), str(out_video),
                ABParams(mode=mode), audio_b=str(b_wav))
            wav = out_wav(out_video, td, f"out_mode{mode}.wav")
            l, r, _ = read_wav_stereo(str(wav))
            return l, r

        def energies(sig: np.ndarray) -> tuple[float, float]:
            return goertzel(sig, SR, 440), goertzel(sig, SR, 880)

        # 模式1
        l, r = run_mode(1)
        mid, side = (l + r) / 2, (l - r) / 2
        e440, e880 = energies(mid)
        check("模式1 Mid=B(880)主导", e880 > 10 * e440,
              f"440={e440:.2e} 880={e880:.2e}")
        e440, e880 = energies(side)
        check("模式1 Side=A(440)主导", e440 > 10 * e880,
              f"440={e440:.2e} 880={e880:.2e}")

        # 模式2
        l, r = run_mode(2)
        mid, side = (l + r) / 2, (l - r) / 2
        e440, e880 = energies(mid)
        check("模式2 Mid=A(440)主导", e440 > 10 * e880,
              f"440={e440:.2e} 880={e880:.2e}")
        e440, e880 = energies(side)
        check("模式2 Side=B(880)主导", e880 > 10 * e440,
              f"440={e440:.2e} 880={e880:.2e}")

        # 模式3:同模式1 + 超声
        l, r = run_mode(3)
        mid = (l + r) / 2
        e440, e880 = energies(mid)
        check("模式3 Mid=B(880)主导", e880 > 10 * e440,
              f"440={e440:.2e} 880={e880:.2e}")
        e_us = goertzel(mid, SR, 19000)
        check("模式3 超声载波(19k)可检出", e_us > 1e-5,
              f"E19k={e_us:.2e}")

        # 模式5
        l, r = run_mode(5)
        mid = (l + r) / 2
        mid_rms = float(np.sqrt(np.mean(mid.astype(np.float64) ** 2)))
        l_rms = float(np.sqrt(np.mean(l.astype(np.float64) ** 2)))
        corr = float(np.corrcoef(l, r)[0, 1])
        # 注:AAC 封装后单声道残余为原生 AAC 量化噪声(实测 ≈-35dB,
        # 与原版同链同量级),阈值取 3%;封装前严格为零见下一项。
        check("模式5 单声道近静音(AAC 残余)", mid_rms < 0.03 * a_mono_rms,
              f"mid_rms={mid_rms:.2e} A_rms={a_mono_rms:.3f}")
        stats5 = process_audio_hide(str(a_wav), str(td / "m5_pure.wav"),
                                    ABParams(mode=5))
        check("模式5 封装前单声道严格为零", stats5["mid_rms"] == 0.0,
              f"mid_rms={stats5['mid_rms']}")
        check("模式5 L 声道保留原声", abs(l_rms - a_mono_rms) < 0.25 * a_mono_rms,
              f"L_rms={l_rms:.3f}")
        check("模式5 corr≈-1", corr < -0.99, f"corr={corr:.4f}")

        # 防重复
        mode5_wav = out_wav(td / "out_mode5.mp4", td, "mode5_again.wav")
        try:
            process_audio_hide(str(mode5_wav), str(td / "again.wav"),
                               ABParams(mode=5))
            check("防重复拦截", False, "未抛错")
        except AlreadyProcessedError:
            check("防重复拦截", True)

        # 魔术帧集成(模式5)
        aux_video = td / "aux.mp4"
        sh(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
            "color=c=blue:s=320x240:r=30:d=2", "-c:v", "libx264",
            "-pix_fmt", "yuv420p", "-an", str(aux_video)])
        magic_out = td / "magic_hide.mp4"
        magic_frame(str(a_video), str(aux_video), str(magic_out),
                    audio_mode=5)
        mwav = out_wav(magic_out, td, "magic_hide.wav")
        ml, mr, _ = read_wav_stereo(str(mwav))
        m_mid_rms = float(np.sqrt(np.mean(
            (((ml + mr) / 2).astype(np.float64)) ** 2)))
        check("魔术帧+模式5 单声道近静音(AAC 残余)", m_mid_rms < 0.03 * a_mono_rms,
              f"mid_rms={m_mid_rms:.2e}")
        probe = sh(["ffprobe", "-v", "error", "-show_entries",
                    "stream=avg_frame_rate", "-of", "csv=p=0",
                    str(magic_out)])
        check("魔术帧+模式5 视频仍 90fps", "90/1" in probe.stdout,
              probe.stdout.strip().splitlines()[0] if probe.stdout else "")

    print("\n结果:", "全部通过" if not failures else f"{len(failures)} 项失败: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
