# 依赖接口清单

本 skill **不接任何行情/业务数据源**（不用 PandaData），输入是调用方提供的本地音视频文件。这里列的是它实际调用的第三方库接口，以及自己对外暴露的接口。

改任何一行调用前，先看这份清单确认影响面。

## 一、对外暴露的接口（本 skill 的契约）

### `scripts/transcribe.py`

| 接口 | 签名 | 用途 |
|---|---|---|
| `run` | `run(input_path, language=None, model_size=None, model_path=None, initial_prompt=None, output_dir=None, config=None, write_files=True, **kwargs) -> dict` | **标准 agent 入口**。转录 + 落盘，返回完整结果 dict |
| `validate_input` | `validate_input(input_path, cfg=None) -> dict` | 输入预检，返回 `{path, name, ext, kind, size_bytes}`；不通过抛 `InputValidationError` |
| `transcribe` | `transcribe(input_path, language=None, model_size=None, model_path=None, initial_prompt=None, cfg=None) -> dict` | 核心转录，**静默不落盘**，供 `run()` 组合 |
| `InputValidationError` | `InputValidationError(code, message, hint="")` | 结构化异常，`.code` 供机器分支 |
| `write_outputs` | `write_outputs(result, output_dir, cfg) -> dict[str, str]` | 落盘三件套，返回实际路径 |
| `render_plain_text` | `render_plain_text(result) -> str` | 拼接纯文本稿 |
| `render_timeline` | `render_timeline(result) -> str` | 渲染 `[mm:ss - mm:ss] 文本` |
| `classify_extension` | `classify_extension(ext, cfg) -> "audio" \| "video" \| None` | 纯函数，扩展名分类 |

### `scripts/model_utils.py`

| 接口 | 签名 | 用途 |
|---|---|---|
| `ensure_model` | `ensure_model(model_size="base", model_path=None) -> str` | 定位或下载模型，返回本地目录；四级兜底全失败则 `exit(1)` |
| `find_local_model` | `find_local_model(model_size="base") -> str \| None` | 只查本地，不触网 |
| `local_model_candidates` | `local_model_candidates(model_size="base") -> list[Path]` | 按优先级列出待扫描目录 |
| `modelscope_snapshot_dirs` | `modelscope_snapshot_dirs() -> list[Path]` | 列出 ModelScope 缓存里的 snapshot 目录 |
| `missing_model_files` | `missing_model_files(path) -> list[str]` | 返回缺失文件清单，空列表 = 完整 |
| `manual_download_hint` | `manual_download_hint(model_size) -> str` | 手动下载指引文本 |

### `scripts/validate.py` / `scripts/env_check.py` / `scripts/config.py`

| 接口 | 用途 |
|---|---|
| `validate.validate_transcript(payload, strict=False) -> dict` | 校验结果 dict |
| `validate.validate_file(path, strict=False) -> dict` | 校验 JSON 文件 |
| `env_check.self_check(config_path=None, model_size=None) -> dict` | 全量自检报告 |
| `config.load_config(config_path=None) -> dict` | 加载配置（四级降级） |
| `config.config_source(config_path=None) -> str` | 配置实际来源，供自检展示 |

## 二、调用的第三方接口

### faster-whisper（硬依赖，核心引擎）

| 接口 | 调用位置 | 用到的字段/参数 |
|---|---|---|
| `WhisperModel(model_path, device=, compute_type=, local_files_only=True)` | `transcribe._load_whisper_model` | `local_files_only=True` 是**刻意的**：模型必须已在本地，禁止在转录时隐式联网 |
| `model.transcribe(path, **kwargs) -> (segments_iter, info)` | `transcribe.transcribe` | 见下表 |
| `segment.start` / `segment.end` / `segment.text` | `transcribe.transcribe` | float / float / str |
| `info.language` | `transcribe.transcribe` | str，BCP-47 |
| `info.language_probability` | `transcribe.transcribe` | float ∈ [0,1] |
| `info.duration` | `transcribe.transcribe` | float，秒 |

`transcribe()` 的 kwargs（全部由 `config/transcribe_config.yaml` 驱动，见 `_build_transcribe_kwargs`）：

| 参数 | 默认 | 为什么是这个值 |
|---|---|---|
| `beam_size` | 5 | 束搜索宽度；调 1 可显著提速，准确率略降 |
| `best_of` | 5 | 采样候选数；`temperature=0` 时基本不生效，保留以备调温 |
| `temperature` | 0.0 | **贪心解码**。转录要可复现，不要创造性 |
| `condition_on_previous_text` | False | **关闭**。开启会让长音频的重复幻觉明显增多 |
| `vad_filter` | True | 过滤静音与背景噪声段 |
| `vad_parameters` | `{"min_silence_duration_ms": 2000}` | 仅在 `vad_filter=True` 时传，否则 faster-whisper 会报参数冲突 |
| `language` | None | None = 自动检测；显式传能提升准确率和速度 |
| `initial_prompt` | None | 领域提示词，改善专有名词识别 |

**未使用**的 faster-whisper 能力（v0.1 刻意不接）：`word_timestamps`（词级时间戳）、`hotwords`（热词表，1.1+ 才有）、`translate`（翻译成英文）、多模型并行。见 SKILL.md「已知局限」。

### modelscope（软依赖，下载通道 2）

| 接口 | 调用位置 | 说明 |
|---|---|---|
| `snapshot_download(repo_id, cache_dir=)` | `model_utils._try_modelscope` | 返回 snapshot 目录路径 |

按顺序尝试三个命名空间（`MODELSCOPE_NAMESPACE_CANDIDATES`）：`pengzhendong` → `keepitsimple` → `Systran`。因为 ModelScope 上的镜像仓库由不同上传者维护，命名空间不统一。

**落点结构**（这是 v0.1 一个严重缺陷的根源，务必记住）：

```
<cache_dir>/models/<owner>--faster-whisper-<size>/snapshots/<revision>/
```

与 hf 系的 `local_dir=` 直接落在 `<cache_root>/<size>/` **完全不同**。`find_local_model()` 必须两处都扫，否则模型明明在本地却每次重新下载。

### huggingface_hub（软依赖，下载通道 3 和 4）

| 接口 | 调用位置 | 说明 |
|---|---|---|
| `snapshot_download(repo_id, local_dir=)` | `model_utils._try_hf` | hf-mirror 与官方共用，只差 endpoint |

**`HF_ENDPOINT` 的陷阱**：这个环境变量在 `huggingface_hub` **import 时**就被固化成模块级常量 `constants.ENDPOINT`，并被 `hf_api` 等子模块 `from ... import ENDPOINT` 各自复制一份。所以：

```python
from huggingface_hub import snapshot_download   # ← 常量在这一步就定死了
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"   # ← 太晚了，无效
```

而 modelscope 往往会先把 huggingface_hub 带进当前进程。`_force_hf_endpoint()` 因此做两件事：设 `os.environ`（覆盖"尚未导入"的情况）+ 遍历 `sys.modules` 覆盖所有 huggingface_hub 子模块的 `ENDPOINT`（覆盖"已导入"的情况）。

这是一处运行时补丁，上游若改变常量存放位置可能失效 —— 已记入「已知局限」，测试在 `tests/test_hf_endpoint.py`。

### PyAV（`av`，硬依赖，间接调用）

本 skill **不直接 import av**。faster-whisper 内部用 `av.open()` 解码音视频并提取音轨。列在 `requirements.txt` 里是为了让这个解码依赖显式可见 —— 它自带的静态 ffmpeg 解码库正是"系统没装 ffmpeg 也能跑"的原因。

相关错误会表现为 `decode_failed`，`error.hint` 里给出三平台的 ffmpeg 安装命令。

### ctranslate2（硬依赖，间接调用）

Whisper 模型的推理后端。本 skill 只在 `_detect_device()` 里直接碰它一次：

| 接口 | 用途 |
|---|---|
| `ctranslate2.get_cuda_device_count()` | `model.device: auto` 时探测 CUDA |

**刻意不用 torch 探测设备** —— 那会凭空多出 2GB+ 的安装体积。探测失败（属性不存在、抛异常、模块缺失）一律降级到 `cpu`。

### PyYAML（软依赖）

| 接口 | 调用位置 |
|---|---|
| `yaml.safe_load(text)` | `config.load_config` |

用 `safe_load` 而非 `load`：配置文件可能来自用户或第三方仓库，`load` 会执行任意 Python 对象构造。

缺失时自动降级到 `config.DEFAULT_CONFIG`，不报错。两边的键结构与标量值有防漂移测试把守（`tests/test_config.py::TestRealConfigFile`）。

## 三、刻意不依赖的东西

| 不依赖 | 原因 |
|---|---|
| 云端语音识别 API（讯飞/百度/Azure/Whisper API） | 本 skill 的核心卖点就是离线与隐私 |
| PandaData / 任何行情数据源 | 这是通用语音工具，与量化数据无关 |
| torch | 只为了探测设备而装 2GB 不值得 |
| pandas / numpy | 转录产物是文本分段，用不上表格计算 |
| 系统 ffmpeg | PyAV 自带解码库；缺它只降级为告警 |
| 任何账号 / API key / 环境变量凭据 | `env: {}`，五平台安装都不需要认证 |

## 四、版本兼容

| 库 | 声明 | 备注 |
|---|---|---|
| Python | ≥ 3.10 | 代码用了 `from __future__ import annotations`，3.9 语法上能跑但不在支持范围 |
| faster-whisper | ≥ 1.0 | `hotwords` 参数需 1.1+，v0.1 未使用 |
| ctranslate2 | ≥ 4.0 | 随 faster-whisper 安装 |
| av | ≥ 11.0 | 随 faster-whisper 安装 |
| modelscope | ≥ 1.9 | 仅下载通道需要 |
| huggingface_hub | ≥ 0.23 | 仅下载通道需要 |
| PyYAML | ≥ 6.0 | 缺失可降级 |
