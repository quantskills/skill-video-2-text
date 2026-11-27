---
name: skill-video-2-text
description: 当需要把本地音频或视频离线转写成文字时使用此 skill。基于 faster-whisper 在本机产出带时间戳的原始语料，标点、分段、摘要交给调用方大模型完成；零凭据、零云端 API，音频全程不离开本机。
tags: [audio, video, transcription, whisper, speech-to-text, offline, meeting-notes, subtitle]
license: GPL-3.0-only
metadata:
  license: GPL-3.0-only
  organization: QuantSkills
  organization_url: https://github.com/quantskills
  repository: skill-video-2-text
  repository_url: https://github.com/quantskills/skill-video-2-text
  project_type: skill
  collection: document-knowledge
---

# 音视频转文字 · 本地离线转录（faster-whisper）

## 工具定位

- **工具类型**：通用工具类（工程型 `engineered`，非量化流水线）。把本地音视频转成带时间戳的原始语料，是一份**给大模型当原料的中间产物生成器**，不是端到端的会议纪要工具。
- **解决问题**：在没有云端语音 API、没有账号、录音不能出本机的场景下，把「声音」变成「大模型能接着加工的结构化文本」。
- **使用对象**：agent（Claude Code / Codex / Cursor / Hermes / OpenClaw）为主，也可作为 Python 库被 import。
- **数据来源**：调用方提供的本地音视频文件（`LocalFile`），**不接任何行情 / 业务 / 数据源**。详见 `references/source_boundary.md`。

## 术语约定

| 术语 | 含义 |
|---|---|
| 第 1 阶段（脚本） | `scripts/transcribe.py` 负责的「声音 → 带时间戳的粗糙文字」 |
| 第 2 阶段（大模型） | 调用方 LLM 负责的「粗糙文字 → 可读文稿」，即下文第 5 步 |
| segment | 识别引擎按换气停顿切出的一段，形如 `{start, end, text}`。**边界是声学边界，不是语义边界** |
| 交接物 | `output/{name}.transcript.json`。两阶段之间唯一的传递载体，必须完整可信 |
| 原始态 | 第 1 阶段刻意保留语气词、缺失标点、未合并分段的状态。见 `references/methodology.md` |
| 阻塞项 / 告警项 | 自检的两种失败等级。阻塞项退出 1 必须停下；告警项退出 2 仍可转录 |
| 五级模型来源 | `explicit_path` → `local_cache` → `modelscope` → `hf_mirror` → `hf_official`，命中即止 |

> **不要把「转录」等同于「纪要」。** 本 skill 只交付第 1 阶段。第 5 步由调用方大模型完成，它不是可选的美化，而是流程的组成部分——跳过它你拿到的是一份没有标点、满是语气词的原始稿。

## 适用场景

- 把一段会议录音 / 访谈录音 / 课程录像转成可检索、可引用的文字稿
- 隐私敏感录音（问诊、法律、人事、内部谈话）**不允许上云**，必须全程本地处理
- 给视频做字幕初稿：`output/{name}.timeline.txt` 已是 `[mm:ss - mm:ss] 文本` 格式
- agent 需要先把音频内容变成文本，才能继续做摘要、抽取决议、生成待办
- 国内网络环境下不想配代理就能跑通（模型下载走 ModelScope / hf-mirror 兜底）

**不适用**：说话人分离（谁在说）、实时流式字幕、歌唱转写、需要 100% 准确的法律 / 医疗正式文本（必须人工逐字复核）。

## 运行环境

| 项 | 要求 | 缺失时的判定 |
|---|---|---|
| Python | ≥ 3.10 | **阻塞项** |
| `faster-whisper` ≥ 1.0、`ctranslate2` ≥ 4.0 | 转录核心 | **阻塞项** |
| 模型权重 `Systran/faster-whisper-base`（约 145MB） | 首次运行自动下载 | **阻塞项** |
| 系统 `ffmpeg` | 可选 | 告警项（PyAV 自带静态解码库） |
| `modelscope` ≥ 1.9 或 `huggingface_hub` ≥ 0.23 | 下载通道，二选一即可 | 告警项 |
| `PyYAML` ≥ 6.0 | 读 `config/*.yaml` | 告警项（缺失时降级到内置默认值） |
| `av` ≥ 11.0 | 视频音轨解码 | 告警项 |
| `node` | 仅跑 `scripts/validate-qsh-form.mjs` 时需要 | 不影响转录 |
| **凭据** | **无** | 不读任何账号 / token / API key |

阻塞项与告警项的分线是**刻意的**：缺 ffmpeg 不该在转录前就拦住用户，PyAV 通常够用；只有真的返回 `decode_failed` 时才回头装。这条判定线由 `tests/test_env_check.py::TestCheckFfmpeg` 把守。

自检命令（`--self-check` 在本 skill 中是**运行环境自检**语义，因为本 skill 不接任何数据源）：

```bash
python3 scripts/env_check.py --self-check          # 人读，报告走 stderr
python3 scripts/env_check.py --self-check --json   # 机读，stdout 只有一个 JSON 对象
echo $?                                            # 0=就绪 1=阻塞 2=仅告警
```

`--json` 报告的结构：

```json
{
  "ok": false,
  "status": "blocked",
  "exit_code": 1,
  "platform": {"system": "Darwin", "release": "25.5.0", "machine": "arm64"},
  "summary": {"blockers": ["faster_whisper"], "warnings": ["ffmpeg"]},
  "checks": [
    {"group": "runtime", "items": []},
    {"group": "dependencies", "items": []},
    {"group": "decoder", "items": []},
    {"group": "model", "items": []},
    {"group": "config", "items": []}
  ]
}
```

分组名与顺序固定为 `runtime` / `dependencies` / `decoder` / `model` / `config`，调用方可以依赖它。`status` 三值：`ready`（退出 0）/ `degraded`（退出 2）/ `blocked`（退出 1）。

只有三个无害的环境变量可选，全部与凭据无关：

| 变量 | 作用 |
|---|---|
| `HF_ENDPOINT` | 覆盖 HuggingFace 端点；默认按配置走 hf-mirror.com |
| `FASTER_WHISPER_MODEL_DIR` | 显式指定本地模型目录，命中则完全不触网 |
| `FASTER_WHISPER_CACHE_ROOT` | 覆盖模型缓存根目录 |

## 平台特殊处理清单

本 skill 不接市场数据，因此没有「A股特殊处理清单」。它的等价物是**跨平台 / 跨网络环境差异清单**：

| 平台 / 环境 | 特殊处理 |
|---|---|
| macOS | Python 3.9 自带版本过低，需 `brew install python@3.12` 后建 venv；Apple Silicon 上 `model.device` 设 `cuda` 无效，MPS 未验证，请用 `cpu` |
| Windows | 路径分隔符与中文路径需注意引号；`ffmpeg` 用 `winget install ffmpeg`；PowerShell 下用 `Copy-Item` 而非 `cp` |
| Debian / Ubuntu | 精简容器常缺解码库，`sudo apt install ffmpeg` 后重试；CI 镜像建议直接装上 |
| 国内网络 | pip 走 `-i https://mirrors.aliyun.com/pypi/simple/`；模型下载顺序已把 ModelScope 排在 HF 之前，**无需配置代理** |
| 代理 / 内网 | 若必须走 hf-mirror，注意 `HF_ENDPOINT` 需在**导入 huggingface_hub 之前**生效——该库在 import 时就把端点固化成模块级常量，事后改环境变量无效（v0.1 真实缺陷，见 `CHANGELOG.md`） |
| 纯 cron / 无 agent 环境 | 第 1~4 步可全自动，**第 5 步无法自动完成**（它是给大模型的指令，不是脚本）。登记在「已知局限」 |
| CI | `requirements-dev.txt` 刻意不继承运行期依赖，重框架在 `tests/` 内以桩件替代，353 例约 0.4 秒跑完；模型集成测试为 opt-in，CI 不设 `V2T_RUN_MODEL_TESTS` 即整体跳过 |

## 整体流程

五步，前四步是脚本、可全自动，第 5 步是**给调用方大模型的指令**。

```text
env_check  →  解码能力确认  →  transcribe.py  →  validate.py  →  LLM 后处理
(自检)        (软性判定)       (出原始语料)      (契约闸门)       (成稿)
                                      ↓
                   stdout: 唯一一个 JSON 对象
                   output/: .transcript.json + .transcript.txt + .timeline.txt
```

### 第 1 步 · 环境自检

```bash
python3 scripts/env_check.py --self-check --json
```

退出 1（`status: blocked`）时**必须停下来**，按报告里每项的 `hint` 处理——那些 hint 是可直接执行的命令，不是泛泛建议。退出 2（`degraded`）可以继续。

### 第 2 步 · 解码能力确认

系统 `ffmpeg` 缺失只记告警，**不拦停**。faster-whisper 底层用 PyAV 解码，PyAV 自带静态编译的 ffmpeg 解码库，多数情况下够用。策略是：**先试，真的返回 `decode_failed` 再按 hint 装 ffmpeg**。

视频文件不需要用户先手动抽音轨，直接传 `.mp4` / `.mov` / `.mkv` 即可。

### 第 3 步 · 转录（第 1 阶段）

```bash
python3 scripts/transcribe.py ~/Downloads/meeting.m4a --language zh
```

这一步**刻意什么都不做**：不加标点、不去语气词、不合并 segment、不按语义重新分段。只做两件极轻的规整——文本 `strip()`、丢弃空文本分段。

原因是**段间时间间隔是第 5 步判断句读的证据**：两段之间停了 1.2 秒很可能是句号，停了 0.1 秒多半是逗号。脚本里先把 segment 合并掉，大模型就只能靠语义猜标点，而语义猜标点在长难句上经常猜错。同理，「呃这个呃项目的话」里的重复语气词是判断迟疑与自我修正的线索，删掉后语气会变。完整论证见 `references/methodology.md`。

产出三份文件：

| 文件 | 内容 | 用途 |
|---|---|---|
| `output/{name}.transcript.json` | 完整结果对象 | **交接物**，第 4、5 步的输入 |
| `output/{name}.transcript.txt` | 分段按换行拼接，不含时间戳 | 检索、引用 |
| `output/{name}.timeline.txt` | 每行 `[mm:ss - mm:ss] 文本` | 回原音频对照、字幕初稿 |

### 第 4 步 · 校验（不能跳过）

```bash
python3 scripts/validate.py output/meeting.transcript.json --strict
echo $?    # 0 = PASS，1 = FAIL
```

这是交接物上的闸门。**拿一份 `segments` 为空或时间戳错乱的 JSON 去做第 5 步，大模型不会报错，它会编**——产出一份格式完美、语气通顺、内容全是幻觉的文稿。这比脚本直接崩掉危险得多，因为用户看不出问题。典型触发场景：视频只有画面没有音轨，解码不报错但 `segments` 是空数组。

校验项：8 个顶层字段 + 5 个 source 字段齐全、`ok` 必须为 `true`、时间戳单调递增且 `end >= start`、末段不超总时长（容差 1.0 秒）、`text` 是非空字符串、`segment_count` 与实际长度一致、`segments` 非空。

校验器自身**不得因输入畸形而抛异常**，只能返回 FAIL——一个崩在它本该拒绝的输入上的校验器，等于把闸门变成新的故障点。由 `tests/test_validate.py::TestValidatorNeverCrashes` 把守。

FAIL 时**流程必须停止**，不要带着残缺数据进第 5 步。

### 第 5 步 · 大模型后处理（第 2 阶段）

这一步**不是脚本**，是交给调用方大模型的指令。`agents/openai.yaml` 里 `x-steps` 的 `postprocess` 项 `script: null` 就是这个意思。

大模型读 `output/{name}.transcript.json`（或 stdout 那一个 JSON 对象），完成四件事：

1. **纠错**：修同音字与专有名词错字。「在/再」「制定/制订」要看上下文语义；人名、公司名、产品名、术语结合 `--initial-prompt` 给的领域词判断。规则脚本做不到这件事。
2. **补标点**：**用 segment 之间的时间间隔作为主要依据**，语义为辅。这正是第 3 步保留原始态的原因。
3. **按语义分段**：识别话题转折点重新分段，**不要按时间戳每 N 秒机械切**。声学边界不等于语义边界。
4. **写摘要**：3~5 句，覆盖结论、决议、待办、风险点。若能识别出待办，单列成清单并注明负责人（如果原文提到）。

交付 `output/{name}.transcript.md`，建议结构：

```markdown
# {原标题}

## 摘要
（3-5 句）

## 待办 / 决议
- [ ] {事项} —— {负责人}，{期限}

## 正文
{纠错、加标点、按语义分段后的全文}

---
> 原始转录：`{name}.transcript.json`（{segment_count} 段，{duration} 秒，模型 {model_size}，语言 {language}）
> 本文稿由自动转录 + 大模型后处理生成，**未经人工逐字复核**，不得作为正式依据。
```

末尾那行免责声明**不要省**。它把「机器生成、未复核」这个事实显式传递给读者。

**保留原始 JSON**：它是可追溯性的凭据。用户随时可以对照 `*.timeline.txt` 检查「这句话原文到底是什么」，前提是原始态还在。

## 输出契约

stdout **永远是且仅是一个 JSON 对象**。进度、模型下载日志、依赖告警一律走 stderr。机器消费方可以放心 `json.loads(stdout)`——这条契约由 `tests/test_stdio_contract.py` 与 CI 负路径断言双重把守，它曾是 v0.1 的真实缺陷。

### 成功

```json
{
  "ok": true,
  "source": {
    "path": "/Users/demo/Downloads/meeting.m4a",
    "name": "meeting.m4a",
    "ext": ".m4a",
    "kind": "audio",
    "size_bytes": 18432000
  },
  "model_size": "base",
  "language": "zh",
  "language_probability": 0.987,
  "duration": 1843.2,
  "segment_count": 2,
  "segments": [
    {"start": 0.0, "end": 3.2, "text": "呃今天我们主要讨论三季度的排期"},
    {"start": 4.4, "end": 7.8, "text": "先看第一个议题就是数据接口的改造"}
  ],
  "outputs": {
    "json": "/Users/demo/workspace/output/meeting.transcript.json",
    "plain_text": "/Users/demo/workspace/output/meeting.transcript.txt",
    "timeline": "/Users/demo/workspace/output/meeting.timeline.txt"
  }
}
```

字段说明：

| 字段 | 类型 | 说明 |
|---|---|---|
| `ok` | bool | 成功恒为 `true`。校验器要求它必须为 `true`，失败结果不能被当成转录产物 |
| `source.path` | str | 输入文件绝对路径 |
| `source.name` | str | 文件名（含扩展名） |
| `source.ext` | str | 小写扩展名 |
| `source.kind` | str | `audio` 或 `video` |
| `source.size_bytes` | int | 文件字节数 |
| `model_size` | str | 实际使用的模型规格 |
| `language` | str | BCP-47 语言码，如 `zh` / `en` / `zh-CN` |
| `language_probability` | float | 语言检测置信度，0~1，保留 3 位 |
| `duration` | float | 音频总时长（秒），保留 2 位 |
| `segment_count` | int | `segments` 长度，必须与之一致 |
| `segments[].start` | float | 起始秒，保留 2 位 |
| `segments[].end` | float | 结束秒，保留 2 位，单调递增 |
| `segments[].text` | str | 原始态文本，已 `strip()`，**无标点、含语气词** |
| `outputs` | dict | 三份产物的实际路径；`--no-write` 时为空对象 |

### 失败

```json
{
  "ok": false,
  "error": {
    "code": "input_not_found",
    "message": "文件不存在: /Users/demo/Downloads/meeting.m4a",
    "hint": "请确认路径拼写；含空格或中文的路径需要加引号。"
  }
}
```

`error` 恒为 `{code, message, hint}` 三件套，不多不少：`code` 给机器分支（稳定枚举，见下节），`message` 给人读（含实际值），`hint` 给**可直接执行的修复动作**。调用方读 `code` 分支处理，**不要解析 traceback**。

### 机器可执行版本

`scripts/validate.py` 是上述契约的机器可执行版本，返回 `{status, errors, warnings, stats}`，`status` 为 `PASS` 或 `FAIL`。`--strict` 把告警也计入失败。

## 错误码

12 个，构成**稳定枚举**——新增需同步改代码、`agents/openai.yaml` 的 `x-error-codes`、本表，以及补上测试。`tests/test_contract_files.py::TestErrorCodeConsistency` 会做「代码 ↔ 本文档 ↔ openai.yaml」三方一致性校验，少一处就红。

| `error.code` | 什么时候出现 | 该怎么办 |
|---|---|---|
| `missing_argument` | 命令行没有给输入文件路径（唯一使用退出码 2 的情形） | 按 hint 补上路径：`python3 scripts/transcribe.py <文件> --language zh` |
| `input_not_found` | 路径不存在 | 检查拼写；含空格或中文的路径必须加引号；确认 `~` 已被 shell 展开 |
| `input_not_a_file` | 路径是目录、设备节点或符号链接指向的目录 | 本 skill 只处理单个音视频文件，传具体文件而不是目录 |
| `unsupported_format` | 扩展名不在支持清单内（如 `.amr` `.silk` `.ts` `.pcm`） | 按 hint 列出的清单确认可用格式；其他格式先用 ffmpeg 真转码，**不要靠改扩展名绕过** |
| `input_unreadable` | 文件存在但打不开（权限不足或被独占） | 检查文件权限，确认没有其他进程独占该文件 |
| `input_empty` | 文件为 0 字节 | 无法解码，请重新导出或重新下载 |
| `input_too_large` | 超过 `input.max_size_mb`（默认 4096MB） | 调大 `config/transcribe_config.yaml` 的 `input.max_size_mb`，或先切分文件 |
| `dependency_missing` | 未安装 faster-whisper 或 ctranslate2 | 按 hint 执行 `pip install -r requirements.txt`，国内加镜像参数 |
| `model_load_failed` | 模型目录不完整，或 device / compute_type 组合不被支持 | 按 hint 把 `model.compute_type` 从 `int8` 改成 `float32` 重试；仍失败则删除缓存目录让它重新下载 |
| `decode_failed` | 解码器问题（精简 Python 环境缺解码库、容器损坏、编码不支持） | 按 hint 安装系统 ffmpeg：macOS `brew install ffmpeg`、Windows `winget install ffmpeg`、Debian·Ubuntu `sudo apt install ffmpeg` |
| `transcribe_failed` | 识别过程本身异常，且不属于上述任何一类 | 把完整 stderr 反馈给维护者；可先换 `--model-size tiny` 排除模型规格问题 |
| `interrupted` | 用户 Ctrl-C 中断（唯一使用退出码 130 的情形） | 无需处理，重新运行即可；已下载的模型会命中缓存不会重下 |

> 错误码的**类名不限定**：本 skill 用自定义的 `InputValidationError(ValueError)` 承载 `code` / `message` / `hint`，而不是直接抛 `RuntimeError`。契约约束的是**行为**（必须抛异常、异常必须携带可机读的失败分类），不是类名。详见「使用方式」。

### 退出码

| 退出码 | 含义 |
|---|---|
| `0` | 成功，stdout 是完整结果对象 |
| `1` | 业务失败，stdout 是 `{ok: false, error: {...}}`，读 `error.code` 分支 |
| `2` | 缺少或非法参数（仅 `missing_argument`） |
| `130` | 用户中断（仅 `interrupted`） |

`scripts/env_check.py --self-check` 复用前三个：`0` 就绪、`1` 有阻塞项、`2` 仅有告警仍可转录。`scripts/validate.py` 只用 `0`（PASS）与 `1`（FAIL）。

## 使用方式

### 作为命令行工具

```bash
# 最简
python3 scripts/transcribe.py ~/Downloads/meeting.m4a

# 指定语言与模型（显式给 language 比自动检测更快更准）
python3 scripts/transcribe.py ~/Downloads/meeting.m4a --language zh --model-size base

# 领域提示词，改善专有名词识别
python3 scripts/transcribe.py meeting.m4a --language zh --initial-prompt "以下是量化交易系统评审会议，涉及因子、回测、夏普比率"

# 只出 JSON 不落盘
python3 scripts/transcribe.py meeting.m4a --no-write

# 指定产物目录与配置文件
python3 scripts/transcribe.py meeting.m4a --output-dir /tmp/out --config config/transcribe_config.yaml
```

参数：

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `input` | 位置参数 | 必填 | 音频或视频文件路径 |
| `--language` | str | 自动检测 | BCP-47 码，如 `zh` / `en`；也尊重 config 的 `transcribe.language` |
| `--model-size` | str | `base` | `tiny` / `base` / `small` / `medium` / `large-v3`；仅 `base` 做过端到端验证 |
| `--model-path` | str | 无 | 显式本地模型目录，给了就完全不触网 |
| `--initial-prompt` | str | 无 | 领域提示词 |
| `--config` | str | `config/transcribe_config.yaml` | 配置文件路径 |
| `--output-dir` | str | config 的 `output.dir` | 产物目录；相对路径按仓库根解析 |
| `--no-write` | flag | false | 只打 JSON，不落盘 |

### 作为库被 import

```python
import sys
sys.path.insert(0, "/path/to/skill-video-2-text")

from scripts.transcribe import run, validate_input, InputValidationError

# 预检（不转录，只校验输入合法性）
info = validate_input("/abs/path/meeting.m4a")
# -> {"path": ..., "name": ..., "ext": ..., "kind": "audio", "size_bytes": ...}

# 转录
try:
    result = run("/abs/path/meeting.m4a", language="zh", model_size="base")
except InputValidationError as exc:
    print(exc.code, exc.message, exc.hint)   # 机器分支用 code
    raise
print(result["segment_count"], result["outputs"]["json"])
```

`run()` 的完整签名：

```python
def run(input_path: str,
        language: str | None = None,
        model_size: str | None = None,
        model_path: str | None = None,
        initial_prompt: str | None = None,
        output_dir: str | None = None,
        config: str | None = None,
        write_files: bool = True,
        **kwargs) -> dict:
    ...
```

**异常契约（import 调用方）**：`run()` / `validate_input()` / `transcribe()` 在校验失败、依赖缺失、模型不可用、解码失败时抛 `InputValidationError`。

- 类名：`InputValidationError`，继承自内置 `ValueError`（因此 `except ValueError` 也能兜住）
- 触发条件：输入校验不通过（6 个 `input_*` / `unsupported_format` 码）、缺 faster-whisper（`dependency_missing`）、模型加载失败（`model_load_failed`）、解码或识别失败（`decode_failed` / `transcribe_failed`）
- 携带属性：`.code`（稳定枚举，机器分支用）、`.message`（人读，含实际值）、`.hint`（可直接执行的修复动作）

`KeyboardInterrupt` 不被包装，由 CLI 层捕获后转成 `interrupted` / 退出 130。

**错误契约（CLI 调用方）**：同一份失败信息在命令行下表现为「非 0 退出码 + stdout 唯一一个 `{ok: false, error: {...}}`」，不抛 traceback。两个契约同时成立——`run()` 抛异常是给 import 方的，退出码是给 subprocess 方的。

其余可 import 的接口（`transcribe` / `write_outputs` / `render_plain_text` / `render_timeline` / `classify_extension`，以及 `model_utils` / `validate` / `env_check` / `config` 的函数）见 `references/need_used_api.md`，总入口索引见 `references/api_guide.md`。

### 配置

超参全部外置到 `config/transcribe_config.yaml`，脚本内不硬编码。优先级：**命令行 > 环境变量 > `config/*.yaml` > 代码内置默认值**。PyYAML 缺失时静默降级到内置默认值，行为与走 YAML 分支一致（两者同源，由 `tests/test_config.py` 做一致性校验）。

```yaml
model:
  size: base          # tiny / base / small / medium / large-v3
  device: cpu         # cpu / cuda / auto（auto 会探测 CUDA，刻意不引入 torch）
  compute_type: int8  # int8 提速明显；部分平台不支持时改 float32
transcribe:
  beam_size: 5
  temperature: 0.0                  # 贪心解码：转录要可复现，不是要有创意
  condition_on_previous_text: false # 降低长音频的重复幻觉
  vad_filter: true
  vad_min_silence_ms: 2000
```

`temperature: 0.0` 是刻意的：同一份音频两次运行应该得到同一份文字。

### 测试

```bash
pip install -r requirements-dev.txt -i https://mirrors.aliyun.com/pypi/simple/
python3 -m pytest tests/ -q        # 353 passed + 2 skipped，约 0.4 秒
```

`requirements-dev.txt` **刻意不继承** `requirements.txt`：重依赖在 `tests/` 内全部以桩件替代。这样做有三个好处——CI 十几秒跑完而不是先拉几百 MB 二进制；契约测试本来就不需要真引擎；更关键的是，**只有在不装重依赖的环境里，「缺依赖必须报 `dependency_missing`」这条负路径才天然可测**。

## 验收要求

| 项 | 要求 | 证据 |
|---|---|---|
| 入口可运行 | `python3 scripts/transcribe.py --help` 正常；`run()` 可 import | `tests/test_transcribe_core.py` |
| 输入校验 | `validate_input()` 对不存在 / 非文件 / 不支持格式 / 不可读 / 空 / 超限六种情况抛结构化异常 | `tests/test_validate_input.py` |
| stdout 纯净 | 任何路径下 stdout 恰好一个 JSON 对象，日志走 stderr | `tests/test_stdio_contract.py` |
| 错误码三方一致 | 代码 ↔ 本文档 ↔ `agents/openai.yaml` 完全相同，且每个码都有测试覆盖 | `tests/test_contract_files.py::TestErrorCodeConsistency` |
| 退出码契约 | 0 / 1 / 2 / 130 四值，与文档一致；`env_check` 的 0 / 1 / 2 在 openai.yaml 中声明 | `tests/test_contract_files.py::TestExitCodeConsistency` |
| 自检如实报告 | 阻塞项存在时 `status: blocked` 且非 0 退出，分组名与顺序稳定 | `tests/test_env_check.py` |
| 校验器不崩 | 畸形输入只返回 FAIL，绝不抛异常 | `tests/test_validate.py::TestValidatorNeverCrashes` |
| 第 3 步不做语义清理 | 只允许 `strip()` 与丢空段，不得加标点 / 删语气词 / 合并 segment | `tests/test_transcribe_core.py::TestTranscribeResultShape` |
| 原始 JSON 必须落盘 | 可追溯性凭据，不能只在 stdout 一闪而过 | `tests/test_render.py::TestWriteOutputs` |
| 模型五级回退 | 命中即不重复下载；同时识别 faster-whisper 与 ModelScope 两种缓存布局 | `tests/test_model_discovery.py` |
| hf-mirror 真的生效 | 切换端点时同步改写已导入模块树上的每一份 ENDPOINT | `tests/test_hf_endpoint.py` |
| 零凭据 | `scripts/*.py` 的 AST 不读任何凭据类环境变量，只允许三个无害变量 | `tests/test_contract_files.py::TestRequirements` |
| 契约文件可解析 | 所有 `*.md` 内嵌的 yaml / json 代码块、`agents/openai.yaml`、`config/*.yaml` 均能解析 | `tests/test_contract_files.py::TestEmbeddedSnippetsParse` |
| 发布合规 | LICENSE 为 GPLv3 全文；INSTALL.md 覆盖 5 个平台；两份 README 含免责声明；CHANGELOG 遵循 Keep a Changelog；`.gitignore` 排除转录产物 | `tests/test_contract_files.py::TestReleaseCompliance` |
| CI | qsh-form 校验 + pytest + 5 步负路径反向断言，红灯不予验收 | `.github/workflows/validate.yml` |
| 模型可靠性（opt-in） | 真跑 faster-whisper 推理：同输入两次结果逐字一致、语种置信度 ≥ 0.9、关键术语覆盖 ≥ 11/13、视频容器解封装出真实语音、时间轴单调不重叠、权重缓存命中不重复下载 | `tests/test_model_reliability.py`（13 例，需 `V2T_RUN_MODEL_TESTS=1` + 装齐运行期依赖；CI 上默认整体跳过） |
| 文档一致性 | 术语约定 / 适用场景 / 运行环境 / 整体流程 / 输出契约 / 错误码 / 使用方式 / 验收要求 / 已知局限 齐全 | 本文档 + `TestSkillMdFrontmatter` |

**未来函数检测**：本 skill 无训练集、无时序切分、不做任何预测，该项**不适用**（理由登记在「已知局限」）。

**种子可复现性**：`temperature: 0.0` + `beam_size: 5`，即束搜索且不启用采样回退，不引入随机性，无需固定种子。同机同模型同参数下输出一致（由 `test_repeat_transcription_is_identical` 实测把守）；跨版本的比特级一致性不能保证，见「已知局限」。

## 已知局限

| 局限 | 说明 | 后续 |
|---|---|---|
| 仅 `base` 端到端验证 | `tiny` / `small` / `medium` / `large-v3` 参数已适配但未实跑验证 | v0.2 补端到端 |
| 无说话人分离 | 输出不含「谁在说」，多人对话需人工区分；多人同时说话只会得到糊在一起的稿子 | 评估 pyannote |
| 无实时流式 | 只支持整文件转录 | 需求驱动 |
| 无热词表 | 专有名词只能靠 `--initial-prompt` 软引导，不能强制 | 评估 vocabulary 偏置 |
| 音乐 / 歌唱效果差 | Whisper 面向语音训练，歌唱会产出幻觉歌词 | 不在范围内 |
| 长音频受内存限制 | 数小时录音一次性载入；CPU int8 下耗时约为音频时长的 0.2~0.5 倍 | v0.2 分块转录 |
| 第 5 步脱离 agent 无法自动完成 | 它是给大模型的指令，不是脚本。纯 cron 批处理只能跑到第 4 步 | v0.2 可选补一个 LLM API 版后处理脚本 |
| 不能保证比特级一致 | 同机同参数可复现；跨 faster-whisper / ctranslate2 版本、跨 CPU 指令集、跨 `compute_type` 时结果可能有细微差异。模型权重按上游仓库当前快照下载，不锁 revision | 需要严格复现时用 `--model-path` 指向固定权重目录 |
| 转录准确率有上限 | 强口音、方言、远场拾音、背景音乐、8kHz 电话录音都会明显拉低准确率。任何正式用途前**必须人工逐字复核** | 换更大模型 |
| `base` 模型输出繁体与同音字错误 | **实测**（`sample_zh.wav`，CPU int8）：内容骨架与句式正确，但倾向输出繁体（對/數據/週/測），金融术语出同音字错（「對**其**」应为「对**齐**」、「**自斷**」应为「**字段**」、「**戶身**300」应为「**沪深**三百」、「**華點**」应为「**滑点**」），数字写成阿拉伯数字（「千分之1.5」应为「千分之一点五」），标点一逗到底。**这正是第 2 阶段（调用方大模型后处理）存在的理由，不是脚本缺陷** | 换更大模型可降低错误率；正式用途走第 5 步后处理 + 人工复核。证据：`test_known_weak_spots_are_documented_not_hidden` |
| 连续语音不分段，时间轴会退化 | **实测**：21 秒不间断发言只切出 **1 个** segment（0.0→21.0s 全文挤在一段）。原因是 `vad_filter: true` + `vad_min_silence_ms: 2000` 需 2 秒静音才切分，而整段又落在 whisper 单个 30 秒窗口内。对会议录音这类长时间连续发言，`timeline` 会退化成一条巨段，**失去跳转与字幕对齐价值**（`demo.mp4` 有自然停顿，同样时长切出 6 段，可作对照） | 调小 `vad_min_silence_ms` 可强制切分，代价是把语气停顿也切碎。证据：`test_continuous_speech_collapses_to_single_segment` |
| 未来函数检测不适用 | 无训练集、无时序切分、不做预测，故无「训练集严格 `date < T`」可断言。**这是能力性质使然，不是遗漏** | 不适用 |
| 不锁依赖精确版本 | `requirements.txt` 用 `>=` 下限约束，上游破坏性更新可能影响行为 | 需要时补 lock 文件 |

**隐私边界**：音频不离开本机（无上传、无云端 API），但转录产物是**明文文本**，敏感度不低于原始录音，且更容易被搜索、复制、误提交。`output/` 与 `*.transcript.*` / `*.timeline.txt` 已 gitignore。处理问诊、法律、财务、人事类录音时建议把 `--output-dir` 指到加密卷，用完删除中间产物。**不要把转录稿粘贴进在线 LLM**——那等于放弃本地转录的全部隐私优势。

**合规边界**：录制他人语音在多数司法辖区需要事先授权。是否有权转录、转录后如何保管，由使用者自行确认并承担全部责任。本 skill 仅供研究与教育用途，不构成投资、法律或医疗建议。

## 表单声明（qsh-form）

下面这块供支持 qsh-form 的宿主渲染成输入表单，由 `scripts/validate-qsh-form.mjs` 校验（CI 会跑）。

```json qsh-form
{
  "version": 1,
  "task": {
    "placeholder": "描述你要转录的文件和想要的成品，例如：把这段会议录音转成带决议与待办的纪要",
    "required": true
  },
  "fields": [
    {
      "key": "input_path",
      "type": "text",
      "label": "音视频文件路径",
      "placeholder": "/absolute/path/meeting.m4a",
      "required": true,
      "help": "必须是本机已存在的文件。含空格或中文的路径请加引号；支持 mp3 wav m4a flac ogg aac wma opus 与 mp4 mov mkv avi webm flv wmv m4v"
    },
    {
      "key": "language",
      "type": "select",
      "label": "语言",
      "required": false,
      "help": "显式指定比自动检测更快更准",
      "options": [
        {"value": "auto", "label": "自动检测"},
        {"value": "zh", "label": "中文"},
        {"value": "en", "label": "English"},
        {"value": "ja", "label": "日本語"},
        {"value": "ko", "label": "한국어"}
      ]
    },
    {
      "key": "model_size",
      "type": "select",
      "label": "模型规格",
      "required": false,
      "help": "仅 base 做过端到端验证；越大越准也越慢",
      "options": [
        {"value": "base", "label": "base（默认，约 145MB）"},
        {"value": "tiny", "label": "tiny（最快，准确率最低）"},
        {"value": "small", "label": "small（约 2 倍耗时）"},
        {"value": "medium", "label": "medium（约 5 倍耗时）"},
        {"value": "large-v3", "label": "large-v3（约 10 倍以上耗时）"}
      ]
    },
    {
      "key": "initial_prompt",
      "type": "textarea",
      "label": "领域提示词",
      "required": false,
      "placeholder": "以下是量化交易系统评审会议，涉及因子、回测、夏普比率",
      "help": "给模型领域上下文以改善专有名词识别。这是软引导，不能强制"
    },
    {
      "key": "output_dir",
      "type": "text",
      "label": "产物目录",
      "required": false,
      "placeholder": "output",
      "help": "相对路径按仓库根解析。处理敏感录音时建议指到加密卷"
    },
    {
      "key": "deliverable",
      "type": "select",
      "label": "交付形态",
      "required": false,
      "help": "第 5 步由大模型完成，这里决定它产出什么",
      "options": [
        {"value": "minutes", "label": "会议纪要（摘要 + 待办 + 全文）"},
        {"value": "fulltext", "label": "仅全文（纠错加标点分段）"},
        {"value": "summary", "label": "仅摘要（3-5 句）"},
        {"value": "timeline", "label": "仅时间轴稿（字幕初稿）"}
      ]
    },
    {
      "key": "audience",
      "type": "text",
      "label": "读者 / 用途",
      "required": false,
      "placeholder": "给未参会的产品同学看",
      "help": "影响第 5 步的行文详略与术语解释程度"
    },
    {
      "key": "record_date",
      "type": "date",
      "label": "录音日期",
      "required": false,
      "help": "写进成稿标题与元信息"
    },
    {
      "key": "speaker_notes",
      "type": "textarea",
      "label": "说话人说明",
      "required": false,
      "placeholder": "两位：产品负责人（女声）、后端工程师（男声）",
      "help": "本 skill 不做说话人分离。给出说明后，第 5 步可据此人工归因，但准确性不保证"
    },
    {
      "key": "max_minutes",
      "type": "number",
      "label": "预计时长（分钟）",
      "required": false,
      "help": "用于预估耗时。CPU + int8 + base 下约为音频时长的 0.2~0.5 倍"
    }
  ],
  "prompt_template": "请把本地文件 {{input_path}} 离线转写成文字，按 skill-video-2-text 的五步流程执行：\n\n1. 先跑 `python3 scripts/env_check.py --self-check --json`。退出码 1 表示有阻塞项，停下来按 hint 处理并把结论告诉我；退出码 2 表示只有告警，可以继续。\n2. 确认解码能力。缺系统 ffmpeg 只是告警，不要因此中断，先试。\n3. 执行转录：`python3 scripts/transcribe.py {{input_path}} --model-size {{model_size}} --language {{language}}{{#initial_prompt}} --initial-prompt \"{{initial_prompt}}\"{{/initial_prompt}}{{#output_dir}} --output-dir {{output_dir}}{{/output_dir}}`。stdout 只有一个 JSON 对象，日志在 stderr，请直接 `json.loads` 结果而不要去解析 stderr。失败时读 `error.code` 分支处理，把 `error.hint` 原样给我。\n4. 执行校验：`python3 scripts/validate.py <上一步 outputs.json 的路径> --strict`。FAIL 时**必须停下**，不要带着残缺数据进第 5 步——那样只会产出一份格式完美但内容全是编的文稿。把 errors 原样告诉我。\n5. 你来完成后处理：修同音字与专有名词错字、**以 segment 之间的时间间隔为主要依据**补标点、按语义（不是按时间戳）重新分段、写 3-5 句摘要。交付形态为「{{deliverable}}」，读者是「{{audience}}」，录音日期 {{record_date}}，说话人说明：{{speaker_notes}}。预计时长约 {{max_minutes}} 分钟。\n\n附加要求：\n- 成稿末尾保留那行「未经人工逐字复核」的免责声明，不要省。\n- 不要删除或改写 `output/` 下的原始 JSON 与 timeline，它们是可追溯性凭据。\n- 转录稿是明文敏感数据，不要提交到版本库、不要上传第三方服务、也不要粘贴进任何在线模型。\n- 本 skill 仅供研究与教育用途；正式用途前我会自己逐字复核。\n\n背景补充：{{task}}"
}
```

## 参考文档索引

| 文档 | 内容 |
|---|---|
| `references/api_guide.md` | **总入口索引**：先读这份，再按需下钻 |
| `references/data_guide.md` | 输入格式指南：支持清单、约束、什么样的输入能得到好结果、耗时预期、常见陷阱、隐私 |
| `references/methodology.md` | 方法论：为什么是「脚本转录 + 大模型后处理」两阶段，以及它带来的 7 条硬性约束 |
| `references/need_used_api.md` | 依赖接口清单：对外暴露的契约 + 调用的第三方接口 + 刻意不依赖的东西 |
| `references/source_boundary.md` | 模型来源与输入来源的边界、离线可复现性、产物与隐私、网络访问清单 |
| `agents/openai.yaml` | OpenAPI 3.1 工具定义，供 Codex / function-calling 平台加载 |
| `agents/cursor-rule.mdc` | Cursor rule |
| `agents/portable-loader.md` | 通用可移植加载说明（英文，含 subprocess 与 import 两种集成方式） |
| `INSTALL.md` | 五平台安装指南 |
| `CHANGELOG.md` | 版本变更，含 v0.1 五项真实缺陷的记录 |
