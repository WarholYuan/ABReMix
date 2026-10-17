# 肥猫复刻版（小红书部分 · 命令行内核）

按《肥猫复刻方案》（`~/workspace/rea-work/feimao-replica-plan.md`）开发的第一轮成果：
方案 §1 架构中的**引擎层 + 素材层 + 小红书全部模式**，以命令行形式交付；
参数依据《肥猫 3.3 逆向分析报告》与《肥猫 V2.7 逆向分析》的原文模板与模式规格。
实跑验证结果见 [TESTING.md](TESTING.md)。

## 环境与依赖

- Python ≥ 3.10，以**标准库**为主；唯一例外：AB 音频模块用 numpy（见 `requirements.txt`）。
- ffmpeg / ffprobe：定位规则在 `app/core/ffmpeg_resolve.py`——
  环境变量 `FEIMAO_FFMPEG`/`FEIMAO_FFPROBE` → 项目 `tools/` 目录 → 系统 PATH。
- 本轮开发/测试机实测：系统自带 **ffmpeg 8.1.2**（Ubuntu 包），含 libx264 与
  全部所需滤镜（life/zoompan/scale2ref/interleave/blend/geq/cellauto…），
  直接走 PATH，未另装静态构建。
- GPU：原版纯AB 等模式有 NVENC 路径；本机无 N 卡，复刻走 CPU 路径
  （libx264 CRF18，与原版 CPU 口径一致）。

## 项目结构

```
feimao-replica/
├─ cli.py                  # 命令行入口（子命令见下）
├─ app/
│  ├─ core/
│  │  ├─ templates.py      # 模板引擎 + 冻结模板库（BL 四步原文、105:35、grid、life 原文滤镜串、交织变体参数表）
│  │  ├─ runner.py         # 执行器：subprocess + 进度解析 + 失败带命令与 stderr 摘要
│  │  ├─ probe.py          # ffprobe 封装：时长/宽高/fps
│  │  └─ ffmpeg_resolve.py # ffmpeg/ffprobe 定位
│  ├─ media/
│  │  ├─ background_gen.py # 生成背景：grid / life / 噪声 / 棋盘 / 波纹 / 点阵 / 纯色 / 乱色块
│  │  ├─ frame_seq.py      # 帧序列通道：抽帧 → 交错编排（move 语义）→ 回拼
│  │  └─ ab_audio.py       # AB 音频隐藏：声道隐藏 5 模式(mid/side)+ 超声波 + corr 防重复
│  ├─ postprocess/
│  │  └─ mp4box.py         # MP4 box 解析/改写：卡时长精确版（dirty stts + 全口径时长对齐）
│  └─ modes/
│     ├─ common.py         # 交织骨架运行器与统一编码参数
│     ├─ bl.py             # BL-1 / BL-2（方案 §4 冻结流水线，作骨架样例）
│     ├─ xhs_new.py        # 小红书·新区模式
│     └─ xhs_hist.py       # 小红书·历史区模式
└─ tests/
   ├─ samples/             # 测试素材（自动生成：testsrc2/testsrc + 正弦音轨）
   ├─ outputs/             # 实跑产物（含样例出片 pure_ab.mp4）
   └─ run_all.py           # 全模式实跑验证脚本（生成 TESTING.md）
```

每个模式函数的头部注释都标注了参数来源（报告章节/偏移）与复刻组装说明。

## 用法

通用：`python3 cli.py <子命令> -i 主视频 -a 辅轨 -o 输出 [--seed N]`

| 子命令 | 模式 | 示例 |
|---|---|---|
| `pure-ab` | 新 纯AB（90fps 隔帧交织）⚠️ **发闪风险**：90fps 不是平台 60fps 的整数倍，平台抽帧相位漂移、两路会被乱序抽中而闪（2026-10-14 实测）；正式发布推荐 `ab-180`（180fps = 60×3，整齐只抽中一路）。本模式保留仅为与原版对齐 | `python3 cli.py pure-ab -i main.mp4 -a aux.mp4 -o out.mp4` |
| `interleave-105-35` | 105:35 交织变体（原文滤镜） | `python3 cli.py interleave-105-35 -i main.mp4 -a aux.mp4 -o out.mp4` |
| `ab-180` | 新 180小（180fps；已按原版真品逐帧校准：前 4 帧纯主轨 + 每 3 帧插 1 帧辅轨，辅轨可缺省用 `--aux-gen circles` 自动生成同心圆图案——真品辅轨即此图案） | `python3 cli.py ab-180 -i main.mp4 -a aux.mp4 -o out.mp4` 或 `python3 cli.py ab-180 -i main.mp4 --aux-gen circles -o out.mp4` |
| `fanzhuan-ab` | 新 反转AB（60+60→120fps） | `python3 cli.py fanzhuan-ab -i main.mp4 -a aux.mp4 -o out.mp4` |
| `new-120` | 新120帧（底图+120:60 交错） | `python3 cli.py new-120 -i main.mp4 -o out.mp4 --seed 42` |
| `xin-fanzhuan` | 新反转（帧序列逐帧交替） | `python3 cli.py xin-fanzhuan -i main.mp4 -a aux.mp4 -o out.mp4` |
| `one-b-three-a` | 1B3A（帧序列 1 主 3 辅） | `python3 cli.py one-b-three-a -i main.mp4 -a aux.mp4 -o out.mp4` |
| `frame-gap` | 新 帧间隔（周期叠帧+特效） | `python3 cli.py frame-gap -i main.mp4 -a aux.mp4 -o out.mp4 --period 3 --count 2 --effect edge` |
| `random-frame-ab` | 新 随机帧AB | `python3 cli.py random-frame-ab -i main.mp4 -a aux.mp4 -o out.mp4 --method single --seed 42`（`--method periodic --effect blur` 为半透明周期叠加，特效先烤进短循环片段） |
| `single-frame-ab` | 新9-08 单帧AB | `python3 cli.py single-frame-ab -i main.mp4 -a aux.mp4 -o out.mp4 [--swap-orient]` |
| `realshot-ab` | 新 实拍AB | `python3 cli.py realshot-ab -i main.mp4 -a aux.mp4 -o out.mp4 [--duration-from-aux]` |
| `double-frame` | 新9-08 双帧 | `python3 cli.py double-frame -i main.mp4 -a aux.mp4 -o out.mp4 --opacity 0.5` |
| `flash-frame` | 新 闪帧（辅轨首帧交替 120fps） | `python3 cli.py flash-frame -i main.mp4 -a aux.mp4 -o out.mp4`（省略 `-a` 则自动生成随机图案首帧） |
| `magic-frame` | 新 魔术帧（90:30 交织） | `python3 cli.py magic-frame -i main.mp4 -a aux.mp4 -o out.mp4`（`--audio-mode 1-5` 叠 AB 音频隐藏，B 取辅轨音轨） |
| `ab-audio` | 黑五 AB 音频（声道隐藏 5 模式） | `python3 cli.py ab-audio -i main.mp4 -a speech.mp4 --mode 1 -o out.mp4`（模式5 纯反相不用 `-a`） |
| `overlay-120` | 新 120fps 隔帧叠加（奇数帧整帧叠辅轨） | `python3 cli.py overlay-120 -i main.mp4 -a aux.mp4 -o out.mp4` |
| `three-frame-bg` | 三帧叠背景 | `python3 cli.py three-frame-bg -i main.mp4 -a bg.mp4 -o out.mp4`（成片文件名自动带 `_三帧` 规则后落到 `-o`） |
| `batch-908` | 新9-08 AB批量 | `python3 cli.py batch-908 --input-dir ./in --aux-dir ./aux --out-dir ./out`（或 `--auto-aux blocks` 自动生成辅轨） |
| `dongtai-a` | 新9-08 动态A | `python3 cli.py dongtai-a -i main.mp4 -o out.mp4 --bg-gen grid --seed 42`（或 `--bg bg.mp4`） |
| `life-blend` | life 复合（原文滤镜） | `python3 cli.py life-blend -i main.mp4 -o out.mp4 --seed 42` |
| `card-duration` | 新 卡时长（精确版） | `python3 cli.py card-duration -i main.mp4 -o out.mp4 --seed 42`（`--keep-elst-real` 为 cvj 兼容口径） |
| `gen-bg` | 单独生成背景视频 | `python3 cli.py gen-bg --kind grid -o bg.mp4 --width 1280 --height 720 --duration 5 --seed 42`（kind: grid/life/circles/noise/checker/wave/dots/color/blocks） |
| `bl-1` / `bl-2` | BL 冻结流水线（样例） | `python3 cli.py bl-1 -i main.mp4 -a bg.mp4 --out-dir ./out` |
| `heiwu-batch` | 黑五·批处理（三模式，参数冻结） | `python3 cli.py heiwu-batch -i main.mp4 -o out.mp4 --mode 1`（模式一 720x1280 pad+scale；模式二 720x1280+CRF17+baseline；模式三 496x864+ultrafast+6000k+AAC 码率 195247 怪值照抄） |
| `yuan-data` | 黑五·元数据（SKT4 式伪装时长） | `python3 cli.py yuan-data -i main.mp4 --out-dir ./out --duration 03:18:00` |
| `yuan-data2` | 黑五·元数据2（四档命令+后置校验） | `python3 cli.py yuan-data2 -i main.mp4 --out-dir ./out --mode 1 --duration 02:00:00`（命名 `{stem}_元数据2_{n}.mp4`，含解3秒+关键帧校验） |
| `short-duration` | 伪短时长（长片显示成短时长） | `python3 cli.py short-duration -i main.mp4 -o out.mp4 --display-seconds 5` |
| `fingerprint` | 指纹后处理 cvj/weird4/weird8（原地改写，先备份） | `python3 cli.py fingerprint -i out.mp4 --mode cvj --target-seconds 1800 --seed 42`；`--mode weird8 --sar 2671:51675` |

特效预设（`--effect`）：`none / rotate（旋转）/ color（变色）/ mirror（镜像）/ blur（模糊）/ blink（闪烁）/ edge（边缘检测）`，`frame-gap` 另支持 `--custom-filter` 自定义滤镜串。

## 跑测试

```bash
python3 tests/run_all.py           # 全量实跑 + 生成 TESTING.md
python3 tests/run_all.py --only 纯AB  # 只跑名字含关键字的用例
```

## 与原版的已知差异

1. **卡时长已升级精确版**（2026-10-14）：阶段二走 `app/postprocess/mp4box.py`——
   mvhd/tkhd/mdhd + dirty stts（Σcount 不变、Σ时长对齐目标、随机块长打散）
   全部对齐目标值（随机 25–60 分钟），stco/co64 偏移与祖先 box 大小同步修正。
   elst 默认全口径一并对齐：实测 ffmpeg 系读取器以 elst 段时长为准裁剪时间线，
   不对齐时 ffprobe 仍读真实时长且只解出头几帧；对齐后 ffprobe 读目标时长、
   全帧完整可解。`--keep-elst-real` 可切换原版 cvj 口径分裂（elst 保持真实、
   手机正常播，但 ffmpeg 系读真实时长）。未做：cvj 的 mp4a sample entry 处理、
   weird4/weird8 指纹后处理（仍属方案 M3 后续）。
2. **魔术帧已补**（2026-10-14）：静态报告曾标“未确认”，后在 3.3 脱壳镜像中
   恢复全流程并实现视频三段（a1 标准化 / b1 填满循环 / 90:30 交织剔头 2 帧，
   实测规律为每 3 帧 2 主 1 辅、90fps）；a1 的 crf/-g 原值在转储中丢失，
   取 crf 18 / 短 GOP 12。**第 4 步 AB 音频隐藏（M4）已落地**：`ab-audio`
   子命令与魔术帧 `--audio-mode` 共用 `app/media/ab_audio.py`——声道隐藏
   5 模式（mid/side 公式、模式5 纯反相）、超声波双层载波、全片深度
   （粉红噪 + Haas，只作用于 Side）、corr 防重复、时间掩码；专项验证
   `tests/test_ab_audio.py` 13 项全过。复刻取值（原版数值在转储中丢失）：
   B 人声带通 300–3400Hz、载波 19000+20500Hz / amp 0.05、Haas 0.4ms、
   防重复阈值；模式5 封装前单声道严格为零，AAC 后残余 ≈-35dB 为原生
   AAC 量化噪声（与原版同编码链、同量级）。
3. **新120帧的底图 shape.png**：流水线五段结构按报告实现，底图取源片首帧；
   其确切生成方式未在逆向字符串中展开。
4. **动态A 的合成方式**：字符串证据只有「动态背景 A 轨」一行，复刻组装为
   “动态背景全画布 + 主视频等比缩小居中叠加”，属复刻解释。
5. **双帧**：报告注「细节从略」，按双轨逐帧混合框架实现。
6. **grid 背景基底图**：原文滤镜只给出对 `[0:v]` 的处理链，基底未明示；
   复刻用棋盘格 PNG 作输入。抖动参数的分布区间为复刻设定（原版只说
   “在抓取附近随机抖动”），seed 可复现。
7. **life 复合的 `{orient}` 与 `{lw}/{lh}`**：原文为占位符，复刻中 orient 置空、
   细胞尺寸取画幅 1/2；blend 系数默认 a=0.7/b=0.3（可调）。
8. **编码路径**：统一 CPU libx264；CRF/码率口径与报告一致。
   GPU 择优（NVENC 实测双档）曾于 2026-10-14 按 P1-1 加入，
   同日按用户决定取消——其电脑均为苹果机（Apple Silicon），
   没有 NVIDIA 显卡，NVENC 无处运行；代码已回退至纯 CPU 版。
9. **输出时间戳处理**：交织骨架在 ffmpeg 8 下需 `-fps_mode passthrough` 才能
   全量保留交织帧（默认 CFR 会按图的帧率元数据丢帧，实测 900→835），
   已在骨架运行器内置；BL 家族的 `-vsync vfr` 属冻结模板，未改。
10. **横竖屏互换（单帧AB）**：实现为输出画布宽高对调 + 等比缩放补边，
    原版“格式工厂式互换”的确切算法未恢复。
11. 未做（不在本轮范围）：GUI、快手分区模式（用户 2026-10-16 决定
    其他平台与 GUI 均不做）、去水印下载（方案明确剔除）。
12. **黑五专区 + M3 尾巴已落地**（2026-10-17）：
    - 黑五·批处理三模式（`heiwu-batch`，参数冻结）。两处实测修正：
      模式一的 pad 前置等比缩小（pad 不接受比输入小的画布，顺序不动）；
      模式三「AAC 195247」方案曾注采样率，实测 16.16 定点采样率字段
      装不下 >65535 且标准 aac 编码器拒绝——码率 195247bps 是唯一
      自洽解读，按 `-b:a` 冻结。
    - 元数据/元数据2（`yuan-data`/`yuan-data2`）：四档命令按 3.3 脱壳
      镜像 tab_skt4 抓包字符串逐字录入（CPU·模式一 High@4.1 1000k
      Wxmm / CPU·模式二 Main@3.1 2000k；GPU 两档仅存档，机器无 N 卡）；
      `-tag:v avc1`、时基 15360、bt709 三件套、`encoding_tool=
      Wxmm_9020230808`、`-brand isom`；SKT4 式时长伪装
      （mvhd ts=1000 / mdhd 保留时基 / tkhd 用同轨 mdhd 时基）走
      `mp4box.disguise_duration_skt4`，默认对齐 elst（原版不动 elst，
      但 ffmpeg 系读取器按真实媒体长度钳制 elst 后仍读真实时长；
      显示口径面向读 mvhd 的播放器，验收走 box 级）。元数据2 带后置
      校验（解3秒+关键帧）。SKT4_VF 滤镜串在脱壳字符串中未恢复，留空。
    - 伪短时长（`short-duration`）：mp4box 字节级等效实现（本机无
      Bento4），失败回退 ffmpeg 直出（原版同款回退语义）。
    - 指纹三模式（`fingerprint`，`app/postprocess/fingerprint.py`）：
      cvj = 视频时基锁 12000 + dirty stts + elst 口径分裂 + esds
      153028 + compressor「JVT-AVC Coding」+ 剥离 udta/ilst
      （常量均出自脱壳镜像；原版的 hdlr 改名/track_id/音频 mdat
      标记等细节未恢复，不发明未知值）；
      weird4 = 视频轨保 delta 只放大 count（幽灵样本）+ 音频 dirty
      stts + esds 200112 + udta 相机指纹块（assets/weird4_udta.hex
      优先，内嵌默认模板为复刻自备值，不照抄原版坐标/机型）；
      weird8 = avcC SPS 锁伪造 SAR 2671:51675（Exp-Golomb 改写，
      avcC 变长重建；解码器口径实测 SAR 生效，ffprobe codecpar 仍
      显示容器层 1:1 属正常）+ compressor + 文件尾 free box。
    - 专项验证 `tests/test_heiwu.py` 29 项断言全过；卡时长/AB 音频/
      魔术帧/overlay-120 四套既有回归同轮全绿。
