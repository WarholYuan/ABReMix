"""MP4 box 级后处理:精确时长伪装(卡时长精确版)。

对应方案 §7(M3 MP4 后处理)的核心子集,参数口径依据《肥猫 3.3 逆向
分析报告》§5.4 指纹后处理注释与 §7.1 SKT4 式写法:

  - mvhd / tkhd / mdhd 的 duration 全部改写为目标(假)时长
    (mdhd 保留原 timescale,duration = 目标秒数 × timescale;tkhd 用
    mvhd 的 timescale 换算)——与原版 SKT4 写法一致;
  - stts(采样时长表)按 cvj「dirty stts」模式重写:Σcount 必须等于
    实际采样数,Σ(count×delta) 必须等于目标时长(timescale 单位);
    delta 以 base = 目标单位 // 采样数 打底,余数用单样本
    (1, base+1) 条目逐个分发,base 条目按 ≤250 的随机块长切分
    (注释原文:「首250,base + 交替(1,base+1)/(rand~250,base)」),
    使采样表形态不规则、不呈均匀缩放痕迹;
  - ctts(B 帧呈现偏移)与视频轨 elst 的 media_time 同步按拉伸比
    (目标 stts 总时长 / 原 stts 总时长)缩放,保证拉伸后的时间轴
    内洽:不缩放时呈现偏移仍是原尺度小量,实测会出现尾帧 pts
    跳变与解码 dts 非单调告警;缩放后全片解码干净无告警。
    例外:音频轨 media_time 不缩放——ffmpeg 把它同时用作解码器
    的 skip_samples(AAC 填充采样数,实物采样计数),缩放后实测
    431 个音频帧被跳掉 179 个;其时间轴偏移保持原值仅差
    0.02 秒级(假时间线口径),可忽略。
  - elst(编辑列表)有两种口径,由 elst_mode 选择:
    * "fake"(默认,全口径):elst 首段 segment_duration 也改目标值。
      实测依据:ffmpeg 系读取器(ffprobe/平台转码大概率同系)以 elst
      段时长为准裁剪时间线——elst 保持真实时 ffprobe 仍读真实时长,
      且只解出落在真实时间线内的头几帧(2026-10-14 本机实测:
      10s 素材卡 1800s,elst 真实时只读出 2/300 帧)。全口径下
      ffprobe 读目标时长、300/300 帧完整可解。代价:播放时间线被
      拉长,每帧停留时长按比例放大(卡时长模式的应有之义)。
    * "real"(cvj 兼容):elst 保持真实时长不动——口径分裂,
      mvhd/mdhd/stts 说假话、elst 说真话。原版 cvj 注释称此法
      「手机正常播」(其目标读取器以 mvhd 判显示时长、以 elst
      组织播放);但 ffmpeg 系读取器会读出真实时长,选用时知悉。

stts 条目数变化会改变 moov 大小:本模块在替换 stts 后同步修正
所有祖先 box(moov/trak/mdia/minf/stbl)的 size,并按拼接点修正
全部 stco/co64  chunk 偏移(指向拼接点之后数据的偏移 += size 差)。

局限(诚实标注):
  - ctts 只做等比例缩放,不重排条目;version 0/1 均已处理。
  - 版本仅支持常见布局:box size 为 32 位或 largesize 64 位;
    mvhd/tkhd/mdhd/elst 的 version 0/1 均已处理。
"""
from __future__ import annotations

import random
import struct
from dataclasses import dataclass, field
from pathlib import Path

_CONTAINER_TYPES = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"edts"}


@dataclass
class Box:
    type: bytes
    start: int          # size 字段起始偏移
    size: int           # 含 header 的总大小
    header: int         # header 大小(8 或 16)
    children: list["Box"] = field(default_factory=list)
    parent: "Box | None" = None

    @property
    def end(self) -> int:
        return self.start + self.size

    @property
    def content_start(self) -> int:
        return self.start + self.header

    def find_all(self, btype: bytes) -> list["Box"]:
        out = []
        for child in self.children:
            if child.type == btype:
                out.append(child)
            out.extend(child.find_all(btype))
        return out

    def find(self, btype: bytes) -> "Box | None":
        found = self.find_all(btype)
        return found[0] if found else None


def parse_boxes(data: bytes, start: int = 0, end: int | None = None,
                parent: Box | None = None) -> list[Box]:
    """解析 [start, end) 内的 box 序列,容器 box 递归解析子 box。"""
    if end is None:
        end = len(data)
    boxes: list[Box] = []
    pos = start
    while pos + 8 <= end:
        size32 = struct.unpack(">I", data[pos:pos + 4])[0]
        btype = bytes(data[pos + 4:pos + 8])
        header = 8
        if size32 == 1:
            if pos + 16 > end:
                break
            size = struct.unpack(">Q", data[pos + 8:pos + 16])[0]
            header = 16
        elif size32 == 0:
            size = end - pos
        else:
            size = size32
        if size < header or pos + size > end:
            break
        box = Box(type=btype, start=pos, size=size, header=header,
                  parent=parent)
        if btype in _CONTAINER_TYPES:
            box.children = parse_boxes(data, pos + header, pos + size,
                                       parent=box)
        boxes.append(box)
        pos += size
    return boxes


def parse_file(data: bytes) -> list[Box]:
    return parse_boxes(data, 0, len(data))


# ---------------------------------------------------------------------------
# 字段读写(version 0/1)
# ---------------------------------------------------------------------------

def _read_timescale_duration(data: bytes, box: Box) -> tuple[int, int, int, int]:
    """读 mvhd/mdhd 的 (timescale, duration, duration偏移, duration字节数)。"""
    c = box.content_start
    version = data[c]
    if version == 1:
        ts_off = c + 4 + 8 + 8
        dur_off = ts_off + 4
        dur_size = 8
    else:
        ts_off = c + 4 + 4 + 4
        dur_off = ts_off + 4
        dur_size = 4
    timescale = struct.unpack(">I", data[ts_off:ts_off + 4])[0]
    if dur_size == 8:
        duration = struct.unpack(">Q", data[dur_off:dur_off + 8])[0]
    else:
        duration = struct.unpack(">I", data[dur_off:dur_off + 4])[0]
    return timescale, duration, dur_off, dur_size


def _write_u32_or_u64(data: bytearray, off: int, size: int, value: int) -> None:
    if size == 8:
        data[off:off + 8] = struct.pack(">Q", value)
    else:
        data[off:off + 4] = struct.pack(">I", value & 0xFFFFFFFF)


def _read_tkhd_duration(data: bytes, box: Box) -> tuple[int, int, int]:
    """读 tkhd 的 (duration, duration偏移, duration字节数)。单位为 mvhd timescale。"""
    c = box.content_start
    version = data[c]
    if version == 1:
        dur_off = c + 4 + 8 + 8 + 4 + 4
        dur_size = 8
    else:
        dur_off = c + 4 + 4 + 4 + 4 + 4
        dur_size = 4
    if dur_size == 8:
        duration = struct.unpack(">Q", data[dur_off:dur_off + 8])[0]
    else:
        duration = struct.unpack(">I", data[dur_off:dur_off + 4])[0]
    return duration, dur_off, dur_size


def read_elst(data: bytes, box: Box) -> list[tuple[int, int, int, int]]:
    """读 elst 条目:(segment_duration, media_time, rate_integer, rate_fraction)。"""
    c = box.content_start
    version = data[c]
    count = struct.unpack(">I", data[c + 4:c + 8])[0]
    entries = []
    off = c + 8
    for _ in range(count):
        if version == 1:
            seg, media_time = struct.unpack(">Qq", data[off:off + 16])
            off += 16
        else:
            seg, media_time = struct.unpack(">Ii", data[off:off + 8])
            off += 8
        rate_int, rate_frac = struct.unpack(">hH", data[off:off + 4])
        off += 4
        entries.append((seg, media_time, rate_int, rate_frac))
    return entries


def read_stts(data: bytes, box: Box) -> list[tuple[int, int]]:
    c = box.content_start
    count = struct.unpack(">I", data[c + 4:c + 8])[0]
    entries = []
    off = c + 8
    for _ in range(count):
        cnt, delta = struct.unpack(">II", data[off:off + 8])
        entries.append((cnt, delta))
        off += 8
    return entries


def read_ctts(data: bytes, box: Box) -> tuple[int, list[tuple[int, int]]]:
    """读 ctts:(version, [(count, offset)])。offset 单位为 mdhd timescale。"""
    c = box.content_start
    version = data[c]
    count = struct.unpack(">I", data[c + 4:c + 8])[0]
    entries = []
    off = c + 8
    for _ in range(count):
        cnt, off_v = struct.unpack(">II", data[off:off + 8])
        if version == 1 and off_v >= 0x80000000:
            off_v -= 0x100000000
        entries.append((cnt, off_v))
        off += 8
    return version, entries


def _scale_ctts_in_place(data: bytearray, box: Box, ratio: float) -> None:
    """把 ctts 的 composition offset 按时长拉伸比同比例缩放(等长改写)。

    不缩放时 B 帧呈现偏移仍是原尺度的小量,拉伸后的解码时间轴上
    呈现顺序错乱(实测解码出现 dts 非单调告警、尾帧 pts 跳变)。
    """
    version, entries = read_ctts(bytes(data), box)
    c = box.content_start
    off = c + 8
    for cnt, off_v in entries:
        new_v = int(round(off_v * ratio))
        if version == 1:
            struct.pack_into(">i", data, off + 4, new_v)
        else:
            struct.pack_into(">I", data, off + 4, new_v & 0xFFFFFFFF)
        off += 8


def _is_audio_track(data: bytes, trak: Box) -> bool:
    """按 mdia/hdlr 的 handler_type 判断是否为音频轨('soun')。"""
    hdlr = trak.find(b"hdlr")
    if hdlr is None:
        return False
    c = hdlr.content_start
    # hdlr 内容:version/flags(4) + pre_defined(4) + handler_type(4)
    return bytes(data[c + 8:c + 12]) == b"soun"


def _scale_elst_media_time_in_place(data: bytearray, box: Box,
                                    ratio: float) -> None:
    """把 elst 各条目的 media_time 按拉伸比缩放(空编辑 media_time=-1 不动)。"""
    c = box.content_start
    version = data[c]
    count = struct.unpack(">I", data[c + 4:c + 8])[0]
    off = c + 8
    for _ in range(count):
        if version == 1:
            media_time = struct.unpack(">q", data[off + 8:off + 16])[0]
            if media_time >= 0:
                struct.pack_into(">q", data, off + 8,
                                 int(round(media_time * ratio)))
            off += 16 + 4
        else:
            media_time = struct.unpack(">i", data[off + 4:off + 8])[0]
            if media_time >= 0:
                struct.pack_into(">i", data, off + 4,
                                 int(round(media_time * ratio)))
            off += 8 + 4


# ---------------------------------------------------------------------------
# dirty stts 生成(cvj 模式)
# ---------------------------------------------------------------------------

def dirty_stts_entries(total_samples: int, target_units: int,
                       rng: random.Random) -> list[tuple[int, int]]:
    """生成 Σcount=total_samples、Σ(count×delta)=target_units 的不规则 stts。

    base = target // N 打底;余数 extra(<N)以 (1, base+1) 单样本条目
    分发;base 条目切成 ≤250 的随机块。首块 ≤250(注释原文「首250」)。
    """
    if total_samples <= 0:
        raise ValueError("采样数必须为正")
    base, extra = divmod(target_units, total_samples)
    if base <= 0:
        raise ValueError("目标时长小于一个采样单位,无法伪造 stts")
    entries: list[tuple[int, int]] = []
    remaining = total_samples
    # 首块:≤250,且必须给待分发的 extra 单样本条目留足样本数
    first = min(250, remaining - extra)
    if first > 0:
        entries.append((first, base))
        remaining -= first
    while remaining > 0:
        if extra > 0:
            entries.append((1, base + 1))
            remaining -= 1
            extra -= 1
            if remaining <= 0:
                break
        room = remaining - extra  # 本块 base 条目最多可占的样本数
        if room <= 0:
            continue
        chunk = min(rng.randint(50, 250), room)
        entries.append((chunk, base))
        remaining -= chunk
    assert sum(c for c, _ in entries) == total_samples
    assert sum(c * d for c, d in entries) == target_units
    return entries


# ---------------------------------------------------------------------------
# 主入口:精确时长伪装
# ---------------------------------------------------------------------------

@dataclass
class TrackReport:
    timescale: int
    samples: int
    real_units: int
    target_units: int
    stts_entries_before: int
    stts_entries_after: int
    elst_segment_durations: list[int]  # 应保持真实值(口径分裂验证用)


@dataclass
class PatchReport:
    target_seconds: float
    movie_timescale: int
    tracks: list[TrackReport]


def _ancestors(box: Box) -> list[Box]:
    out = []
    node = box.parent
    while node is not None:
        out.append(node)
        node = node.parent
    return out


def _adjust_box_size(data: bytearray, box: Box, delta: int) -> None:
    if box.header == 16:  # largesize
        old = struct.unpack(">Q", data[box.start + 8:box.start + 16])[0]
        data[box.start + 8:box.start + 16] = struct.pack(">Q", old + delta)
    else:
        old = struct.unpack(">I", data[box.start:box.start + 4])[0]
        if old == 0:
            return  # size=0 延伸到文件尾,无需改
        data[box.start:box.start + 4] = struct.pack(">I", old + delta)


def _adjust_chunk_offsets(data: bytearray, splice_end_old: int,
                          delta: int) -> None:
    """把指向旧文件拼接点之后数据的 stco/co64 偏移加上 delta。"""
    if delta == 0:
        return
    top = parse_file(bytes(data))
    moov = next((b for b in top if b.type == b"moov"), None)
    if moov is None:
        return
    for tag, fmt, width in ((b"stco", ">I", 4), (b"co64", ">Q", 8)):
        for box in moov.find_all(tag):
            c = box.content_start
            count = struct.unpack(">I", data[c + 4:c + 8])[0]
            off = c + 8
            for _ in range(count):
                if width == 4:
                    value = struct.unpack(">I", data[off:off + 4])[0]
                else:
                    value = struct.unpack(">Q", data[off:off + 8])[0]
                if value >= splice_end_old:
                    _write_u32_or_u64(data, off, width, value + delta)
                off += width


def _replace_one_stts(data: bytearray, track_index: int,
                      target_seconds: float,
                      rng: random.Random) -> TrackReport:
    """重写第 track_index 条轨道的 stts,并修正祖先 size 与 chunk 偏移。"""
    top = parse_file(bytes(data))
    moov = next(b for b in top if b.type == b"moov")
    traks = [b for b in moov.children if b.type == b"trak"]
    trak = traks[track_index]
    mdia = trak.find(b"mdia")
    mdhd = mdia.find(b"mdhd")
    stts = trak.find(b"stts")
    timescale, _, _, _ = _read_timescale_duration(bytes(data), mdhd)
    old_entries = read_stts(bytes(data), stts)
    real_units = sum(c * d for c, d in old_entries)
    total_samples = sum(c for c, _ in old_entries)
    target_units = int(round(target_seconds * timescale))
    new_entries = dirty_stts_entries(total_samples, target_units, rng)

    elst = trak.find(b"elst")
    elst_segs = ([e[0] for e in read_elst(bytes(data), elst)]
                 if elst is not None else [])

    new_box = bytearray()
    new_box += struct.pack(">I", 8 + 8 + 8 * len(new_entries))
    new_box += b"stts"
    new_box += struct.pack(">I", 0)  # version/flags
    new_box += struct.pack(">I", len(new_entries))
    for cnt, delta_v in new_entries:
        new_box += struct.pack(">II", cnt, delta_v)

    old_start, old_size = stts.start, stts.size
    delta_size = len(new_box) - old_size
    ancestors = _ancestors(stts)
    # 拼接:祖先 box 起点都在 stts 之前,size 字段位置不受拼接影响
    data[old_start:old_start + old_size] = new_box
    for anc in ancestors:
        _adjust_box_size(data, anc, delta_size)
    _adjust_chunk_offsets(data, old_start + old_size, delta_size)

    return TrackReport(
        timescale=timescale, samples=total_samples,
        real_units=real_units, target_units=target_units,
        stts_entries_before=len(old_entries),
        stts_entries_after=len(new_entries),
        elst_segment_durations=elst_segs,
    )


def patch_fake_duration(path: str, target_seconds: float,
                        seed: int = 0,
                        elst_mode: str = "fake") -> PatchReport:
    """把 MP4 的显示时长精确伪造为 target_seconds(原地改写)。

    mvhd/tkhd/mdhd/stts 全部说假话;elst 由 elst_mode 决定
    ("fake"=全口径一并说假话,"real"=cvj 兼容保持真实)。
    改写后用解析器自检:每条轨道 stts 的 Σcount 不变、
    Σ(count×delta) 等于目标,elst 与所选口径一致。
    """
    if elst_mode not in ("fake", "real"):
        raise ValueError(f"未知 elst_mode: {elst_mode}")
    data = bytearray(Path(path).read_bytes())
    top = parse_file(bytes(data))
    moov = next((b for b in top if b.type == b"moov"), None)
    if moov is None:
        raise RuntimeError("未找到 moov box,无法写入伪时长")
    mvhd = moov.find(b"mvhd")
    movie_ts, _, mvhd_dur_off, mvhd_dur_size = _read_timescale_duration(
        bytes(data), mvhd)
    movie_target = int(round(target_seconds * movie_ts))
    traks = [b for b in moov.children if b.type == b"trak"]
    if not traks:
        raise RuntimeError("未找到 trak,无法写入伪时长")

    rng = random.Random(seed)
    reports: list[TrackReport] = []
    for idx in range(len(traks)):
        reports.append(_replace_one_stts(data, idx, target_seconds, rng))

    # stts 替换可能移动了 moov 内部布局,重新解析后再改时长字段(等长改写)
    top = parse_file(bytes(data))
    moov = next(b for b in top if b.type == b"moov")
    mvhd = moov.find(b"mvhd")
    _, _, dur_off, dur_size = _read_timescale_duration(bytes(data), mvhd)
    _write_u32_or_u64(data, dur_off, dur_size, movie_target)
    trak_list = [b for b in moov.children if b.type == b"trak"]
    for idx, trak in enumerate(trak_list):
        tkhd = trak.find(b"tkhd")
        _, tk_off, tk_size = _read_tkhd_duration(bytes(data), tkhd)
        _write_u32_or_u64(data, tk_off, tk_size, movie_target)
        mdhd = trak.find(b"mdhd")
        _, _, md_off, md_size = _read_timescale_duration(bytes(data), mdhd)
        _write_u32_or_u64(data, md_off, md_size,
                          int(round(target_seconds * reports[idx].timescale)))
        ratio = (reports[idx].target_units / reports[idx].real_units
                 if reports[idx].real_units else 1.0)
        ctts = trak.find(b"ctts")
        if ctts is not None:
            _scale_ctts_in_place(data, ctts, ratio)
        elst = trak.find(b"elst")
        if elst is not None:
            if not _is_audio_track(bytes(data), trak):
                _scale_elst_media_time_in_place(data, elst, ratio)
            if elst_mode == "fake":
                entries = read_elst(bytes(data), elst)
                if entries:
                    c = elst.content_start
                    seg_size = 8 if data[c] == 1 else 4
                    _write_u32_or_u64(data, c + 8, seg_size, movie_target)

    Path(path).write_bytes(bytes(data))

    # 自检
    check = inspect_durations(path)
    for rep, got in zip(reports, check["tracks"]):
        assert got["samples"] == rep.samples, "stts Σcount 被改变"
        assert got["stts_units"] == rep.target_units, "stts 总时长未对齐目标"
        assert got["mdhd_duration"] == rep.target_units, "mdhd 未对齐目标"
        if elst_mode == "fake":
            expected = ([movie_target] + rep.elst_segment_durations[1:]
                        if rep.elst_segment_durations else [])
        else:
            expected = rep.elst_segment_durations
        assert got["elst_segment_durations"] == expected, \
            f"elst 与所选口径({elst_mode})不一致"
    assert check["mvhd_duration"] == movie_target, "mvhd 未对齐目标"
    return PatchReport(target_seconds=target_seconds,
                       movie_timescale=movie_ts, tracks=reports)


# ---------------------------------------------------------------------------
# SKT4 式时长伪装(黑五·元数据 / 元数据2)
# ---------------------------------------------------------------------------

def disguise_duration_skt4(path: str, fake_sec: float,
                           align_elst: bool = True) -> dict:
    """按原版 SKT4 参考写法改写三套时长(等长字段改写,不动 stts)。

    依据 3.3 脱壳镜像 tab_skt4 的 disguise_duration 注释原文:
      - mvhd:timescale=1000,duration=fake_sec*1000
      - mdhd:保留原 timescale,duration=fake_sec*timescale
      - tkhd:duration=fake_sec*同轨 mdhd.timescale(非 movie 时基)
    与 patch_fake_duration 的差别:本函数不改采样表(stts 不动),
    显示时长口径靠三套 duration 字段;ffprobe 的 format duration 读
    mvhd,流 duration 读 mdhd。

    align_elst(复刻补充,默认开):原版不动 elst,但实测 ffmpeg 系
    读取器以 elst 段时长为准(ffprobe 仍读真实时长);对齐 elst 首段
    后 ffprobe/播放器口径一致读假时长。要严格原版行为传 False。
    """
    data = bytearray(Path(path).read_bytes())
    top = parse_file(bytes(data))
    moov = next((b for b in top if b.type == b"moov"), None)
    if moov is None:
        raise RuntimeError("未找到 moov box")
    mvhd = moov.find(b"mvhd")
    c = mvhd.content_start
    version = data[c]
    ts_off = c + 4 + (8 + 8 if version == 1 else 4 + 4)
    dur_off, dur_size = ts_off + 4, 8 if version == 1 else 4
    struct.pack_into(">I", data, ts_off, 1000)  # mvhd timescale=1000
    _write_u32_or_u64(data, dur_off, dur_size, int(round(fake_sec * 1000)))
    stats = {"mvhd_timescale": 1000, "mvhd_duration": int(round(fake_sec * 1000)),
             "tracks": []}
    for trak in [b for b in moov.children if b.type == b"trak"]:
        mdhd = trak.find(b"mdhd")
        ts, _, md_off, md_size = _read_timescale_duration(bytes(data), mdhd)
        media_units = int(round(fake_sec * ts))
        _write_u32_or_u64(data, md_off, md_size, media_units)
        tkhd = trak.find(b"tkhd")
        _, tk_off, tk_size = _read_tkhd_duration(bytes(data), tkhd)
        # 原版:tkhd 用同轨 mdhd timescale(非 movie 时基)
        _write_u32_or_u64(data, tk_off, tk_size, media_units)
        stats["tracks"].append({"mdhd_timescale": ts,
                                "mdhd_duration": media_units,
                                "tkhd_duration": media_units})
    if align_elst:
        movie_target = int(round(fake_sec * 1000))  # elst 段用 mvhd 时基
        for elst in moov.find_all(b"elst"):
            c = elst.content_start
            entries = read_elst(bytes(data), elst)
            if not entries:
                continue
            seg_size = 8 if data[c] == 1 else 4
            _write_u32_or_u64(data, c + 8, seg_size, movie_target)
        stats["elst"] = "aligned"
    else:
        stats["elst"] = "untouched(原版口径)"
    Path(path).write_bytes(bytes(data))
    return stats


# ---------------------------------------------------------------------------
# 通用 box 级操作(指纹后处理用)
# ---------------------------------------------------------------------------

def replace_box(data: bytearray, box: Box, new_bytes: bytes) -> int:
    """用 new_bytes 替换 box(可变长),修正祖先 size 与 stco/co64 偏移。

    返回 size 差。box 必须来自对 data 当前内容的一次 parse。
    """
    old_start, old_size = box.start, box.size
    delta = len(new_bytes) - old_size
    data[old_start:old_start + old_size] = new_bytes
    for anc in _ancestors(box):
        _adjust_box_size(data, anc, delta)
    _adjust_chunk_offsets(data, old_start + old_size, delta)
    return delta


def remove_box(data: bytearray, box: Box) -> int:
    """删除一个 box,修正祖先 size 与 chunk 偏移。返回 size 差(负值)。"""
    old_start, old_size = box.start, box.size
    data[old_start:old_start + old_size] = b""
    for anc in _ancestors(box):
        _adjust_box_size(data, anc, -old_size)
    _adjust_chunk_offsets(data, old_start + old_size, -old_size)
    return -old_size


def insert_box(data: bytearray, parent: Box, payload: bytes,
               position: str = "append") -> int:
    """在 parent 容器 box 内插入一个已封装的 box(默认追加到末尾)。

    position="prepend" 时插到 parent 内容最前。返回插入字节数。
    """
    if position == "prepend":
        at = parent.content_start
    else:
        at = parent.start + parent.size
    data[at:at] = payload
    for anc in [parent, *_ancestors(parent)]:
        _adjust_box_size(data, anc, len(payload))
    _adjust_chunk_offsets(data, at, len(payload))
    return len(payload)


def make_box(btype: bytes, content: bytes) -> bytes:
    return struct.pack(">I", 8 + len(content)) + btype + content


def find_moov(data: bytes) -> Box:
    top = parse_file(data)
    moov = next((b for b in top if b.type == b"moov"), None)
    if moov is None:
        raise RuntimeError("未找到 moov box")
    return moov


def patch_mp4a_sample_rate(path: str, rate: int) -> bool:
    """把音频轨 mp4a sample entry 的采样率字段(16.16 定点)改为 rate。

    黑五批处理模式三的冻结怪值 195247:标准 ffmpeg 原生 aac 编码器
    拒绝非常规采样率(上限 96000),原版的定制 ffmpeg 直接接受;
    复刻等效做法——正常编码后在容器层写字段(等长改写)。
    """
    data = bytearray(Path(path).read_bytes())
    moov = find_moov(bytes(data))
    for trak in [b for b in moov.children if b.type == b"trak"]:
        if not _is_audio_track(bytes(data), trak):
            continue
        stsd = trak.find(b"stsd")
        if stsd is None:
            continue
        pos = stsd.content_start + 8
        if pos + 8 > stsd.start + stsd.size:
            continue
        size = struct.unpack(">I", data[pos:pos + 4])[0]
        btype = bytes(data[pos + 4:pos + 8])
        if btype not in (b"mp4a", b"sowt", b"twos"):
            continue
        rate_off = pos + 8 + 24  # entry 内容 +24 为 16.16 采样率
        struct.pack_into(">I", data, rate_off, (rate << 16) & 0xFFFFFFFF)
        Path(path).write_bytes(bytes(data))
        return True
    return False


def inspect_durations(path: str) -> dict:
    """解析 MP4 的各口径时长,供验证与调试(不改文件)。"""
    data = Path(path).read_bytes()
    top = parse_file(data)
    moov = next(b for b in top if b.type == b"moov")
    mvhd = moov.find(b"mvhd")
    movie_ts, mvhd_dur, _, _ = _read_timescale_duration(data, mvhd)
    tracks = []
    for trak in [b for b in moov.children if b.type == b"trak"]:
        mdhd = trak.find(b"mdhd")
        ts, md_dur, _, _ = _read_timescale_duration(data, mdhd)
        tkhd = trak.find(b"tkhd")
        tk_dur, _, _ = _read_tkhd_duration(data, tkhd)
        stts = trak.find(b"stts")
        entries = read_stts(data, stts)
        elst = trak.find(b"elst")
        tracks.append({
            "timescale": ts,
            "mdhd_duration": md_dur,
            "tkhd_duration": tk_dur,
            "samples": sum(c for c, _ in entries),
            "stts_units": sum(c * d for c, d in entries),
            "stts_entries": len(entries),
            "elst_segment_durations": (
                [e[0] for e in read_elst(data, elst)]
                if elst is not None else []),
        })
    return {"movie_timescale": movie_ts, "mvhd_duration": mvhd_dur,
            "tracks": tracks}
