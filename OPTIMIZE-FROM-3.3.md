# 用 3.3 优化复刻版 · 研究结论

日期：2026-10-14
对象：复刻版现状（`~/workspace/feimao-replica/`，子命令数随落地更新，见 README）
依据：《肥猫 3.3 逆向分析报告》+ 3.3 运行时脱壳镜像字符串二次挖掘
（`~/workspace/rea-work/feimao-v3.3-ghidra/dump_utf8.txt`）+ 复刻版代码逐项对照

## 结论一句话

小红书模式数量上复刻版已与 3.3 对齐；真正的优化点不在加模式，
而在 3 处：卡时长做假、魔术帧缺失、GPU 没用上。另有 1 处是
我们实测比 3.3 原版更优的做法（纯AB 帧率），应反向固化。
（进度：本清单 P0/P1 已于 2026-10-14 全部处理完毕——卡时长、
魔术帧、AB 音频、发闪标注、overlay-120 已落地；GPU 择优
经用户决定取消并回退。只剩 P2 校准与清单外项目。）

## 已吸收（不用再做）

- AB180 正品时序：`-g 250 -sc_threshold 0` + 成片后 `setts` 按包重写
  时间戳（压帧 1µs / 辅轨 8332µs / 主轨 8334µs），已写进 `ab_180`
  默认流程（`app/modes/xhs_hist.py`），与真品逐项一致。
- 短 GOP（`-g 12`）+ faststart：单帧AB/实拍AB 已按 3.3 注释实现。
- 随机帧AB 特效预渲染短循环：已实现，与 3.3 性能优化口径一致。
- grid / life 两种 3.3 新增生成背景：已实现（8 种生成背景齐全）。

## P0-1 卡时长精确版（功能缺口，价值最高）✅ 已完成（2026-10-14）

- 原现状：简化版只改 mvhd/tkhd/mdhd 三处 duration 字段，ffprobe
  按采样表口径仍读真实时长，等于没卡住。
- 已落地：`app/postprocess/mp4box.py`（独立 MP4 box 解析/改写，
  结构借鉴脱壳镜像中 `zhaocai_mp4_patch` 的全套 box 工具）——
  mvhd/tkhd/mdhd + dirty stts（Σcount 不变、Σ时长对齐目标、
  ≤250 随机块 + 单样本余数分发）全部对齐目标，stco/co64 与
  祖先 box size 同步修正；`card_duration` 已接入，旧简化逻辑删除。
- 与原计划的一处修正（实测推翻）：原拟按 cvj 注释让 elst 保持
  真实，实测 ffmpeg 系读取器以 elst 段时长为准裁剪时间线——
  elst 真实时 ffprobe 仍读真实时长且 300 帧只解出 2 帧。故默认
  改为全口径（elst 首段一并对齐），cvj 口径保留为
  `--keep-elst-real` 选项（手机正常播 vs ffmpeg 读真实，二选一，
  选用知悉）。
- 另两处实测踩坑已修：ctts 不按拉伸比缩放会出现尾帧 pts 跳变
  与解码 dts 告警；音频轨 elst media_time 被 ffmpeg 兼作
  skip_samples，缩放会跳掉 179/431 个音频帧，故音频轨不缩放。
- 验证：`tests/test_card_duration.py` 21 项断言全过——ffprobe
  format/双流时长均 =1800.000s（目标），视频 300/音频 431 帧
  与原片一致，全片解码零告警；run_all 卡时长单例重跑通过
  （随机目标实测读出 2842.8s）。全量 33 例回归在重跑途中遇
  服务重启中断（前 6 例已绿），其余模式本次未改代码路径，
  33/33 的完整记录仍以 2026-10-13 全量实跑为准（TESTING.md）。
- 未做（仍属 M3 后续）：cvj 的 mp4a sample entry、weird4/weird8。

## P0-2 魔术帧补上（脱壳新发现，此前误判“无证据”）✅ 全部完成（2026-10-14）

> 落地记录：`xhs_new.magic_frame` + CLI `magic-frame` 已实现下列
> 1–3 步与第 4 步封装。实测（红/蓝纯色夹具）：90fps、3s 片输出
> 268 帧（=270−剔头 2），逐帧分类严格呈“每 3 帧 2 主 1 辅”
> （n≥1 时 n≡1 mod 3 为辅帧，R179:B89）；真实素材 10s 冒烟与
> 无音轨素材均通过。`tests/test_magic_frame.py` 10 项断言全过。
> 第 4 步 AB 音频隐藏已随 M4 落地（见下节），魔术帧
> `--audio-mode 1-5` 可叠加（B 取辅轨音轨）；封装参数中
> -aac_ms/-aac_is 的值已按 tab_ab_audio remux 常量证据改为 0/0
> （关闭 AAC 声道联合编码以保护隐藏结构）。

静态报告 §5.1 标注魔术帧“未确认”、复刻版因此跳过。本次在脱壳
镜像字符串中恢复其完整流水线（位于 `ffmpeg_command_catcher`
模块，窗口标题即「魔术帧」，偏移约 0x0a01434f 起）：

1. 生成 a1.mp4：主视频标准化——libx264 preset fast、profile high
   level 4.2、bf 0、yuv420p、faststart，`scale + setsar=1 +
   setpts=N/FRAME_RATE/TB, fps=30`。
2. 生成 b1.mp4：辅视频 `-stream_loop -1` 循环，`scale:
   force_original_aspect_ratio=increase, crop` 填满画布、fps=30，
   preset **medium**、无音频。
3. 合成 ab.mp4：`[a] fps=90 ; [b] fps=30 ; interleave, fps=90,
   select='not(eq(n,0)+eq(n,1))'`——主轨升到 90 与辅轨 30 交织，
   剔除头 2 个交织帧，无音频。
4. 合并视频：接 AB 音频隐藏（同模块 import `tab_ab_audio` 的
   `ABParams / extract_pcm_wav / process_audio_hide`，中间产物
   ab_in.wav → ab_out.wav），最后 `-map 0:v:0 -map 1:a:0 -c:v copy
   -c:a aac -b:a {audio_bitrate} -aac_ms -aac_is -shortest` 封装。

- 即：魔术帧 = **90:30 视频交织 + AB 音频隐藏** 的组合模式。
- 动作：先做视频三段（可直接复用现有交织骨架变体表，加 90:30
  一档 + 剔 n=0,1），音频先直通；AB 音频模块（方案 M4）落地后
  再补第 4 步。
- 注意：个别数值（a1 的 crf、-g 值）在转储字符串中被切分丢失，
  首版按同模块其他模式口径取值（crf 18 / 短 GOP），待原版出片
  对照校准。

## M4 AB 音频隐藏 ✅ 已完成（2026-10-14）

- 落地：`app/media/ab_audio.py`（numpy，与原版 tab_ab_audio 同栈）
  + CLI `ab-audio`；函数骨架按脱壳字符串同名复刻
  （extract_pcm_wav / read_wav_stereo / mix_ab_audio /
  process_audio_hide / remux），模式文案与高级项（α 默认 1.0、
  全片深度=粉红噪+Haas、干扰音量默认 0、remux 192k）均有
  字符串原文依据。
- 声学结构：L=Mid+Side、R=Mid−Side（模式1/3 Mid=B、Side=A；
  2/4 反之），单声道下混严格等于 Mid；B 先经人声带通；Side
  做目标增益对齐 + Haas 延迟（只动 Side）；模式3/4 加双层
  超声载波；模式5 纯反相 L=A、R=−A；corr + side/L 双阈值
  防重复；time_mode 时间掩码（full/first_half/last_half/custom）。
- 验证：`tests/test_ab_audio.py` 13 项全过——模式1/2 的 Mid/Side
  主导频率互换（Goertzel 实测 >10 倍）、模式3 的 19kHz 载波
  可检出、模式5 封装前单声道严格为零且 corr≈−1、防重复拦截、
  魔术帧 + 模式5 集成；CLI 冒烟（模式1/5、magic --audio-mode 5）
  通过。
- 复刻取值（原版数值在转储中丢失，待原版出片校准）：带通
  300–3400Hz、载波 19000+20500Hz / amp 0.05、Haas 0.4ms、
  防重复阈值 corr<−0.5 / side/L>1.2。模式5 经 AAC 后单声道
  残余 ≈1.7%（≈−35dB）为原生 AAC 量化噪声，与原版同编码链
  （aac 192k、-aac_ms 0、-aac_is 0）同量级，非处理误差。

## P1-1 GPU 编码实测择优（速度，Windows 上收益最大）❌ 用户取消（2026-10-14）

> 取消记录：代码当晚曾落地并通过专项验证（见下），同日
> Xiao 明确不要 GPU 加速——他的电脑均为苹果机（MacBook Air /
> iMac / mac mini，Apple Silicon），没有 NVIDIA 显卡，NVENC
> 路径无处运行。代码已全部回退至纯 CPU 版，专项测试文件
> 已移入回收站（trash_id 20261014-121801-ec2919acf53e413090b7c0e063c765b9，
> 30 天内可恢复）。以下为当时的落地记录，存档备查，不再执行。

> 落地记录：`ffmpeg_resolve` 新增编码器实测择优——按
> hevc_nvenc → h264_nvenc → libx264 顺序逐个实测「能否真写出
> 有效 MP4」（编码 0.2s 测试片 + ffprobe 验流），结果进程内 +
> 落盘缓存（`.encoder_cache.json`，按 ffmpeg 路径+版本失效），
> `FEIMAO_ENCODER` 可强制指定。`common.encode_tail()` 双档：
> 交织骨架家族（pure-ab/105:35/new-120/fanzhuan/flash/ab60）
> 在 h264_nvenc cq18 与 libx264 CRF18 间切换（纯AB 的 GPU
> 口径即 h264 一档）；精确配方模式（魔术帧、AB180、单帧等）
> 的编码参数按原配方写死、不走择优。QSV/AMF 探测未接
> （VM 与目标机均为 N 卡口径，原版仅卡时长页用）。
> 验证：`tests/test_encoder_select.py` 10 项全过——本机无卡
> 时两项 NVENC 实测均为 False、双档回退 libx264、
> encode_tail 与旧 CPU 尾逐项一致、pure-ab 实跑帧数不变
> （180 帧/2s）。GPU 路径本身须在 Xiao 的 Windows 机
> （GTX 1060+）上实测后才算验收。

- 现状：`app/core/ffmpeg_resolve.py` 只按路径找 ffmpeg，
  `app/modes/common.py` 全模式统一 CPU libx264。
- 3.3 做法（报告 §6）：resolve 时实测 NVENC 能否真写出有效 MP4
  （bin3→bin 顺序试），失败回退；部分模式编码器优先级
  **hevc_nvenc → h264_nvenc → libx264**（纯AB GPU 口径 cq18）；
  卡时长自动探测 NVENC/QSV/AMF 三家。
- 动作：resolve 加一次性实测缓存；common 编码尾做成
  CPU/GPU 双档，模式按 3.3 口径选档。本机 VM 无 N 卡，
  代码必须无 GPU 时零感知回退（现状即回退态）。
- 前提：用户 Windows 机为原版推荐配置（GTX 1060+），收益在
  那边兑现；VM 上无法实测 GPU 路径。

## P1-2 纯AB 90fps 的平台闪烁（我们的实测，3.3 本身没改）✅ 已完成（2026-10-14）

> 落地记录：`pure-ab` 的 CLI 帮助与 README 条目已加发闪警告
> 并推荐默认用 `ab-180`；魔术帧（同为 90fps 输出）帮助同步
> 标注。配方本身未动。

- 3.3 纯AB 仍是 90fps；但我们已实证：90 与平台 60fps 非整数倍，
  平台抽帧相位漂移、两路乱序被抽中而发闪；180fps（3 倍）则整齐
  只抽中一路。此结论同样适用于魔术帧的 90 输出（待实测）。
- 动作（低成本）：`pure-ab` 保留（与原版对齐），但在 README/帮助
  里标注发闪风险并推荐默认用 `ab-180`；不改配方本身。

## P1-3 缺一个 120fps 隔帧叠加模式 ✅ 已完成（2026-10-14）

> 落地记录：新增 `xhs_new.overlay_120` + CLI `overlay-120`
> （第 23 个子命令）：画布横 1280×720 / 竖 720×1280，主轨
> fps=120、辅轨整帧 overlay 且 enable 只在奇数帧生效。
> 验证：`tests/test_overlay_120.py` 6 项全过——120fps、
> 帧数 240/240、逐帧奇偶符合率 240/240、横竖画布、音轨保留。

- 3.3 另有独立的 120fps 隔帧叠加（主 120fps、辅帧只在奇数帧
  overlay，横 1280×720 / 竖 720×1280，报告 §5.1 末），
  与“新120帧”（120:60 交织）不是同一个。复刻版只有后者。
- 动作：用现有 overlay 类模式（frame-gap/double-frame）骨架
  低成本补一档 `overlay-120`。其 CUDA 变体（256×256
  overlay_cuda）属小众，暂不做。

## P2 细节校准（有原版出片才能定，先挂起）

- 新120帧底图 shape.png 的确切生成方式：复刻取源片首帧，
  3.3 静态+脱壳均未展开。
- 动态A 合成方式：复刻为“背景全画布 + 主视频缩小居中叠加”，
  属复刻解释，无原文证据。
- 脱壳另见 `tab_shadowflow`（TabShadowflow）、`tab_yinghong`、
  `tab_zhaocai`（自带 mp4 patch + pipeline）三个模块名，
  不在 3.3 侧栏导航中，疑为内部/历史模块；其 zhaocai 的 box
  解析器结构可直接借鉴到 P0-1，用户面功能归属待定，不单独开发。

## 建议顺序

1. ~~P0-1 卡时长精确版~~ ✅ 2026-10-14 完成
2. ~~P0-2 魔术帧（含第 4 步）/ M4 AB 音频~~ ✅ 2026-10-14 完成
3. ~~P1-1 GPU 择优~~ ❌ 2026-10-14 用户取消（其机器均为苹果、无 N 卡），代码已回退
4. ~~P1-2 发闪标注 / P1-3 overlay-120~~ ✅ 2026-10-14 完成
5. 剩余：P2 校准（等原版对照出片）、M3 尾巴（cvj mp4a sample
   entry、weird4/weird8 指纹后处理）、快手/B站/黑五其余模式、GUI
