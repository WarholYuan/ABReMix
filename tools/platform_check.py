#!/usr/bin/env python3
"""平台版抽帧验证：判断一条已发布/已转码的视频里 AB 交织是否还在。

用法:
    python3 tools/platform_check.py <视频文件> [--frames 30]

原理（纯标准库，只依赖 ffmpeg/ffprobe）:
  - ffprobe 读规格:分辨率 / 平均帧率 / 时长 / 总帧数
  - 从第 0 帧起取前 N 帧,缩到 128x72 灰度 raw 帧
  - 交替签名:真 AB 片相邻帧属于不同轨道,MAD(i, i+1) 应显著大于
    同轨距离 MAD(i, i+2);自然视频两者接近(比值≈1)
  - 输出中位数比值与结论。比值高 = 交替结构还在;≈1 = 交织已被
    平台转码压掉(或本来就不是 AB 片)

注意:本工具只验"交替结构在不在",不判断过审/查重效果。
辅轨为动态背景、主轨运动剧烈时比值会被稀释,结论会落到"不确定"。
"""
import argparse
import json
import statistics
import subprocess
import sys

W, H = 128, 72
FRAME_BYTES = W * H


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries",
         "stream=width,height,avg_frame_rate,nb_frames:format=duration",
         "-of", "json", path],
        capture_output=True, text=True, check=True).stdout
    d = json.loads(out)
    s = d["streams"][0]
    num, den = s["avg_frame_rate"].split("/")
    fps = float(num) / float(den) if float(den) else 0.0
    return {
        "width": s["width"], "height": s["height"], "fps": round(fps, 2),
        "nb_frames": s.get("nb_frames"),
        "duration": round(float(d["format"]["duration"]), 2),
    }


def read_frames(path, n):
    cmd = ["ffmpeg", "-v", "error", "-i", path,
           "-vf", f"scale={W}:{H},format=gray",
           "-frames:v", str(n), "-f", "rawvideo", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return [raw[i:i + FRAME_BYTES]
            for i in range(0, len(raw) - FRAME_BYTES + 1, FRAME_BYTES)]


def mad(a, b):
    return sum(abs(x - y) for x, y in zip(a, b)) / FRAME_BYTES


def main():
    ap = argparse.ArgumentParser(description="平台版 AB 交织抽帧验证")
    ap.add_argument("video")
    ap.add_argument("--frames", type=int, default=30,
                    help="抽验帧数(默认 30,从第 0 帧起)")
    args = ap.parse_args()

    info = probe(args.video)
    frames = read_frames(args.video, args.frames)
    if len(frames) < 6:
        print("帧数太少,无法验证"); sys.exit(2)

    adj = [mad(frames[i], frames[i + 1]) for i in range(len(frames) - 1)]
    skip = [mad(frames[i], frames[i + 2]) for i in range(len(frames) - 2)]
    m_adj, m_skip = statistics.median(adj), statistics.median(skip)
    ratio = m_adj / m_skip if m_skip > 0 else float("inf")

    if ratio >= 1.8:
        verdict = "交替结构仍在:相邻帧差异显著大于同轨距离,AB 交织大概率熬过了平台转码"
    elif ratio >= 1.3:
        verdict = "不确定:有交替迹象但被稀释(动态辅轨/主轨运动剧烈/平台半压),建议换中段帧再验或与源轨逐帧比对"
    else:
        verdict = "未检出交替:交织已被平台转码压掉,或该文件本身不是 AB 片"

    print(f"文件: {args.video}")
    print(f"规格: {info['width']}x{info['height']}  {info['fps']} fps  "
          f"{info['duration']}s  总帧数 {info['nb_frames']}")
    print(f"抽验: 前 {len(frames)} 帧")
    print(f"相邻帧 MAD 中位数: {m_adj:.2f}")
    print(f"隔帧(同轨) MAD 中位数: {m_skip:.2f}")
    print(f"比值: {ratio:.2f}  (自然视频≈1.0,AB 片显著>1)")
    print(f"结论: {verdict}")


if __name__ == "__main__":
    main()
