"""ffmpeg / ffprobe 定位。

对应原版模块 `ffmpeg_resolve.py`(`resolve_tab_ffmpeg` / `app_root`,负责在多套
ffmpeg 间实测择优),见《肥猫 3.3 逆向分析报告》§3 / §6。

本复刻版的查找顺序:
  1. 环境变量 FEIMAO_FFMPEG / FEIMAO_FFPROBE
  2. 项目内 tools/ 目录(可放静态构建)
  3. 系统 PATH

本机(开发/测试环境)实测:系统 PATH 自带 ffmpeg 8.1.2(Ubuntu 包),含
libx264 与全部所需滤镜(life/zoompan/scale2ref/interleave/blend/geq 等),
故未另行安装,直接走 PATH 路线。
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

_cache: dict[str, str] = {}


def _candidates(env_var: str, binary: str) -> list[str]:
    out: list[str] = []
    env_val = os.environ.get(env_var)
    if env_val:
        out.append(env_val)
    tools_bin = _PROJECT_ROOT / "tools" / binary
    out.append(str(tools_bin))
    found = shutil.which(binary)
    if found:
        out.append(found)
    out.append(binary)  # 最后兜底:直接按名调用
    return out


def _resolve(env_var: str, binary: str) -> str:
    if binary in _cache:
        return _cache[binary]
    for cand in _candidates(env_var, binary):
        if cand == binary or Path(cand).exists():
            _cache[binary] = cand
            return cand
    _cache[binary] = binary
    return binary


def ffmpeg_path() -> str:
    return _resolve("FEIMAO_FFMPEG", "ffmpeg")


def ffprobe_path() -> str:
    return _resolve("FEIMAO_FFPROBE", "ffprobe")


