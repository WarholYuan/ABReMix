"""指纹后处理三模式(方案 §7.2,M3 尾巴)。

依据《肥猫 3.3 逆向分析报告》§5.4 与 3.3 脱壳镜像 xingmai_m12_patch
模块的注释/常量字符串恢复:

- 模式一 cvj:视频轨 mdhd timescale 锁定 12000(stts delta 同步换算)、
  dirty stts(同卡时长精确版)、elst 保持真实(口径分裂,原版注释
  「elst 保持真实秒数(手机正常播),mvhd/mdhd/stts 说假话」)、
  esds 码率 153028、compressor「JVT-AVC Coding」、剥离 udta/ilst 标签。
  (原版另有 hdlr 改名/track_id/mvhd 矩阵/音频 mdat 标记等一组常量,
  属厂商指纹细节,复刻默认不做,避免发明未知值;esds/compressor 两项
  有明确常量故实现。)
- 模式二 weird4:**保持原 stts delta 不变,只放大 count** 使
  Σ(count×delta) 对齐假时长(原版注释原文;count 放大 = “幽灵样本”,
  elst 锁真实播放);音频轨 dirty stts + 时长伪造;esds 码率 200112;
  udta 相机指纹块(模板文件 assets/weird4_udta.hex 优先,无文件用
  内嵌默认模板;字段含厂商/型号/GPS/日期,复刻默认值自备,不照抄
  原版的杭州钱塘江坐标与机型组合)。
- 模式三 weird8:avcC 内 SPS 锁定伪造 SAR(原版值 2671:51675,
  竖屏拉伸效果)——Exp-Golomb 解析/改写 SPS,avcC 变长重建;
  外加 ©too/compressor 字段与文件尾部 free box(采样表膨胀属
  weird4 同族技巧,经 --inflate 开关复用)。

所有改写走 mp4box 的通用 box 操作:变长替换同步修正祖先 size 与
stco/co64 chunk 偏移,改后均可被 ffprobe 正常打开(实跑验证)。
"""
from __future__ import annotations

import random
import struct
from dataclasses import dataclass
from pathlib import Path

from . import mp4box
from .mp4box import Box

# 脱壳镜像中恢复的常量(有原文依据)
CVJ_VIDEO_TS = 12000            # a_patch_video_timescale_cvj: video timescale 12000
CVJ_ESDS_BR = 153028            # 「m1: esds 153028」
CVJ_COMPRESSOR = "JVT-AVC Coding"  # 「m1: compressor JVT-AVC Coding」
WEIRD4_ESDS_BR = 200112         # 「m2: esds 200112」
WEIRD8_SAR = (2671, 51675)      # 报告 §7.2:avcC SPS 伪造 SAR(竖屏拉伸)

_ASSETS = Path(__file__).resolve().parents[2] / "assets"

# weird4 udta 内嵌默认模板(复刻自备值,不照抄原版坐标/机型)。
# 结构为自由格式文本块,沿用相机 EXIF 风格字段;可放
# assets/weird4_udta.hex 覆盖(与原版「外部文件优先」一致)。
_WEIRD4_UDTA_EMBEDDED = (
    b"\x00\x00\x00\x01"  # 版本占位
    b"Canon\x00Canon EOS R50\x00"
    b"2026:09:12 15:04:33\x00"
    b"+31.2345+121.4678/\x00"   # 复刻默认坐标(非原版值)
    b"feimao-replica\x00"
)


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _video_trak(data: bytes, moov: Box) -> Box:
    for trak in [b for b in moov.children if b.type == b"trak"]:
        if not mp4box._is_audio_track(data, trak):
            return trak
    raise RuntimeError("未找到视频轨")


def _audio_traks(data: bytes, moov: Box) -> list[Box]:
    return [b for b in moov.children if b.type == b"trak"
            if mp4box._is_audio_track(data, b)]


def _strip_tags(data: bytearray, moov: Box, types: tuple[bytes, ...]) -> int:
    """剥离 moov 下(含 meta 内)指定类型的 box,返回删除数量。"""
    removed = 0
    while True:
        box = None
        for t in types:
            box = moov.find(t)
            if box is not None:
                break
        if box is None:
            return removed
        mp4box.remove_box(data, box)
        removed += 1
        moov = mp4box.find_moov(bytes(data))


def _patch_esds_bitrate(data: bytearray, moov: Box, br: int) -> int:
    """把音频轨 esds 的 maxBitrate/avgBitrate 改为 br(等长改写)。

    esds 在 mp4a sample entry 内部(stsd 不是容器 box,解析器不下钻),
    故直接在音频轨 stsd 字节范围内定位 esds fourcc。结构:
    version/flags(4) → 0x03 ES_Descriptor → 0x04 DecoderConfigDescriptor
    (tag+len+objectType(1)+streamType(1)+bufferSizeDB(3)+
    maxBitrate(4)+avgBitrate(4))。
    """
    count = 0
    for trak in [b for b in moov.children if b.type == b"trak"]:
        if not mp4box._is_audio_track(bytes(data), trak):
            continue
        stsd = trak.find(b"stsd")
        if stsd is None:
            continue
        span_start = stsd.content_start
        span_end = stsd.start + stsd.size

        def read_len(body: bytes, i: int) -> tuple[int, int]:
            # MPEG-4 expando 长度:高位置 1 续读,最多 4 字节
            v = 0
            n = 0
            while i + n < len(body) and n < 4:
                b = body[i + n]
                v = (v << 7) | (b & 0x7F)
                n += 1
                if not (b & 0x80):
                    break
            return v, n

        pos = span_start
        while True:
            at = bytes(data).find(b"esds", pos, span_end)
            if at < 0:
                break
            c = at + 4  # esds 内容起点
            body = bytes(data[c:span_end])
            pos = at + 4
            pos3 = body.find(b"\x03", 4)
            if pos3 < 0:
                continue
            _, n3 = read_len(body, pos3 + 1)
            pos4 = body.find(b"\x04", pos3 + 1 + n3)
            if pos4 < 0:
                continue
            _, n4 = read_len(body, pos4 + 1)
            base = pos4 + 1 + n4 + 1 + 1 + 3
            if base + 8 <= len(body):
                struct.pack_into(">I", data, c + base, br)
                struct.pack_into(">I", data, c + base + 4, br)
                count += 1
                break
    return count


def _find_avc1(data: bytes, trak: Box) -> Box | None:
    """在视频轨 stsd 内定位 avc1 sample entry(伪 box,手工定位)。"""
    stsd = trak.find(b"stsd")
    if stsd is None:
        return None
    c = stsd.content_start
    # stsd: version/flags(4) + entry_count(4) + entries
    pos = c + 8
    end = stsd.start + stsd.size
    while pos + 8 <= end:
        size = struct.unpack(">I", data[pos:pos + 4])[0]
        btype = bytes(data[pos + 4:pos + 8])
        if btype in (b"avc1", b"avc3", b"hvc1", b"hev1", b"mp4v"):
            return Box(type=btype, start=pos, size=size, header=8,
                       parent=stsd)
        if size < 8:
            break
        pos += size
    return None


def _patch_compressor(data: bytearray, moov: Box, name: str) -> bool:
    """改写视频 sample entry 的 compressorname(32 字节定长字段)。"""
    data_b = bytes(data)
    trak = _video_trak(data_b, moov)
    avc1 = _find_avc1(data_b, trak)
    if avc1 is None:
        return False
    # VisualSampleEntry: header(8)+reserved(6)+dref(2)+pre_defined/
    # reserved(16)+width/height(4)+horiz/vertresolution(8)+reserved(4)+
    # frame_count(2)+compressorname(32)
    off = avc1.content_start + 6 + 2 + 16 + 4 + 8 + 4 + 2
    raw = name.encode("ascii")[:31]
    field = bytes([len(raw)]) + raw + b"\x00" * (31 - len(raw))
    data[off:off + 32] = field
    return True


# ---------------------------------------------------------------------------
# dirty stts 复用 / count 放大(weird4 视频轨)
# ---------------------------------------------------------------------------

def _rewrite_track_stts(data: bytearray, trak_index: int,
                        entries: list[tuple[int, int]]) -> None:
    """用给定条目整体替换某轨 stts(变长,走通用替换)。"""
    moov = mp4box.find_moov(bytes(data))
    trak = [b for b in moov.children if b.type == b"trak"][trak_index]
    stts = trak.find(b"stts")
    new_box = struct.pack(">I", 8 + 8 + 8 * len(entries)) + b"stts"
    new_box += struct.pack(">II", 0, len(entries))
    for cnt, delta in entries:
        new_box += struct.pack(">II", cnt, delta)
    mp4box.replace_box(data, stts, new_box)


def _fake_track_durations(data: bytearray, target_seconds: float) -> None:
    """mvhd/tkhd/mdhd 三套时长对齐目标(等长改写,同 patch_fake_duration)。"""
    moov = mp4box.find_moov(bytes(data))
    mvhd = moov.find(b"mvhd")
    movie_ts, _, dur_off, dur_size = mp4box._read_timescale_duration(
        bytes(data), mvhd)
    movie_target = int(round(target_seconds * movie_ts))
    mp4box._write_u32_or_u64(data, dur_off, dur_size, movie_target)
    for trak in [b for b in moov.children if b.type == b"trak"]:
        tkhd = trak.find(b"tkhd")
        _, tk_off, tk_size = mp4box._read_tkhd_duration(bytes(data), tkhd)
        mp4box._write_u32_or_u64(data, tk_off, tk_size, movie_target)
        mdhd = trak.find(b"mdhd")
        ts, _, md_off, md_size = mp4box._read_timescale_duration(
            bytes(data), mdhd)
        mp4box._write_u32_or_u64(data, md_off, md_size,
                                 int(round(target_seconds * ts)))


def _set_video_timescale_12000(data: bytearray) -> None:
    """cvj:视频轨 mdhd timescale 锁 12000,stts delta 同步换算。

    「Set video mdhd timescale to 12000 and scale stts/duration
    accordingly」(脱壳镜像注释原文)。
    """
    moov = mp4box.find_moov(bytes(data))
    trak = _video_trak(bytes(data), moov)
    mdhd = trak.find(b"mdhd")
    old_ts, _, md_off, md_size = mp4box._read_timescale_duration(
        bytes(data), mdhd)
    if old_ts == CVJ_VIDEO_TS:
        return
    stts = trak.find(b"stts")
    entries = mp4box.read_stts(bytes(data), stts)
    scaled = [(c, max(1, int(round(d * CVJ_VIDEO_TS / old_ts))))
              for c, d in entries]
    traks = [b for b in moov.children if b.type == b"trak"]
    _rewrite_track_stts(data, traks.index(trak), scaled)
    # 重写后重新定位 mdhd 再换算 duration
    moov = mp4box.find_moov(bytes(data))
    trak = _video_trak(bytes(data), moov)
    mdhd = trak.find(b"mdhd")
    old_ts2, old_dur, md_off, md_size = mp4box._read_timescale_duration(
        bytes(data), mdhd)
    c = mdhd.content_start
    version = data[c]
    ts_off = c + 4 + (16 if version == 1 else 8)
    new_dur = int(round(old_dur * CVJ_VIDEO_TS / old_ts2))
    struct.pack_into(">I", data, ts_off, CVJ_VIDEO_TS)
    mp4box._write_u32_or_u64(data, md_off, md_size, new_dur)


# ---------------------------------------------------------------------------
# 模式一 cvj
# ---------------------------------------------------------------------------

@dataclass
class FingerprintReport:
    mode: str
    details: dict


def patch_cvj(path: str, target_seconds: float, seed: int = 0,
              keep_elst_real: bool = True) -> FingerprintReport:
    """模式一 cvj:12000 时基 + dirty stts + 时长伪造 + esds/compressor
    + 剥离 udta/ilst。elst 默认保持真实(口径分裂,原版注释口径)。"""
    p = Path(path)
    data = bytearray(p.read_bytes())
    rng = random.Random(seed)
    details: dict = {}

    _set_video_timescale_12000(data)
    details["video_timescale"] = CVJ_VIDEO_TS

    # dirty stts(视频+音频)——目标时长按各轨 timescale 换算;
    # 每轨替换后偏移失效,逐轨重新解析
    n_tracks = len([b for b in mp4box.find_moov(bytes(data)).children
                    if b.type == b"trak"])
    for idx in range(n_tracks):
        moov = mp4box.find_moov(bytes(data))
        trak = [b for b in moov.children if b.type == b"trak"][idx]
        mdhd = trak.find(b"mdhd")
        ts, _, _, _ = mp4box._read_timescale_duration(bytes(data), mdhd)
        stts = trak.find(b"stts")
        old = mp4box.read_stts(bytes(data), stts)
        n = sum(c for c, _ in old)
        target_units = int(round(target_seconds * ts))
        _rewrite_track_stts(data, idx,
                            mp4box.dirty_stts_entries(n, target_units, rng))
    details["dirty_stts"] = True

    _fake_track_durations(data, target_seconds)
    details["fake_seconds"] = target_seconds

    moov = mp4box.find_moov(bytes(data))
    details["esds_br"] = _patch_esds_bitrate(data, moov, CVJ_ESDS_BR)
    details["compressor"] = _patch_compressor(data, moov, CVJ_COMPRESSOR)
    moov = mp4box.find_moov(bytes(data))
    details["stripped"] = _strip_tags(data, moov, (b"udta", b"ilst"))

    # elst:cvj 口径保持真实;显式要求全口径时才对齐
    if not keep_elst_real:
        moov = mp4box.find_moov(bytes(data))
        mvhd = moov.find(b"mvhd")
        movie_ts, _, _, _ = mp4box._read_timescale_duration(bytes(data), mvhd)
        movie_target = int(round(target_seconds * movie_ts))
        for elst in moov.find_all(b"elst"):
            c = elst.content_start
            seg_size = 8 if data[c] == 1 else 4
            mp4box._write_u32_or_u64(data, c + 8, seg_size, movie_target)
    details["elst"] = "real" if keep_elst_real else "fake"

    p.write_bytes(bytes(data))
    return FingerprintReport(mode="cvj", details=details)


# ---------------------------------------------------------------------------
# 模式二 weird4
# ---------------------------------------------------------------------------

def _inflate_stts_count(entries: list[tuple[int, int]],
                        target_units: int) -> list[tuple[int, int]]:
    """weird4:保持原 delta 不变,只放大 count 使 Σ(count×delta)=target。

    从末段起向前放大(原版注释:「放大末段 count…(幽灵样本)」)。
    """
    total = sum(c * d for c, d in entries)
    if total >= target_units or not entries:
        return list(entries)
    need = target_units - total
    out = list(entries)
    cnt, delta = out[-1]
    if delta <= 0:
        return out
    out[-1] = (cnt + (need + delta - 1) // delta, delta)
    # 精确对齐:把多出的零头摊回一个新尾条
    over = sum(c * d for c, d in out) - target_units
    if over > 0:
        head_sum = sum(c * d for c, d in out[:-1])
        exact = (target_units - head_sum) // delta
        out[-1] = (max(1, exact), delta)
    return out


def load_weird4_udta() -> bytes:
    """weird4 udta 模板:assets/weird4_udta.hex 优先,无文件用内嵌常量
    (与原版「外部文件优先、无文件用内嵌」行为一致)。"""
    hex_file = _ASSETS / "weird4_udta.hex"
    if hex_file.is_file():
        try:
            return bytes.fromhex(hex_file.read_text(encoding="ascii").strip())
        except ValueError as exc:
            raise RuntimeError(f"weird4_udta invalid: {exc}")
    return _WEIRD4_UDTA_EMBEDDED


def patch_weird4(path: str, target_seconds: float,
                 seed: int = 0) -> FingerprintReport:
    """模式二 weird4:视频轨 count 放大(保 delta)+ 音频 dirty stts +
    esds 200112 + udta 相机指纹块。elst 锁真实播放。"""
    p = Path(path)
    data = bytearray(p.read_bytes())
    rng = random.Random(seed)
    details: dict = {}

    n_tracks = len([b for b in mp4box.find_moov(bytes(data)).children
                    if b.type == b"trak"])
    for idx in range(n_tracks):
        moov = mp4box.find_moov(bytes(data))
        trak = [b for b in moov.children if b.type == b"trak"][idx]
        mdhd = trak.find(b"mdhd")
        ts, _, _, _ = mp4box._read_timescale_duration(bytes(data), mdhd)
        stts = trak.find(b"stts")
        old = mp4box.read_stts(bytes(data), stts)
        target_units = int(round(target_seconds * ts))
        if mp4box._is_audio_track(bytes(data), trak):
            n = sum(c for c, _ in old)
            _rewrite_track_stts(
                data, idx, mp4box.dirty_stts_entries(n, target_units, rng))
            details["audio"] = "dirty stts"
        else:
            new = _inflate_stts_count(old, target_units)
            _rewrite_track_stts(data, idx, new)
            details["video"] = {"delta_kept": True,
                                "entries_before": len(old),
                                "entries_after": len(new)}

    _fake_track_durations(data, target_seconds)
    details["fake_seconds"] = target_seconds

    moov = mp4box.find_moov(bytes(data))
    details["esds_br"] = _patch_esds_bitrate(data, moov, WEIRD4_ESDS_BR)

    # udta 相机指纹块:存在则替换、否则追加(与原版一致)
    moov = mp4box.find_moov(bytes(data))
    udta_payload = mp4box.make_box(b"udta", load_weird4_udta())
    old_udta = moov.find(b"udta")
    if old_udta is not None:
        mp4box.replace_box(data, old_udta, udta_payload)
        details["udta"] = "replaced"
    else:
        moov = mp4box.find_moov(bytes(data))
        mp4box.insert_box(data, moov, udta_payload)
        details["udta"] = "appended"

    p.write_bytes(bytes(data))
    return FingerprintReport(mode="weird4", details=details)


# ---------------------------------------------------------------------------
# 模式三 weird8(SPS SAR 伪造)
# ---------------------------------------------------------------------------

class _BitReader:
    def __init__(self, data: bytes):
        self.data, self.pos = data, 0

    def u(self, n: int) -> int:
        v = 0
        for _ in range(n):
            byte = self.data[self.pos >> 3]
            v = (v << 1) | ((byte >> (7 - (self.pos & 7))) & 1)
            self.pos += 1
        return v

    def ue(self) -> int:
        zeros = 0
        while self.u(1) == 0:
            zeros += 1
        return (1 << zeros) - 1 + (self.u(zeros) if zeros else 0)

    def se(self) -> int:
        k = self.ue()
        return (k + 1) // 2 if k & 1 else -(k // 2)


class _BitWriter:
    def __init__(self):
        self.bits: list[int] = []

    def u(self, v: int, n: int) -> None:
        for i in range(n - 1, -1, -1):
            self.bits.append((v >> i) & 1)

    def ue(self, v: int) -> None:
        code = v + 1
        n = code.bit_length() - 1
        self.bits.extend([0] * n)
        self.u(code, n + 1)

    def se(self, v: int) -> None:
        self.ue(2 * v - 1 if v > 0 else -2 * v)

    def rbsp(self) -> bytes:
        self.u(1, 1)  # rbsp_stop_one_bit
        while len(self.bits) % 8:
            self.bits.append(0)
        out = bytearray()
        for i in range(0, len(self.bits), 8):
            b = 0
            for bit in self.bits[i:i + 8]:
                b = (b << 1) | bit
            out.append(b)
        return bytes(out)


def _nal_to_rbsp(nal: bytes) -> bytes:
    """去 emulation_prevention_three_byte(0x000003 → 0x0000)。"""
    out = bytearray()
    i = 0
    while i < len(nal):
        if i + 2 < len(nal) and nal[i] == 0 and nal[i + 1] == 0 \
                and nal[i + 2] == 3:
            out += b"\x00\x00"
            i += 3
        else:
            out.append(nal[i])
            i += 1
    return bytes(out)


def _rbsp_to_nal(rbsp: bytes) -> bytes:
    out = bytearray()
    zeros = 0
    for b in rbsp:
        if zeros >= 2 and b <= 3:
            out.append(3)
            zeros = 0
        out.append(b)
        zeros = zeros + 1 if b == 0 else 0
    return bytes(out)


def _parse_sps_header(rbsp: bytes) -> tuple[_BitReader, int, int, int]:
    """读 SPS 头(profile/level/sps_id/chroma),返回 reader 与关键参数。"""
    r = _BitReader(rbsp)
    profile_idc = r.u(8)
    r.u(8)  # constraint flags + reserved
    r.u(8)  # level_idc
    sps_id = r.ue()
    chroma_format_idc = 1
    if profile_idc in (100, 110, 122, 244, 44, 83, 86, 118, 128,
                       138, 139, 134, 135):
        chroma_format_idc = r.ue()
        if chroma_format_idc == 3:
            r.u(1)
        r.ue()  # bit_depth_luma_minus8
        r.ue()  # bit_depth_chroma_minus8
        r.u(1)  # qpprime_y_zero_transform_bypass
        if r.u(1):  # seq_scaling_matrix_present
            n = 8 if chroma_format_idc != 3 else 12
            for i in range(n):
                if r.u(1):
                    size = 16 if i < 6 else 64
                    last, next_s = 8, 8
                    for _ in range(size):
                        if next_s:
                            next_s = (last + r.se() + 256) % 256
                        last = next_s if next_s else last
    r.ue()  # log2_max_frame_num_minus4
    poc = r.ue()
    if poc == 0:
        r.ue()
    elif poc == 1:
        r.u(1)
        r.se()
        r.se()
        for _ in range(r.ue()):
            r.se()
    r.ue()  # max_num_ref_frames
    r.u(1)  # gaps_in_frame_num
    r.ue()  # pic_width_in_mbs_minus1
    r.ue()  # pic_height_in_map_units_minus1
    frame_mbs_only = r.u(1)
    if not frame_mbs_only:
        r.u(1)
    r.u(1)  # direct_8x8_inference
    if r.u(1):  # frame_cropping
        r.ue(); r.ue(); r.ue(); r.ue()
    return r, profile_idc, chroma_format_idc, frame_mbs_only


def rewrite_sps_sar(sps_nal: bytes, sar_w: int, sar_h: int) -> bytes:
    """把 SPS NAL(不含起始码,含 NAL header 字节)的 SAR 改为 sar_w:sar_h。

    重建 VUI 起始处的 aspect_ratio 字段:vui 存在则改写其头部,
    vui 不存在则在裁剪字段后插入最小 VUI(仅含 aspect_ratio)。
    返回新 NAL。不支持的多 SPS/奇异结构抛 RuntimeError。
    """
    if len(sps_nal) < 4 or (sps_nal[0] & 0x1F) != 7:
        raise RuntimeError("不是 SPS NAL")
    rbsp = _nal_to_rbsp(sps_nal[1:])
    r, profile, _chroma, _fmo = _parse_sps_header(rbsp)
    head_bits = r.pos  # vui_parameters_present_flag 之前的位数

    w = _BitWriter()
    # 拷贝头部比特(到 vui flag 之前)
    for i in range(head_bits):
        w.bits.append((rbsp[i >> 3] >> (7 - (i & 7))) & 1)

    vui_present = r.u(1)
    if vui_present:
        # 原 VUI 存在:重建其开头,aspect_ratio 强制 extended 并写新值,
        # 其余 VUI 内容原样拷贝(跳过头部的旧 aspect_ratio 字段)
        w.u(1, 1)
        w.u(1, 1)          # aspect_ratio_info_present_flag
        w.u(255, 8)        # extended_SAR
        w.u(sar_w, 16)
        w.u(sar_h, 16)
        if r.u(1):  # 原有 aspect_ratio_info
            idc = r.u(8)
            if idc == 255:
                r.u(16); r.u(16)
        # 拷贝 VUI 剩余比特到 rbsp 尾(去掉原 stop bit,由 writer 重加)
        tail_start = r.pos
        total_bits = len(rbsp) * 8
        # 找 rbsp_trailing_bits 起点:从尾部数 stop+alignment
        end = total_bits
        while end > 0 and ((rbsp[(end - 1) >> 3] >> (7 - ((end - 1) & 7))) & 1) == 0:
            end -= 1
        end -= 1  # 去掉 stop_one_bit
        for i in range(tail_start, max(tail_start, end)):
            w.bits.append((rbsp[i >> 3] >> (7 - (i & 7))) & 1)
    else:
        # 原无 VUI:插入最小 VUI(仅 aspect_ratio),之后无更多内容
        w.u(1, 1)          # vui_parameters_present_flag
        w.u(1, 1)          # aspect_ratio_info_present_flag
        w.u(255, 8)
        w.u(sar_w, 16)
        w.u(sar_h, 16)

    new_rbsp = w.rbsp()
    return sps_nal[:1] + _rbsp_to_nal(new_rbsp)


def _rebuild_avcc_with_sar(data: bytes, avcc: Box,
                           sar: tuple[int, int]) -> bytes:
    """重建 avcC box:首个 SPS 的 SAR 改写为 sar,其余字段原样保留。"""
    body = bytearray(data[avcc.content_start:avcc.start + avcc.size])
    if len(body) < 11 or body[0] != 1:
        raise RuntimeError("avcC 结构异常")
    num_sps = body[5] & 0x1F
    if num_sps < 1:
        raise RuntimeError("avcC 无 SPS")
    sps_len = struct.unpack(">H", body[6:8])[0]
    sps = bytes(body[8:8 + sps_len])
    new_sps = rewrite_sps_sar(sps, sar[0], sar[1])
    new_body = bytes(body[:6]) + struct.pack(">H", len(new_sps)) + new_sps \
        + bytes(body[8 + sps_len:])
    return mp4box.make_box(b"avcC", new_body)


def patch_weird8(path: str, sar: tuple[int, int] = WEIRD8_SAR,
                 tail_free: int = 4096) -> FingerprintReport:
    """模式三 weird8:avcC SPS 锁伪造 SAR + compressor + 尾 free box。

    SAR 默认 2671:51675(原版值,竖屏拉伸观感);可传其它值。
    文件尾部追加 free box(原版「文件尾部 free」注释)。
    """
    p = Path(path)
    data = bytearray(p.read_bytes())
    details: dict = {"sar": f"{sar[0]}:{sar[1]}"}

    moov = mp4box.find_moov(bytes(data))
    trak = _video_trak(bytes(data), moov)
    avc1 = _find_avc1(bytes(data), trak)
    if avc1 is None:
        raise RuntimeError("未找到 avc1 sample entry")
    # avcC 位于 VisualSampleEntry 定长头之后(86 字节)
    off = avc1.content_start + 78
    size = struct.unpack(">I", data[off:off + 4])[0]
    btype = bytes(data[off + 4:off + 8])
    if btype != b"avcC":
        raise RuntimeError("avc1 内未找到 avcC")
    avcc = Box(type=b"avcC", start=off, size=size, header=8, parent=avc1)
    new_avcc = _rebuild_avcc_with_sar(bytes(data), avcc, sar)
    # avcC 变长:手工替换并沿 avc1→stsd→stbl→…→moov 祖先链同步 size
    delta = len(new_avcc) - size
    data[off:off + size] = new_avcc
    node: Box | None = avc1
    while node is not None:
        mp4box._adjust_box_size(data, node, delta)
        node = node.parent
    mp4box._adjust_chunk_offsets(data, off + size, delta)
    details["avcc_delta"] = delta

    moov = mp4box.find_moov(bytes(data))
    details["compressor"] = _patch_compressor(data, moov, CVJ_COMPRESSOR)

    if tail_free > 0:
        data += struct.pack(">I", 8 + tail_free) + b"free" + b"\x00" * tail_free
        details["tail_free"] = tail_free

    p.write_bytes(bytes(data))
    return FingerprintReport(mode="weird8", details=details)
