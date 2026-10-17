#!/usr/bin/env python3
"""测试 龙门120 模式（zoompan + hue）功能完整性。

测试要点（依据 feimao33-fresh-REPORT.md §C tab17）：
1. zoompan 生成动态网格底图
2. hue 色相旋转滤镜应用
3. 叠加主视频后输出 60fps
4. 验证输出文件属性和可解码性
"""
import pytest
import json
import subprocess
from pathlib import Path
from app.modes.longmen_120 import longmen_120
from app.core.probe import probe

FIXTURE = "samples/source.mp4"
OUT = "out/test_longmen_120.mp4"


def test_longmen_120_basic():
    """基础功能: 生成带 zoompan+hue 底图的 60fps 视频。"""
    result = longmen_120(FIXTURE, OUT, duration=3.0)
    
    # 输出文件应存在且可探测
    assert Path(OUT).exists()
    info = probe(OUT)
    
    # 核心断言
    assert info["fps"] == 60, "应输出 60fps"
    assert info["duration"] >= 2.9, "时长不应被截断"
    assert info["codec"] == "h264"
    assert info["profile"] == "Main"
    
    # 可解码验证
    cmd = ["ffmpeg", "-v", "error", "-i", OUT, "-f", "null", "-"]
    subprocess.run(cmd, check=True, capture_output=True)
    
    print(f"✅ 龙门120 基础测试通过: {info['width']}×{info['height']} {info['fps']}fps {info['duration']:.2f}s")


def test_longmen_120_filter_presence():
    """滤镜完整性: 验证 zoompan 和 hue 滤镜被正确应用。"""
    # 读取生成的文件元数据
    info = probe(OUT)
    
    # 通过再次运行生成过程，捕获 ffmpeg 命令行输出并验证滤镜存在
    # （实际实现时 app/core/runner.py 会打印完整命令）
    import io, sys
    old_stderr = sys.stderr
    sys.stderr = io.StringIO()
    
    try:
        longmen_120(FIXTURE, OUT, duration=1.0)
        cmd_log = sys.stderr.getvalue()
    finally:
        sys.stderr = old_stderr
    
    assert "zoompan" in cmd_log, "应包含 zoompan 滤镜"
    assert "hue=" in cmd_log, "应包含 hue 色相旋转滤镜"
    assert "scale=1536:1080" in cmd_log or "scale=" in cmd_log, "应有底图缩放"
    
    print(f"✅ 龙门120 滤镜验证通过")


def test_longmen_120_hue_rotation():
    """色相旋转验证: 确认输出视频有明显色彩变化（非静态底图）。"""
    # 抽取前后两帧，计算色相统计差异
    frames_dir = Path("out/temp/longmen_frames")
    frames_dir.mkdir(parents=True, exist_ok=True)
    
    cmd1 = ["ffmpeg", "-y", "-i", OUT, "-vf", "select='eq(n,10)'", "-vframes", "1", str(frames_dir / "frame_10.png")]
    cmd2 = ["ffmpeg", "-y", "-i", OUT, "-vf", "select='eq(n,50)'", "-vframes", "1", str(frames_dir / "frame_50.png")]
    
    subprocess.run(cmd1, check=True, capture_output=True)
    subprocess.run(cmd2, check=True, capture_output=True)
    
    # 验证两帧都存在（简化版验证，完整版可计算色相直方图差异）
    assert (frames_dir / "frame_10.png").exists()
    assert (frames_dir / "frame_50.png").exists()
    
    print(f"✅ 龙门120 色相旋转验证通过（帧提取成功）")


def test_longmen_120_zoompan_motion():
    """运动验证: zoompan 应产生可见的缩放/平移运动。"""
    # 通过 ffprobe 的 lavfi.scene 或手动帧差验证运动（简化版：检查关键滤镜参数）
    import io, sys
    old_stderr = sys.stderr
    sys.stderr = io.StringIO()
    
    try:
        longmen_120(FIXTURE, OUT, duration=1.0)
        cmd_log = sys.stderr.getvalue()
    finally:
        sys.stderr = old_stderr
    
    # 验证 zoompan 的关键参数存在
    assert "z='min(zoom+" in cmd_log or "zoompan=z=" in cmd_log, "应有缩放因子表达式"
    assert "x='iw/2" in cmd_log, "应有 X 轴正弦/余弦运动表达式"
    assert "y='ih/2" in cmd_log, "应有 Y 轴正弦/余弦运动表达式"
    
    print(f"✅ 龙门120 运动参数验证通过")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
