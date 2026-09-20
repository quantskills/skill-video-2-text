# 输入格式指南

本 skill 的输入是**调用方提供的本地音视频文件**，不接任何行情/业务数据源。这份文档说明支持什么、什么样的输入能得到好结果、以及常见陷阱。

## 支持的格式

以扩展名做第一道闸门（`validate_input()`），实际解码能力由 PyAV / ffmpeg 决定。

### 音频

| 扩展名 | 说明 |
|---|---|
| `.mp3` | 最常见，有损压缩，质量足够 |
| `.wav` | 无损，识别效果最好；文件大 |
| `.m4a` | AAC 容器，手机录音、语音备忘录默认格式 |
| `.flac` | 无损压缩，体积约为 wav 的 60% |
| `.ogg` | Opus/Vorbis，部分录音笔与 IM 使用 |
| `.aac` | 裸 AAC 流 |
| `.wma` | Windows Media Audio |
| `.opus` | 低码率语音编码，IM 语音消息常见 |

### 视频

| 扩展名 | 说明 |
|---|---|
| `.mp4` | 最常见（H.264/H.265 + AAC） |
| `.mov` | Apple QuickTime，iPhone/Mac 录屏与拍摄 |
| `.mkv` | Matroska，多音轨容器 |
| `.avi` | 老式容器 |
| `.webm` | 网页录制，常见于在线会议录屏 |
| `.flv` | Flash Video，老直播录像 |
| `.wmv` | Windows Media Video |
| `.m4v` | Apple 视频，实质是 mp4 |

**视频会自动提取音轨**，不需要用户先用 ffmpeg 转成音频。

### 不支持

其他扩展名一律返回 `unsupported_format`，`error.hint` 里会列出完整支持清单。常见被拒的：`.ts`（MPEG 传输流，扩展名与 TypeScript 冲突，刻意不收）、`.3gp`、`.amr`、`.pcm`（无头裸流，缺少采样率信息）。这些请先转码：

```bash
ffmpeg -i input.amr -ar 16000 -ac 1 output.wav
```

扩展名列表可在 `config/transcribe_config.yaml` 的 `input.audio_extensions` / `input.video_extensions` 里改。

## 输入约束

| 约束 | 默认值 | 违反时的错误码 |
|---|---|---|
| 路径必须存在 | — | `input_not_found` |
| 必须是普通文件（不能是目录/设备节点） | — | `input_not_a_file` |
| 必须可读 | — | `input_unreadable` |
| 必须非空 | > 0 字节 | `input_empty` |
| 体积上限 | 4096 MB | `input_too_large` |

体积上限是防止误传（比如把整个磁盘镜像或一个目录压缩包丢进来）。可调 `input.max_size_mb`。

## 什么样的输入能得到好结果

Whisper 内部把音频重采样到 **16kHz 单声道**，所以高于 16kHz 的采样率不会带来任何准确率提升，只会让文件更大、解码更慢。

### 推荐

- **采样率 16kHz**，单声道。这是最经济的组合。
- **近场拾音**：嘴到麦克风 30cm 以内。距离每翻一倍，准确率下降都很明显。
- **单人或轮流说话**，避免长时间重叠。本 skill **没有说话人分离**，多人同时说话只会得到一份糊在一起的稿子。
- **码率 ≥ 64kbps**（语音）。低于 32kbps 的强压缩会丢掉辅音细节。
- **安静环境**。空调声、键盘声、马路噪声都会显著拉低准确率。

### 会明显变差的情况

| 情况 | 后果 | 缓解 |
|---|---|---|
| 强口音 / 方言 | 错字率上升，专有名词几乎必错 | 换更大的模型；用 `--initial-prompt` 给领域词 |
| 背景音乐 | 歌词会被当成语音转录进来 | `vad_filter` 能挡一部分，挡不住就换模型 |
| 多人重叠 | 段落错乱、内容丢失 | 无解，只能重录或人工核对 |
| 远场（会议室吊顶麦） | 大量漏字 | 靠近麦克风重录 |
| 电话录音（8kHz） | 高频信息已丢失，上限受限 | 换 `small` 以上模型略有帮助 |
| 超长录音（数小时） | 内存吃紧、耗时长 | v0.1 不分片，建议先切分 |
| 静音开头很长 | VAD 会跳过，但解码仍要扫 | 可接受 |

## 时长与耗时预期

CPU + int8 + base 模型下，粗略经验值是**转录耗时约为音频时长的 0.2~0.5 倍**（即 10 分钟音频约 2~5 分钟）。影响因素：

- 模型规格：`tiny` ≈ base 的一半耗时，`small` ≈ 2 倍，`medium` ≈ 5 倍，`large-v3` ≈ 10 倍以上
- `beam_size`：默认 5，调到 1 可显著提速，代价是准确率略降
- `vad_filter`：开启会先跑一遍 VAD，但能跳过静音段，长录音净收益通常为正
- 是否有 GPU：`config` 里 `model.device` 设 `auto` 会自动探测 CUDA；未做 MPS（Apple Silicon GPU）验证

## 常见陷阱

**路径含空格或中文没加引号** —— shell 会把它拆成多个参数，表现为 `input_not_found`。始终加引号：

```bash
python3 scripts/transcribe.py "~/Downloads/2026 会议 录音.m4a"
```

**`~` 没被展开** —— 引号会阻止 shell 展开 `~`。`validate_input()` 内部调了 `expanduser()`，所以 `"~/x.mp3"` 能用；但更稳妥的写法是让 shell 展开：`~/Downloads/x.mp3`（不加引号）或 `"$HOME/Downloads/x.mp3"`。

**把目录当文件传** —— 返回 `input_not_a_file`，不是 `input_not_found`。这两个码分开是为了让调用方能给出不同提示。

**扩展名与实际编码不符** —— 比如把 `.amr` 改名成 `.mp3`。扩展名闸门会放行，但解码会失败并返回 `decode_failed`。**不要靠改扩展名绕过格式检查**，请用 ffmpeg 真转码。

**从微信/钉钉导出的语音消息** —— 可能是 `.silk` 或 `.amr`，都不在支持列表里。需要先转码。

**视频只有画面没有音轨** —— 解码不会报错，但 `segments` 会是空数组，`validate.py` 判 FAIL（`segments 为空 —— 没有识别到任何语音`）。这正是第 4 步校验存在的意义：不校验的话，你会拿一份空 JSON 去写摘要。

## 隐私

音频**不会离开本机** —— 没有上传、没有云端 API。但转录产物（`output/` 下的 JSON / TXT / timeline / 最终的 Markdown）是**明文文本**，敏感度不低于原始录音，且更容易被搜索、被复制、被误提交。

`.gitignore` 已排除 `output/` 与 `*.transcript.*` / `*.timeline.txt`。仍然建议：

- 处理问诊、法律、财务、人事类录音时，把 `--output-dir` 指到加密卷
- 转录完成后按需删除中间产物
- 录制他人语音前确认已获得合法授权（多地法律要求告知同意）
