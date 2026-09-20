# Source Boundary

本 skill 有两类外部来源需要划边界：**模型权重**从哪来，以及**输入音频**从哪来。

## 一、模型来源

### 允许的模型来源

按 `model_utils.ensure_model()` 的尝试顺序：

| 优先级 | 来源 | 落点 | 说明 |
|---|---|---|---|
| 0 | `--model-path` / `FASTER_WHISPER_MODEL_DIR` | 调用方指定 | 显式指定则**完全不下载**，目录不完整直接失败退出 |
| 1 | 本地缓存 | `<cache_root>/<size>/` 或 `<cache_root>/_ms_cache/models/<owner>--<repo>/snapshots/<revision>/` | 两种落点都扫，零网络请求 |
| 2 | ModelScope | `_ms_cache/models/...` | 国内首选，免代理。命名空间依次试 `pengzhendong` → `keepitsimple` → `Systran` |
| 3 | hf-mirror.com | `<cache_root>/<size>/` | Hugging Face 国内镜像站 |
| 4 | Hugging Face 官方 | `<cache_root>/<size>/` | 最后兜底，国内经常超时 |

缓存根目录默认 `~/.cache/faster-whisper-models`，可用 `FASTER_WHISPER_CACHE_ROOT` 迁移。

**唯一的模型仓库**：`Systran/faster-whisper-base`（及其 `<size>` 变体）。这是 faster-whisper 官方发布的 CTranslate2 转换权重，不是 OpenAI 的原始 PyTorch checkpoint —— 两者格式不通用。

### 模型完整性判定

一个目录被认定为"可用模型"，必须同时满足（`model_utils.missing_model_files()`）：

- 必需：`model.bin`、`config.json`、`tokenizer.json`
- 词表：`vocabulary.txt` **或** `vocabulary.json`（二者有其一即可 —— base/small/medium 用 `.txt`，large-v3 用 `.json`）

可选：`preprocessor_config.json`。faster-whisper 自带 log-mel 特征提取器，缺它也能跑。

**这个清单是单一同源的**：手动下载指引（`manual_download_hint()`）直接读这几个常量生成，所以文档承诺的文件与代码实际校验的文件永远一致。v0.1 曾把 `vocabulary.json` 和 `preprocessor_config.json` 列为必需，但 ModelScope 上的 base 仓库实际提供的是 `vocabulary.txt` 且没有 `preprocessor_config.json` —— 用户照指引下载必然失败。回归测试：`tests/test_model_discovery.py::TestManualDownloadHint::test_hint_matches_actually_downloaded_files`。

### 禁止的模型来源

以下不作为生产依据：

- 来源不明的第三方"优化版"/"量化版"权重 —— 无法验证是否被篡改，也无法复现准确率
- 从聊天工具、网盘、论坛二手转发的模型文件
- 未标注转换来源的 CTranslate2 模型（无法确认对应哪个 Whisper 版本）
- OpenAI 原始 PyTorch checkpoint（`.pt`）—— faster-whisper 加载不了，会报 `model_load_failed`

**校验和**：v0.1 不做模型文件哈希校验。这是一处已知的信任缺口 —— 下载通道的可信度完全依赖 ModelScope / hf-mirror / Hugging Face 三个站点本身。对模型来源有强合规要求的场景，请自行下载后核对哈希，再用 `--model-path` 指向该目录。

## 二、输入来源

### 允许

**调用方提供的本地音视频文件**，且调用方对其拥有合法处理权限。这是唯一的输入来源。

### 禁止

- 从网络 URL 直接转录（v0.1 不做下载，`input_not_a_file` / `input_not_found` 会拦住）
- 未获授权录制的他人语音 —— 多地法律对录音有告知同意要求
- 通过改扩展名伪装成支持格式的文件（如把 `.amr` 改名 `.mp3`）—— 扩展名闸门会放行，但解码会失败；请真转码

### 不接的数据源

本 skill **不使用 PandaData**，也不使用任何行情、基本面、新闻数据源。它是一个通用语音工具，与量化数据链路无交集。这一点与同仓库其他 skill（如 `skill-dl-gnn-stock-graph`）不同，是刻意的。

## 三、离线可复现性

### 完全离线的部分

一旦模型在本地缓存，**转录过程零网络请求**。`WhisperModel(..., local_files_only=True)` 是刻意设置的，它禁止 faster-whisper 在加载时隐式联网。

复现一次转录需要固定：

- 输入文件的字节内容
- 模型规格与权重版本
- `config/transcribe_config.yaml` 的版本（尤其 `beam_size`、`temperature`、`vad_filter`、`vad_min_silence_duration_ms`）
- `compute_type` 与 `device`
- 完整的 CLI 调用命令

### 不能保证比特级一致的部分

即使上述全部固定，仍**不承诺跨硬件比特级一致**，原因：

- **浮点非确定性**：ctranslate2 的 int8 量化与 CPU 指令集（AVX2/AVX-512/NEON）相关，不同硬件的舍入路径可能不同
- **`compute_type` 差异**：`int8` / `int8_float16` / `float32` 的输出本来就不完全相同
- **线程调度**：多线程推理下分段边界可能有毫秒级抖动

能保证的是：**同一台机器、同一份配置、`temperature=0.0`（贪心解码）下，两次运行的分段文本一致**。`temperature` 默认 0 正是为此 —— 一旦引入采样，复现性就彻底没了。

### 模型权重的版本问题

`Systran/faster-whisper-base` 仓库本身可能被上游更新。v0.1 不固定 revision（ModelScope 走默认分支，hf 走 `main`），因此**不同时间下载的模型理论上可能不同**。对复现性有强要求的场景，请下载后自行归档，并用 `--model-path` 指向归档副本。

## 四、产物与隐私

### 产物清单

| 文件 | 内容 | 敏感度 |
|---|---|---|
| `output/<名>.transcript.json` | 完整结果对象（含全部文本 + 时间戳） | 高 |
| `output/<名>.transcript.txt` | 纯文本稿 | 高 |
| `output/<名>.timeline.txt` | `[mm:ss - mm:ss] 文本` | 高 |
| 最终 `<名>.transcript.md` | 大模型后处理产物 | 高 |

### 已做的保护

`.gitignore` 排除了 `output/`、`*.transcript.json`、`*.transcript.txt`、`*.timeline.txt`、`*.transcript.md`、`models/`、`*.bin`。

### 仍需调用方自己做的

- **转录稿是明文文本**，敏感度不低于原始录音，而且更容易被搜索、被复制、被误提交。处理问诊、法律、财务、人事类录音时，把 `--output-dir` 指到加密卷
- **不要把转录稿提交到仓库或上传到第三方服务**（包括粘贴进在线 LLM —— 那等于把本地转录的隐私优势全部放弃）
- 按需删除中间产物
- 录制他人语音前确认已获得合法授权

### 凭据

**本 skill 不读取任何凭据环境变量。** 没有 `*_USERNAME`、没有 `*_TOKEN`、没有 `*_API_KEY`。五平台安装指南里的 `env` 块都是空的。这是它相对同仓库其他 skill 的一个显著区别：装完就能用，不需要任何账号。

## 五、网络访问清单

明确列出本 skill 会发起的所有网络请求，便于合规审查：

| 时机 | 目标 | 用途 | 可否避免 |
|---|---|---|---|
| 首次运行且本地无模型 | `modelscope.cn` | 下载模型权重 | 可：预先用 `--model-path` 指定 |
| ModelScope 失败时 | `hf-mirror.com` | 下载模型权重 | 可：同上 |
| 前两者都失败时 | `huggingface.co` | 下载模型权重 | 可：同上 |
| **转录过程中** | **无** | — | — |
| **后处理过程中** | **无**（由对话中的大模型完成） | — | — |

音频文件**永不上传**。
