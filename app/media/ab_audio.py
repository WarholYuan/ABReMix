"""AB 音频隐藏(黑五·AB 音频,方案 M4)。

依据《肥猫 3.3 逆向分析报告》§5.4(a)与 2026-10-14 脱壳镜像中
tab_ab_audio 模块的字符串二次挖掘(函数/常量名与日志原文):

  - 函数骨架按原版同名复刻:extract_pcm_wav / read_wav_stereo /
    mix_ab_audio / process_audio_hide / remux 等;
  - 模式定义(原版 MODE_LABELS 原文):
      模式1  机审听话术B,耳机听原片A
      模式2  机审听原片A,耳机听话术B
      模式3  模式1 + 超声波(双层载波)
      模式4  模式2 + 超声波(双层载波)
      模式5  红薯静音(纯反相)【推荐批量】,无需音频 B
  - 声道隐藏公式(mid/side):机器单声道下混听到 Mid,立体声
    听感由 Side 承载另一路——L = Mid + Side,R = Mid - Side,
    单声道 (L+R)/2 = Mid 严格成立;
  - 防重复处理:输入立体声的 L/R 相关系数 corr 与 side/L 比值
    命中已处理特征时跳过(原版日志「已处理过 (corr=…), 跳过」,
    常量 IDEM_CORR_THRESH / SIDE_L_THRESH);
  - 高级项(原版 UI):音量 α(默认 1.0)、全片深度(粉红噪 +
    Haas)、干扰音量(默认 0);处理后输出验证统计
    Mid_RMS / Side_RMS / L/R_corr;
  - 时间掩码(time_mode):full / first_half / last_half / custom,
    掩码外保持原声(原版 _time_mask 同名结构)。

未恢复量(复刻取值,待原版出片校准):BAND_LOW/BAND_HIGH
(取人声带 300-3400Hz,只作用于话术 B)、CARRIER_FREQ
(取 19000Hz + 第二层 +1500Hz,各 amp 0.05)、DEFAULT_HAAS_MS
(取 0.4ms,只延迟 Side 分量以保证 Mid 严格纯净)、各阈值常数。
原版用 numpy,本模块同栈(numpy 为 M4 引入的唯一第三方依赖)。
"""
from __future__ import annotations

import subprocess
import tempfile
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..core.ffmpeg_resolve import ffmpeg_path

SAMPLE_RATE = 44100
BAND_LOW, BAND_HIGH = 300.0, 3400.0
CARRIER_FREQ = 19000.0
IDEM_CORR_THRESH = -0.5  # corr 低于此值视为已处理(反相特征)
SIDE_L_THRESH = 1.2  # side/L 高于此值且 corr 低时视为已处理


class AlreadyProcessedError(RuntimeError):
    """输入已被 AB 隐藏处理过(corr 防重复命中)。"""


@dataclass
class ABParams:
    mode: int = 1
    alpha: float = 1.0  # 原片 A 音量(原版默认 1.0)
    haas_ms: float = 0.4  # Side 分量 Haas 延迟
    carrier_freq: float = CARRIER_FREQ
    carrier_amp: float = 0.05
    deep: bool = False  # 全片深度(粉红噪 + Haas)
    interfere_vol: float = 0.0  # 干扰(深度噪声)音量,默认 0
    target_side_gain: float = 1.0  # Side 目标增益(相对 Mid RMS)
    time_mode: str = "full"  # full/first_half/last_half/custom
    custom_ranges: list[tuple[float, float]] = field(default_factory=list)
    sample_rate: int = SAMPLE_RATE
    audio_bitrate: str = "192k"  # 原版 remux 默认 192k
    seed: int = 0


def extract_pcm_wav(media_path: str, out_wav: str,
                    sample_rate: int = SAMPLE_RATE) -> str:
    """从媒体提取立体声 PCM WAV(原版:ffmpeg -vn -acodec pcm_s16le -ar -ac 2)。"""
    cmd = [ffmpeg_path(), "-y", "-i", media_path, "-vn",
           "-acodec", "pcm_s16le", "-ar", str(sample_rate), "-ac", "2",
           out_wav]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"提取音频失败({media_path}): {proc.stderr.strip()[-300:]}")
    return out_wav


def read_wav_stereo(path: str) -> tuple[np.ndarray, np.ndarray, int]:
    """读立体声 WAV,返回 (left, right float32[-1,1], sample_rate)。"""
    with wave.open(path, "rb") as w:
        if w.getnchannels() != 2:
            raise ValueError("音频必须是立体声")
        sr = w.getframerate()
        raw = w.readframes(w.getnframes())
    data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    data = data.reshape(-1, 2)
    return data[:, 0].copy(), data[:, 1].copy(), sr


def write_wav_stereo(path: str, left: np.ndarray, right: np.ndarray,
                     sr: int) -> None:
    data = np.stack([left, right], axis=1)
    pcm = (np.clip(data, -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def _to_mono(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    return (left + right) * 0.5


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x)))) if x.size else 0.0


def _corr(left: np.ndarray, right: np.ndarray) -> float:
    if left.size == 0 or np.std(left) == 0 or np.std(right) == 0:
        return 0.0
    return float(np.corrcoef(left, right)[0, 1])


def _pad_or_trim(x: np.ndarray, n: int) -> np.ndarray:
    if x.shape[0] >= n:
        return x[:n]
    return np.pad(x, (0, n - x.shape[0]))


def _peak_normalize(left: np.ndarray, right: np.ndarray,
                    peak: float = 0.99) -> tuple[np.ndarray, np.ndarray]:
    m = max(float(np.max(np.abs(left))) if left.size else 0.0,
            float(np.max(np.abs(right))) if right.size else 0.0)
    if m > peak:
        gain = peak / m
        return left * gain, right * gain
    return left, right


def _bandpass_fft(x: np.ndarray, sr: int,
                  low: float = BAND_LOW,
                  high: float = BAND_HIGH) -> np.ndarray:
    if x.size == 0:
        return x
    spec = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(x.shape[0], 1.0 / sr)
    spec[(freqs < low) | (freqs > high)] = 0
    return np.fft.irfft(spec, x.shape[0]).astype(np.float32)


def _pink_noise(rng: np.random.RandomState, n: int, sr: int) -> np.ndarray:
    """FFT 谱整形粉红噪(原版:rfft 后按 1/sqrt(f) 塑形再 irfft)。"""
    white = rng.randn(n).astype(np.float32)
    spec = np.fft.rfft(white)
    freqs = np.fft.rfftfreq(n, 1.0 / sr)
    freqs[0] = freqs[1] if len(freqs) > 1 else 1.0
    spec = spec / np.sqrt(freqs)
    pink = np.fft.irfft(spec, n).astype(np.float32)
    rms = _rms(pink)
    return pink / rms if rms > 0 else pink


def _delay(x: np.ndarray, samples: int) -> np.ndarray:
    if samples <= 0:
        return x
    return np.pad(x, (samples, 0))[:x.shape[0]]


def _add_ultrasonic(left: np.ndarray, right: np.ndarray, sr: int,
                    freq: float, amp: float) -> tuple[np.ndarray, np.ndarray]:
    """双层超声载波:freq 与 freq+1500 两条正弦,等量加入左右声道。"""
    n = left.shape[0]
    t = np.arange(n, dtype=np.float32) / sr
    carrier = (np.sin(2 * np.pi * freq * t)
               + np.sin(2 * np.pi * (freq + 1500.0) * t)).astype(np.float32)
    carrier *= (amp / np.sqrt(2.0))
    return left + carrier, right + carrier


def _time_mask(n: int, sr: int, params: ABParams) -> np.ndarray:
    """时间掩码:1.0=处理区,0.0=原声区(原版 _time_mask)。"""
    mask = np.zeros(n, dtype=np.float32)
    if params.time_mode == "full":
        mask[:] = 1.0
    elif params.time_mode == "first_half":
        mask[:n // 2] = 1.0
    elif params.time_mode == "last_half":
        mask[n // 2:] = 1.0
    elif params.time_mode == "custom":
        for start, end in params.custom_ranges:
            s = max(0, int(start * sr))
            e = min(n, int(end * sr))
            mask[s:e] = 1.0
    else:
        raise ValueError(f"未知 time_mode: {params.time_mode}")
    return mask


def mix_ab_audio(a_mono: np.ndarray, b_mono: np.ndarray, mode: int,
                 sr: int, params: ABParams,
                 rng: np.random.RandomState) -> tuple[np.ndarray, np.ndarray]:
    """双音源声道隐藏(模式 1-4):返回 (left, right)。

    Mid/Side 分配:模式1/3 Mid=B、Side=A;模式2/4 Mid=A、Side=B。
    话术 B 先经人声带通;A 以 alpha 计入。Side 按 target_side_gain
    对齐到 Mid 的 RMS 倍数后做 Haas 延迟(只动 Side,Mid 严格
    纯净);deep 时 Side 再叠干扰噪声(interfere_vol)。
    """
    n = a_mono.shape[0]
    b_mono = _pad_or_trim(b_mono, n)
    b_band = _bandpass_fft(b_mono, sr)
    a_scaled = a_mono * params.alpha
    if mode in (1, 3):
        mid, side = b_band, a_scaled
    else:
        mid, side = a_scaled, b_band
    side_rms, mid_rms = _rms(side), _rms(mid)
    if side_rms > 0 and mid_rms > 0:
        gain = params.target_side_gain * mid_rms / side_rms
        side = side * float(np.clip(gain, 0.25, 4.0))
    side = _delay(side, int(params.haas_ms * sr / 1000.0))
    if params.deep and params.interfere_vol > 0:
        noise = _pink_noise(rng, n, sr) * params.interfere_vol
        side = side + noise
    left = (mid + side).astype(np.float32)
    right = (mid - side).astype(np.float32)
    if mode in (3, 4):
        left, right = _add_ultrasonic(left, right, sr,
                                      params.carrier_freq,
                                      params.carrier_amp)
    return left, right


def _ab_mono_for_single_video(a_mono: np.ndarray,
                              mode: int) -> tuple[np.ndarray, np.ndarray]:
    """单音源成轨:模式5 纯反相——L = A,R = -A(单声道下混严格为 0)。"""
    if mode != 5:
        raise ValueError("单音源路径仅支持模式5")
    return a_mono.copy(), -a_mono.copy()


def process_audio_hide(in_wav: str, out_wav: str, params: ABParams,
                       b_wav: str | None = None) -> dict:
    """对立体声 WAV 做 AB 隐藏,返回验证统计 dict。

    in_wav 为原片 A 的立体声 WAV;模式 1-4 需 b_wav(话术 B)。
    输入命中已处理特征(corr/侧向比)时抛 AlreadyProcessedError。
    """
    if params.mode not in (1, 2, 3, 4, 5):
        raise ValueError(f"未知模式: {params.mode}")
    left, right, sr = read_wav_stereo(in_wav)
    n = left.shape[0]

    corr0 = _corr(left, right)
    side0 = _rms((left - right) * 0.5)
    l0 = _rms(left)
    if corr0 < IDEM_CORR_THRESH or (l0 > 0 and side0 / l0 > SIDE_L_THRESH
                                    and corr0 < 0.2):
        raise AlreadyProcessedError(
            f"已处理过 (corr={corr0:.3f}), 跳过")

    a_mono = _to_mono(left, right)
    rng = np.random.RandomState(params.seed)
    if params.mode == 5:
        new_left, new_right = _ab_mono_for_single_video(a_mono, 5)
    else:
        if b_wav is None:
            raise ValueError(f"模式{params.mode} 需要音频 B")
        b_left, b_right, _ = read_wav_stereo(b_wav)
        b_mono = _to_mono(b_left, b_right)
        new_left, new_right = mix_ab_audio(a_mono, b_mono, params.mode,
                                           sr, params, rng)

    mask = _time_mask(n, sr, params)
    out_left = left * (1 - mask) + new_left * mask
    out_right = right * (1 - mask) + new_right * mask
    out_left, out_right = _peak_normalize(out_left.astype(np.float32),
                                          out_right.astype(np.float32))
    write_wav_stereo(out_wav, out_left, out_right, sr)

    mid = _to_mono(out_left, out_right)
    side = (out_left - out_right) * 0.5
    return {
        "mid_rms": _rms(mid), "side_rms": _rms(side),
        "corr": _corr(out_left, out_right),
        "samples": n, "sample_rate": sr,
    }


def remux_video_with_audio(video_path: str, audio_wav: str, out_path: str,
                           audio_bitrate: str = "192k") -> str:
    """把处理后的 WAV 与原视频合成(原版同名 remux)。

    -aac_ms 0 / -aac_is 0:关闭 AAC 自身的声道联合编码,防止其
    中/侧与强度立体声重排破坏隐藏结构(原版 remux 常量中
    -aac_ms 后即跟值 0)。
    """
    cmd = [ffmpeg_path(), "-y", "-i", video_path, "-i", audio_wav,
           "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
           "-c:a", "aac", "-b:a", audio_bitrate,
           "-aac_ms", "0", "-aac_is", "0",
           "-movflags", "+faststart", out_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"合成视频失败: {proc.stderr.strip()[-300:]}")
    return out_path


def process_video_ab_hide(video_path: str, out_path: str, params: ABParams,
                          audio_b: str | None = None,
                          tmp_dir: str | None = None) -> dict:
    """整片处理:视频 A(+音频 B 媒体)到带隐藏音频的输出视频。"""
    work = Path(tmp_dir) if tmp_dir else Path(
        tempfile.mkdtemp(prefix="ab_audio_"))
    work.mkdir(parents=True, exist_ok=True)
    a_wav = str(work / "_ab_hide_in.wav")
    extract_pcm_wav(video_path, a_wav, params.sample_rate)
    b_wav = None
    if params.mode != 5:
        if audio_b is None:
            raise ValueError(f"模式{params.mode} 需要音频 B")
        b_wav = str(work / "_ab_b.wav")
        extract_pcm_wav(audio_b, b_wav, params.sample_rate)
    out_wav = str(work / "_ab_hide_out.wav")
    stats = process_audio_hide(a_wav, out_wav, params, b_wav=b_wav)
    remux_video_with_audio(video_path, out_wav, out_path,
                           params.audio_bitrate)
    return stats
