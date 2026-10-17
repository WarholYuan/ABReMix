"""模板引擎 + 冻结模板库。

对应方案 §3 的 core/template_engine.py 与 §4「参数冻结区」。

⚠ 本文件中的 BL 家族模板字符串与《肥猫 3.3 逆向分析报告》§4 的原文
**逐字一致**(偏移出处标注在每个常量上方),开发中不得“优化”任何参数。
小红书变体的滤镜串凡报告有原文的(105:35、grid、life)同样逐字录入;
无完整原文的模式按报告给出的参数(帧率配比/CRF/剔帧式)在各 mode 函数里
组装,组装规则见 build_interleave_filter()。

模板占位符约定(与原版一致的命名):
  {ffmpeg_path} {input_path} {bg_video_path} {output_path} {duration}
  {width} {height} {filename} {time_tag} {artist} {title} {date}
"""
from __future__ import annotations

import string


# ---------------------------------------------------------------------------
# BL-1 / BL-2 冻结模板(来源:《肥猫 3.3 逆向分析报告》§4,与 2.7 逐字一致)
# ---------------------------------------------------------------------------

# Step 1 — 主视频标准化(报告 §4,@0x4a1b406)
BL_STEP1 = '''"{ffmpeg_path}" -y -i "{input_path}" -filter_complex [0:v]scale=1536:1080:force_original_aspect_ratio=decrease,pad=1536:1080:(ow-iw)/2:(oh-ih)/2:black,setpts=PTS-STARTPTS,setsar=1,fps=30,format=yuv420p,settb=1/30,setpts=N/30/TB,setpts=PTS/0.953[v];[0:a]loudnorm=I=-16:LRA=11:TP=-1.5,volume=1.0,highpass=f=150,lowpass=f=3500[a] -map [v] -map [a] -c:a aac -c:v libx264 -preset fast -pix_fmt yuv420p -profile:v main -b:v 10000k -t {duration} "{output_path}/temp/step1.mp4"'''

# Step 2 — 头部拼 1 秒背景(报告 §4,@0x4a1b5dd;-stream_loop 53、trim 前 30 帧、concat)
BL1_STEP2 = '''"{ffmpeg_path}" -y -i "{output_path}/temp/step1.mp4" -stream_loop 53 -i "{bg_video_path}" -filter_complex [0:v]scale=1536:1080:force_original_aspect_ratio=decrease,pad=1536:1080:(ow-iw)/2:(oh-ih)/2:black,trim=start_frame=0:end_frame=30,setpts=PTS-STARTPTS,setsar=1[first];[1:v]scale=1536:1080:force_original_aspect_ratio=disable,setpts=PTS-STARTPTS,setsar=1,fps=30,format=yuv420p,settb=1/30,setpts=N/30/TB[second];[first][second]concat=n=2:v=1:a=0[v] -map [v] -c:v libx264 -preset fast -pix_fmt yuv420p -profile:v main -b:v 6000k -crf 23 -an -t {duration} "{output_path}/temp/step2.mp4"'''

# Step 3 — scale2ref + interleave 交织、剔除前 19 帧内奇数帧
# (报告 §4,BL-1 @0x4a1b829;BL-2 @0x4a1bcfb,除输出名外相同 —— 用 {mode} 区分)
BL_STEP3 = '''"{ffmpeg_path}" -y -i "{output_path}/temp/step2.mp4" -i "{output_path}/temp/step1.mp4" -filter_complex [1:v][0:v]scale2ref[v1s][v0r];[v0r]setsar=1,fps=30[v0];[v1s]setsar=1,fps=30[v1];[v0][v1]interleave,select='not(eq(n,1)+eq(n,3)+eq(n,5)+eq(n,7)+eq(n,9)+eq(n,11)+eq(n,13)+eq(n,15)+eq(n,17)+eq(n,19))'[v] -map 1:a? -map [v] -c:v libx264 -preset fast -profile:v high -bf 0 -crf 18 -b:v 8000k -vsync vfr -movflags +faststart -f matroska -video_track_timescale 1000k -metadata artist={artist} -t {duration} "{output_path}/{filename}_{mode}_{time_tag}.mkv"'''

# Step 4 — 元数据重写(报告 §4,@0x4a1ba50)
BL_STEP4 = '''"{ffmpeg_path}" -y -i "{output_path}/{filename}_{mode}_{time_tag}.mkv" -c copy -metadata title={title} -metadata date={date} -movflags +faststart -f matroska "{output_path}/temp/meta_temp.mkv"'''

# BL-2 Step 2 — 背景按源宽高预生成(报告 §4,@0x4a1bbf2;循环 50 次、无头部拼接)
BL2_STEP2 = '''"{ffmpeg_path}" -y -stream_loop 50 -i "{bg_video_path}" -vf scale={width}:{height}:force_original_aspect_ratio=disable,setsar=1:1,fps=30 -c:v libx264 -preset fast -pix_fmt yuv420p -profile:v main -b:v 6000k -crf 23 -an -t {duration} "{output_path}/temp/step2.mp4"'''


# ---------------------------------------------------------------------------
# 小红书交织骨架的原文滤镜串(来源:《肥猫 3.3 逆向分析报告》§5.1 / §5.6)
# ---------------------------------------------------------------------------

# 105:35 变体(报告 §5.1,@0x49fac10;2.7 靠运行时脱壳才见、3.3 静态明文可见)
FILTER_105_35 = "[1:v][0:v]scale2ref[v1s][v0r];[v0r]setsar=1,fps=105,format=yuv420p[v0];[v1s]setsar=1,fps=35,format=yuv420p[v1];[v0][v1]interleave,select='not(eq(n,1))',format=yuv420p[v]"

# grid 生成背景(报告 §5.6,@0x49f7478;{zs}/{ax}/{bx}… 为随机抖动占位)
FILTER_GRID = "[0:v]scale={gw}:{gh}:flags=lanczos,zoompan=z='min(zoom+{zs},2.0)':x='iw/2-(iw/zoom/2)+{ax}*sin(on/{bx})':y='ih/2-(ih/zoom/2)+{ay}*cos(on/{by})':d=1:fps=60:s={ow}x{oh},hue=h='{hs}*t+{hb}',format=yuv420p[grid]"

# life 生命游戏背景 + 按行 blend 隔行混合(报告 §5.6,@0x4a332e8)
FILTER_LIFE_BLEND = "life=size={lw}x{lh}:mold=10:rate=30:ratio={ratio:.3f}:random_seed={seed}:death_color={death}:life_color={life}:stitch=0,scale={ow}:{oh}:flags=neighbor,format=yuv420p,hue=h='{hue:.1f}*t'[bg];[0:v]{orient}fps=30,scale={ow}:{oh}:force_original_aspect_ratio=decrease,pad={ow}:{oh}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p[main];[main][bg]blend=all_expr='if(eq(mod(Y\\,2)\\,0)\\,A\\,A*{a:.3f}+B*{b:.3f})':shortest=1,setfield=tff,setpts=PTS-STARTPTS,format=yuv420p[vout]"


# ---------------------------------------------------------------------------
# 交织骨架变体参数表(方案 §4.3;骨架形状同 BL Step3 / FILTER_105_35)
#   main_fps / aux_fps:双轨 fps 配比
#   select:interleave 后的剔帧表达式(None = 不剔)
#   aux_extra:辅轨 fps 后追加的滤镜(如 120:60 的 tpad,见报告 §5.1 与 2.7 脱壳补充)
#   renumber:非 None 时在交织后 settb/setpts 重编号到该帧率(原报告未给出
#             完整命令的模式用此法获得干净时间戳,属复刻组装规则,非原文)
# ---------------------------------------------------------------------------
INTERLEAVE_VARIANTS = {
    "105:35": {  # 报告 §5.1 原文,见 FILTER_105_35;此处仅登记参数
        "main_fps": 105, "aux_fps": 35, "select": "not(eq(n,1))",
        "aux_extra": "", "renumber": None, "verbatim": FILTER_105_35,
    },
    "120:60": {  # 报告 §5.1:主 120fps / 辅 60fps + tpad=start=0:stop_mode=clone
        "main_fps": 120, "aux_fps": 60, "select": "not(eq(n,1))",
        "aux_extra": ",tpad=start=0:stop_mode=clone", "renumber": None,
        "verbatim": None,
    },
    "180": {  # 方案 §4.3:180fps 时间轴,每 3 帧 1 帧动效 + 2 帧实拍(120+60)
        "main_fps": 120, "aux_fps": 60, "select": None,
        "aux_extra": "", "renumber": 180, "verbatim": None,
    },
    "pure90": {  # 报告 §5.1 纯AB:90fps / 自动匹配主片画幅 / 隔帧叠加
        "main_fps": 45, "aux_fps": 45, "select": None,
        "aux_extra": "", "renumber": 90, "verbatim": None,
    },
    "fanzhuan120": {  # 报告 §5.1 反转AB:主+辅各 60fps 交错成 120fps(偶=A、奇=B)
        "main_fps": 60, "aux_fps": 60, "select": None,
        "aux_extra": "", "renumber": 120, "verbatim": None,
    },
    "flash120": {  # 报告 §5.1 闪帧:辅助首帧铺 120 帧与主视频逐帧交替(偶主奇辅)
        "main_fps": 60, "aux_fps": 60, "select": None,
        "aux_extra": "", "renumber": 120, "verbatim": None,
    },
    "ab60": {  # 9-08 系列交错合成:双轨 30fps 交织成 60fps(方案 §5.2 三步流水线第 3 步)
        "main_fps": 30, "aux_fps": 30, "select": None,
        "aux_extra": "", "renumber": 60, "verbatim": None,
    },
}


def build_interleave_filter(variant: str) -> str:
    """按变体参数表组装 scale2ref + interleave 骨架滤镜串。

    形状与 BL Step3 / FILTER_105_35 保持一致:
    [1:v][0:v]scale2ref → 双轨 setsar/fps/format → interleave[,select][,重编号][v]
    有原文的变体(verbatim 非空)直接返回原文。
    """
    spec = INTERLEAVE_VARIANTS[variant]
    if spec.get("verbatim"):
        return spec["verbatim"]  # type: ignore[return-value]
    parts = ["[1:v][0:v]scale2ref[v1s][v0r]"]
    parts.append(f"[v0r]setsar=1,fps={spec['main_fps']},format=yuv420p[v0]")
    parts.append(
        f"[v1s]setsar=1,fps={spec['aux_fps']}{spec['aux_extra']},format=yuv420p[v1]"
    )
    tail = "[v0][v1]interleave"
    if spec["select"]:
        tail += f",select='{spec['select']}'"
    tail += ",format=yuv420p"
    if spec["renumber"]:
        tail += f",settb=1/{spec['renumber']},setpts=N/{spec['renumber']}/TB"
    tail += "[v]"
    parts.append(tail)
    return ";".join(parts)


# ---------------------------------------------------------------------------
# 模板引擎:把 shell 风格模板串渲染为 argv 列表
# ---------------------------------------------------------------------------

class _KeepMissing(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def render(template: str, **values: object) -> str:
    """单串占位符替换(缺失的占位符保持原样,便于分阶段渲染)。"""
    return string.Formatter().vformat(template, (), _KeepMissing(**values))


def render_argv(template: str, **values: object) -> list[str]:
    """把一行 shell 风格命令模板渲染为 argv。

    模板中的双引号按 shell 规则处理:引号内的空格不拆分,引号本身去掉。
    滤镜串中的单引号(如 select='...')原样保留给 ffmpeg。
    """
    text = render(template, **values)
    argv: list[str] = []
    cur: list[str] = []
    in_quotes = False
    started = False
    for ch in text:
        if ch == '"':
            in_quotes = not in_quotes
            started = True
        elif ch in " \t" and not in_quotes:
            if started:
                argv.append("".join(cur))
                cur = []
                started = False
        else:
            cur.append(ch)
            started = True
    if started:
        argv.append("".join(cur))
    return argv


def time_tag_now() -> str:
    """输出命名用的时间戳标签(原版 {time_tag},规则未恢复,复刻用本地时间)。"""
    from datetime import datetime

    return datetime.now().strftime("%H%M%S")
