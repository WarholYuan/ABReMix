"""ffmpeg 执行器:subprocess 运行、进度解析、失败诊断。

对应方案 §1/§3 的 core/runner.py。设计要求(方案 §1「ffmpeg 命令可观测」):
- 每条命令先打印完整命令行(便于调试与验收比对);
- 实时解析 ffmpeg stderr 的 time= 字段回报进度;
- 失败时抛 FFmpegError,携带完整命令与 stderr 尾部摘要。
"""
from __future__ import annotations

import re
import subprocess
import sys

_TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")


class FFmpegError(RuntimeError):
    def __init__(self, cmd: list[str], returncode: int, stderr_tail: str):
        self.cmd = cmd
        self.returncode = returncode
        self.stderr_tail = stderr_tail
        super().__init__(
            "ffmpeg 执行失败 (exit=%s)\n命令: %s\nstderr 摘要(尾部):\n%s"
            % (returncode, " ".join(f'"{c}"' if " " in c else c for c in cmd), stderr_tail)
        )


def format_cmd(cmd: list[str]) -> str:
    return " ".join(f'"{c}"' if (" " in c or ";" in c) else c for c in cmd)


def _normalize_ffmpeg_cmd(cmd: list[str]) -> list[str]:
    """兼容现代 FFmpeg (7+/8+/9+): 将已移除的 -vsync 自动映射为 -fps_mode。"""
    new_cmd: list[str] = []
    i = 0
    val_map = {
        "0": "passthrough",
        "1": "cfr",
        "2": "vfr",
        "vfr": "vfr",
        "cfr": "cfr",
        "passthrough": "passthrough",
    }
    while i < len(cmd):
        arg = cmd[i]
        if arg == "-vsync":
            if i + 1 < len(cmd):
                val = cmd[i + 1]
                new_cmd.extend(["-fps_mode", val_map.get(val, val)])
                i += 2
                continue
            else:
                new_cmd.append("-fps_mode")
                i += 1
                continue
        new_cmd.append(arg)
        i += 1
    return new_cmd


def run_ffmpeg(
    cmd: list[str],
    total_duration: float | None = None,
    label: str = "",
    quiet: bool = False,
) -> None:
    """运行一条 ffmpeg 命令;失败抛 FFmpegError。

    total_duration 给出时,根据 stderr 的 time= 打印百分比进度。
    """
    cmd = _normalize_ffmpeg_cmd(cmd)
    if not quiet:
        prefix = f"[{label}] " if label else ""
        print(f"{prefix}$ {format_cmd(cmd)}", file=sys.stderr, flush=True)
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
    )
    tail: list[str] = []
    last_pct = -1
    assert proc.stderr is not None
    for line in proc.stderr:
        tail.append(line)
        if len(tail) > 60:
            tail.pop(0)
        if total_duration and not quiet:
            m = _TIME_RE.search(line)
            if m:
                secs = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
                pct = min(99, int(secs / total_duration * 100))
                if pct >= last_pct + 10:
                    last_pct = pct
                    print(f"  ... {pct}%", file=sys.stderr, flush=True)
    proc.wait()
    if proc.returncode != 0:
        raise FFmpegError(cmd, proc.returncode, "".join(tail[-25:]).strip())


def run_checked(cmd: list[str], label: str = "") -> str:
    """运行非 ffmpeg 的辅助命令(如 ffprobe 之外的工具),返回 stdout。"""
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"命令失败({label}): {' '.join(cmd)}\n{proc.stderr.strip()[:500]}"
        )
    return proc.stdout
