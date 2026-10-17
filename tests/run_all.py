#!/usr/bin/env python3
"""全模式实跑验证:逐个 CLI 子命令实际执行 + ffprobe 核查,结果写 TESTING.md。

用法:python3 tests/run_all.py [--only 关键字]
素材在 tests/samples/(不存在时自动生成);产物在 tests/outputs/。
每个模式固定 seed=42,失败如实记录 stderr 摘要,不中断整批。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "tests" / "samples"
OUTPUTS = ROOT / "tests" / "outputs"
CLI = [sys.executable, str(ROOT / "cli.py")]
TIMEOUT = 1200

MAIN = SAMPLES / "main_land.mp4"      # testsrc2 1280x720@30 10s + 440Hz
MAIN_P = SAMPLES / "main_port.mp4"    # testsrc2 720x1280@30 10s + 440Hz
AUX = SAMPLES / "aux_land.mp4"        # testsrc  1280x720@30 10s + 880Hz
AUX2 = SAMPLES / "aux2_land.mp4"       # smptebars 1280x720@30 10s + 660Hz


def gen_samples() -> None:
    SAMPLES.mkdir(parents=True, exist_ok=True)
    specs = [
        (MAIN, "testsrc2=size=1280x720:rate=30:duration=10", 440),
        (MAIN_P, "testsrc2=size=720x1280:rate=30:duration=10", 440),
        (AUX, "testsrc=size=1280x720:rate=30:duration=10", 880),
        (AUX2, "smptebars=size=1280x720:rate=30:duration=10", 660),
    ]
    for path, src, freq in specs:
        if path.exists():
            continue
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", src,
             "-f", "lavfi", "-i", f"sine=frequency={freq}:duration=10",
             "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-shortest", str(path)],
            check=True,
        )
    # 批量测试目录:2 个主片 + 2 个辅轨;自动辅轨目录:1 个主片
    bin_dir, baux_dir, bin_auto = (ROOT / "tests" / "batch_in",
                                   ROOT / "tests" / "batch_aux",
                                   ROOT / "tests" / "batch_in_auto")
    for d in (bin_dir, baux_dir, bin_auto):
        d.mkdir(parents=True, exist_ok=True)
    import shutil
    for src, dst in [(MAIN, bin_dir / "main_land.mp4"),
                     (MAIN_P, bin_dir / "main_port.mp4"),
                     (AUX, baux_dir / "aux_land.mp4"),
                     (AUX2, baux_dir / "aux2_land.mp4"),
                     (MAIN, bin_auto / "main_land.mp4")]:
        if not dst.exists():
            shutil.copy(src, dst)


def probe_full(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
         "-show_entries",
         "stream=width,height,avg_frame_rate,nb_read_frames,codec_name",
         "-show_entries", "format=format_name,duration",
         "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    data = json.loads(out.stdout)
    stream = (data.get("streams") or [{}])[0]
    fmt = data.get("format", {})
    fps_text = stream.get("avg_frame_rate", "0/1")
    try:
        num, den = fps_text.split("/")
        fps = float(num) / float(den) if float(den) else 0.0
    except ValueError:
        fps = 0.0
    return {
        "format": (fmt.get("format_name") or "").split(",")[0],
        "size": f"{stream.get('width')}x{stream.get('height')}",
        "fps": f"{fps:.1f}",
        "frames": stream.get("nb_read_frames", "?"),
        "duration": f"{float(fmt.get('duration') or 0):.1f}",
        "codec": stream.get("codec_name", ""),
    }


def run_case(name: str, argv: list[str], expect_out: Path | None,
             note: str = "") -> dict:
    print(f"== {name} ==", flush=True)
    t0 = time.time()
    proc = subprocess.run(CLI + argv, capture_output=True, text=True,
                          timeout=TIMEOUT)
    elapsed = time.time() - t0
    row = {"name": name, "note": note, "elapsed": f"{elapsed:.0f}s"}
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()
        row.update(result="✗ 失败",
                   detail="; ".join(tail[-3:])[:300] if tail else "?")
        return row
    target = expect_out
    if target is not None and not target.exists():
        row.update(result="✗ 失败", detail="命令成功但输出文件不存在")
        return row
    if target is not None:
        info = probe_full(target)
        row.update(result="✓ 通过", detail="", **info)
    else:
        row.update(result="✓ 通过", detail=proc.stdout.strip()[:120])
    return row


def main() -> int:
    only = sys.argv[2] if len(sys.argv) > 2 and sys.argv[1] == "--only" else ""
    gen_samples()
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    O = OUTPUTS
    cases: list[tuple[str, list[str], Path | None, str]] = [
        ("纯AB pure-ab",
         ["pure-ab", "-i", str(MAIN), "-a", str(AUX), "-o", str(O / "pure_ab.mp4")],
         O / "pure_ab.mp4", "期望 90fps / 900 帧"),
        ("105:35 interleave-105-35",
         ["interleave-105-35", "-i", str(MAIN), "-a", str(AUX), "-o", str(O / "v105_35.mp4")],
         O / "v105_35.mp4", "原文滤镜;期望 ≈140fps 交织、剔 n=1 一帧"),
        ("180小 ab-180",
         ["ab-180", "-i", str(MAIN), "-a", str(AUX), "-o", str(O / "ab180.mp4")],
         O / "ab180.mp4", "期望 180fps / 1800 帧"),
        ("反转AB fanzhuan-ab(横)",
         ["fanzhuan-ab", "-i", str(MAIN), "-a", str(AUX), "-o", str(O / "fanzhuan_land.mp4")],
         O / "fanzhuan_land.mp4", "期望 1280x720 / 120fps"),
        ("反转AB fanzhuan-ab(竖)",
         ["fanzhuan-ab", "-i", str(MAIN_P), "-a", str(AUX), "-o", str(O / "fanzhuan_port.mp4")],
         O / "fanzhuan_port.mp4", "期望 720x1280 / 120fps"),
        ("新120帧 new-120",
         ["new-120", "-i", str(MAIN), "-o", str(O / "new120.mp4"), "--seed", "42"],
         O / "new120.mp4", "五段流水线;合成段 120:60"),
        ("新反转 xin-fanzhuan",
         ["xin-fanzhuan", "-i", str(MAIN), "-a", str(AUX), "-o", str(O / "xin_fanzhuan.mp4")],
         O / "xin_fanzhuan.mp4", "帧序列交替;期望 120fps"),
        ("1B3A one-b-three-a",
         ["one-b-three-a", "-i", str(MAIN), "-a", str(AUX), "-o", str(O / "one_b_three_a.mp4")],
         O / "one_b_three_a.mp4", "帧序列 1+3;期望 40fps / 时长 10s"),
        ("帧间隔 frame-gap",
         ["frame-gap", "-i", str(MAIN), "-a", str(AUX), "-o", str(O / "frame_gap.mp4"),
          "--period", "3", "--count", "2", "--effect", "edge"],
         O / "frame_gap.mp4", "每 3 帧叠 2 帧 + 边缘检测特效"),
        ("随机帧AB random-frame-ab(single)",
         ["random-frame-ab", "-i", str(MAIN), "-a", str(AUX), "-o", str(O / "random_single.mp4"),
          "--method", "single", "--seed", "42"],
         O / "random_single.mp4", "seed=42 随机抽一帧叠加"),
        ("随机帧AB random-frame-ab(periodic+blur)",
         ["random-frame-ab", "-i", str(MAIN), "-a", str(AUX), "-o", str(O / "random_periodic.mp4"),
          "--method", "periodic", "--seed", "42", "--effect", "blur",
          "--period", "3", "--count", "2", "--opacity", "0.5"],
         O / "random_periodic.mp4", "特效先烤进 3s 短循环片段再叠"),
        ("单帧AB single-frame-ab",
         ["single-frame-ab", "-i", str(MAIN), "-a", str(AUX), "-o", str(O / "single_frame.mp4")],
         O / "single_frame.mp4", "短 GOP + faststart"),
        ("单帧AB single-frame-ab(横竖互换)",
         ["single-frame-ab", "-i", str(MAIN), "-a", str(AUX), "-o", str(O / "single_frame_swap.mp4"),
          "--swap-orient"],
         O / "single_frame_swap.mp4", "期望输出 720x1280"),
        ("实拍AB realshot-ab",
         ["realshot-ab", "-i", str(MAIN), "-a", str(AUX), "-o", str(O / "realshot.mp4")],
         O / "realshot.mp4", "动效半透明全程叠加"),
        ("双帧 double-frame",
         ["double-frame", "-i", str(MAIN), "-a", str(AUX), "-o", str(O / "double_frame.mp4")],
         O / "double_frame.mp4", "双轨逐帧 50% 混合"),
        ("闪帧 flash-frame(带辅轨)",
         ["flash-frame", "-i", str(MAIN), "-a", str(AUX), "-o", str(O / "flash_aux.mp4"),
          "--seed", "42"],
         O / "flash_aux.mp4", "辅轨首帧交替;期望 120fps"),
        ("闪帧 flash-frame(无辅轨·自动图案)",
         ["flash-frame", "-i", str(MAIN), "-o", str(O / "flash_auto.mp4"),
          "--seed", "42"],
         O / "flash_auto.mp4", "自动生成随机图案首帧;期望 120fps"),
        ("三帧叠背景 three-frame-bg",
         ["three-frame-bg", "-i", str(MAIN), "-a", str(AUX), "-o", str(O / "three_frame.mp4")],
         O / "three_frame.mp4", "第 4 帧起隔帧叠;保持原分辨率"),
        ("9-08批量 batch-908(辅轨池)",
         ["batch-908", "--input-dir", str(ROOT / "tests" / "batch_in"),
          "--aux-dir", str(ROOT / "tests" / "batch_aux"),
          "--out-dir", str(O / "batch908"), "--seed", "42"],
         None, "2 主片 × 2 辅轨循环分配;输出目录 batch908/"),
        ("9-08批量 batch-908(自动辅轨)",
         ["batch-908", "--input-dir", str(ROOT / "tests" / "batch_in_auto"),
          "--out-dir", str(O / "batch908_auto"), "--auto-aux", "blocks",
          "--seed", "42"],
         None, "无辅轨时自动生成乱色块辅轨"),
        ("动态A dongtai-a",
         ["dongtai-a", "-i", str(MAIN), "-o", str(O / "dongtai_a.mp4"),
          "--bg-gen", "grid", "--seed", "42"],
         O / "dongtai_a.mp4", "grid 动态背景作 A 轨,主视频居中叠加"),
        ("life复合 life-blend",
         ["life-blend", "-i", str(MAIN), "-o", str(O / "life_blend.mp4"),
          "--seed", "42"],
         O / "life_blend.mp4", "原文滤镜:生命游戏 + 按行 blend"),
        ("卡时长 card-duration(精确版)",
         ["card-duration", "-i", str(MAIN), "-o", str(O / "card_duration.mp4"),
          "--seed", "42"],
         O / "card_duration.mp4", "精确版:mvhd/tkhd/mdhd/dirty stts/elst 全口径对齐随机 25–60 分钟;ffprobe 读伪时长(盒内与 ffprobe 双口径实测),全帧可解"),
        ("魔术帧 magic-frame",
         ["magic-frame", "-i", str(MAIN), "-a", str(AUX),
          "-o", str(O / "magic_frame.mp4")],
         O / "magic_frame.mp4", "90:30 交织(每 3 帧 2 主 1 辅、剔头 2 帧);音频默认直通,--audio-mode 1-5 可叠 AB 隐藏;专项断言见 test_magic_frame.py / test_ab_audio.py"),
        ("隔帧叠加 overlay-120",
         ["overlay-120", "-i", str(MAIN), "-a", str(AUX),
          "-o", str(O / "overlay_120.mp4")],
         O / "overlay_120.mp4", "120fps 隔帧叠加:奇数帧整帧叠辅轨(与 new-120 交织不同);逐帧奇偶断言见 test_overlay_120.py"),
        ("AB音频 ab-audio(模式1)",
         ["ab-audio", "-i", str(MAIN), "-a", str(AUX), "--mode", "1",
          "-o", str(O / "ab_audio_m1.mp4")],
         O / "ab_audio_m1.mp4", "声道隐藏 模式1(机审听B/耳机听A);模式 1-5 与魔术帧集成的专项断言见 test_ab_audio.py"),
        ("BL-1 bl-1(冻结模板样例)",
         ["bl-1", "-i", str(MAIN), "-a", str(AUX), "--out-dir", str(O / "bl1")],
         None, "四步原文模板;成片在 bl1/ 下 *_BL-1_*.mkv"),
        ("BL-2 bl-2(冻结模板样例)",
         ["bl-2", "-i", str(MAIN), "-a", str(AUX), "--out-dir", str(O / "bl2")],
         None, "Step2 背景预生成版;成片在 bl2/ 下 *_BL-2_*.mkv"),
    ]
    # 生成背景 8 种(小尺寸短时长,验证生成器本身)
    for kind in ("grid", "life", "noise", "checker", "wave", "dots",
                 "color", "blocks"):
        out = O / f"bg_{kind}.mp4"
        cases.append((
            f"生成背景 gen-bg({kind})",
            ["gen-bg", "--kind", kind, "-o", str(out), "--width", "640",
             "--height", "360", "--duration", "3", "--seed", "42"],
            out, "3s / 640x360",
        ))

    rows = []
    for name, argv, expect, note in cases:
        if only and only not in name:
            continue
        try:
            row = run_case(name, argv, expect, note)
        except subprocess.TimeoutExpired:
            row = {"name": name, "note": note, "result": "✗ 超时",
                   "detail": f"超过 {TIMEOUT}s", "elapsed": f">{TIMEOUT}s"}
        # batch / BL 的产物在子目录里,补探第一个成片
        if expect is None and row["result"] == "✓ 通过":
            if name.startswith("9-08"):
                outs = sorted((O / ("batch908" if "辅轨池" in name else "batch908_auto")).glob("*.mp4"))
                row["detail"] = f"产出 {len(outs)} 个成片"
            elif name.startswith("BL"):
                sub = "bl1" if "BL-1" in name else "bl2"
                outs = sorted((O / sub).glob("*.mkv"))
                if outs:
                    info = probe_full(outs[0])
                    row.update(info)
                    row["detail"] = outs[0].name
        rows.append(row)
        print(f"  -> {row['result']} ({row.get('elapsed', '?')})", flush=True)

    ok = sum(1 for r in rows if r["result"].startswith("✓"))
    version = subprocess.run(["ffmpeg", "-version"], capture_output=True,
                             text=True).stdout.splitlines()[0]
    lines = [
        "# 实跑验证记录(TESTING)",
        "",
        f"- 日期:2026-10-13",
        f"- 环境:{version};Python {sys.version.split()[0]}(系统 ffmpeg 走 PATH,见 README)",
        "- 素材:testsrc2/testsrc/smptebars + 正弦音轨,1280x720@30fps 10s(竖屏 720x1280 一份)",
        "- 约定:随机项一律 --seed 42;帧数为 ffprobe -count_frames 实解码计数",
        "",
        "| # | 模式 | 结果 | 容器 | 分辨率 | 帧率 | 帧数 | 时长(s) | 备注 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(rows, 1):
        note = r.get("note", "")
        if r["result"].startswith("✗"):
            note = (note + " | 失败:" + r.get("detail", ""))[:220]
        lines.append(
            f"| {i} | {r['name']} | {r['result']} | {r.get('format', '')} "
            f"| {r.get('size', '')} | {r.get('fps', '')} | {r.get('frames', '')} "
            f"| {r.get('duration', '')} | {note} |"
        )
    lines += [
        "",
        f"**合计:{ok}/{len(rows)} 通过。** 失败项的原因见对应行备注,不隐瞒、不跳过。",
        "",
        "说明:pure-ab 首跑曾出现 900 帧被默认 fps_mode 丢到 835 帧的问题,",
        "已在骨架运行器加 `-fps_mode passthrough` 修复(见 app/modes/common.py 注释)。",
    ]
    (ROOT / "TESTING.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n===== {ok}/{len(rows)} 通过,TESTING.md 已写入 =====")
    return 0 if ok == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
