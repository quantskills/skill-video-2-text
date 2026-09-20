# API Guide · 对外调用总入口

这是 `skill-video-2-text` 接口文档的**总入口**。本 skill 是工程型，接口文档按职责拆成多份，本文件负责索引 + 给出最常用的调用方式；完整签名清单在 `need_used_api.md`。

## 先看这里 · 三份文档怎么选

| 你想做什么 | 读哪份 |
|---|---|
| 用命令行转一个文件 / 用 Python import 调 `run()` | **本文件**（下文「快速调用」） |
| 查全部可 import 的函数签名，或确认改某行调用会影响谁 | `need_used_api.md` |
| 搞清楚支持哪些输入格式、什么样的音频能得到好结果、常见陷阱 | `data_guide.md` |
| 理解「为什么脚本只出原始语料、清理交给大模型」这个设计 | `methodology.md` |
| 确认模型从哪下载、能不能离线、产物隐私怎么处理 | `source_boundary.md` |
| 查错误码 / 退出码 / 输出契约字段 | `../SKILL.md`（「输出契约」「错误码」两节） |
| 在 Codex / Cursor / Hermes / OpenClaw 里挂载 | `../INSTALL.md` + `../agents/` |

## 一句话能力边界

**输入**：调用方提供的**本地**音视频文件（`LocalFile`）。不接行情、不接 PandaData、不接任何业务数据源、不调云端语音 API。
**输出**：带时间戳的**原始**语料（JSON + 纯文本 + 时间轴稿）。标点、分段、摘要**不在本 skill 内完成**——那是调用方大模型的第 5 步。
**凭据**：无。不读任何账号 / token / API key（有 AST 级测试把守）。

## 快速调用

### 方式一 · 命令行（推荐给 subprocess 集成）

```bash
python3 scripts/env_check.py --self-check --json     # 1. 自检：0 就绪 / 1 阻塞 / 2 仅告警
python3 scripts/transcribe.py /abs/meeting.m4a --language zh   # 2. 转录
python3 scripts/validate.py output/meeting.transcript.json --strict  # 3. 校验：0 PASS / 1 FAIL
```

第 4 步（大模型后处理）没有脚本，见 `../SKILL.md`「整体流程」第 5 步。

`transcribe.py` 的 stdout **永远是且仅是一个 JSON 对象**，进度与下载日志走 stderr，所以可以直接：

```bash
python3 scripts/transcribe.py /abs/meeting.m4a --language zh 2>/dev/null | jq -r '.segments[].text'
```

失败时 stdout 是 `{"ok": false, "error": {"code": ..., "message": ..., "hint": ...}}`，退出码非 0。读 `error.code` 分支，**不要解析 traceback**。

### 方式二 · Python import（推荐给同进程 agent）

```python
import sys
sys.path.insert(0, "/path/to/skill-video-2-text")   # 仓库根，不是 scripts/

from scripts.transcribe import run, validate_input, InputValidationError
from scripts.env_check import self_check
from scripts.validate import validate_file

# 0. 环境自检（可选，但强烈建议先跑）
report = self_check()
if report["status"] == "blocked":
    raise SystemExit(report["summary"]["blockers"])   # 每项的 hint 在 report["checks"] 里

# 1. 输入预检（可选；run() 内部也会调，单独调可以更早失败）
info = validate_input("/abs/meeting.m4a")
# -> {"path": ..., "name": ..., "ext": ".m4a", "kind": "audio", "size_bytes": ...}

# 2. 转录
try:
    result = run("/abs/meeting.m4a", language="zh", model_size="base")
except InputValidationError as exc:
    handle(exc.code, exc.message, exc.hint)   # 机器分支用 .code
    raise

# 3. 校验交接物
verdict = validate_file(result["outputs"]["json"], strict=True)
if verdict["status"] != "PASS":
    raise SystemExit(verdict["errors"])       # FAIL 时必须停下，不要进第 5 步

# 4. 把 result["segments"] 交给大模型做后处理
```

`run()` 的完整签名与所有参数含义见 `../SKILL.md`「使用方式」；其余可 import 的函数（`transcribe` / `write_outputs` / `render_plain_text` / `render_timeline` / `classify_extension`，以及 `model_utils` / `config` 的函数）见 `need_used_api.md` §一。

## 三个必须知道的契约

### 1. 异常契约（import 方）与错误契约（CLI 方）同时成立

同一份失败信息有两种表现，**不是二选一**：

| 调用方式 | 失败表现 |
|---|---|
| Python import | 抛 `InputValidationError`（继承 `ValueError`），携带 `.code` / `.message` / `.hint` |
| subprocess | 非 0 退出码 + stdout 唯一一个 `{"ok": false, "error": {...}}`，不抛 traceback |

`run()` **必须**抛异常而不是返回错误 dict——否则 import 方要靠猜。CLI 层负责把异常翻译成退出码与 JSON。

规范只约束**行为**（必须抛、异常必须携带可机读的失败分类），**不约束类名**。本 skill 选 `InputValidationError(ValueError)` 而不是裸 `RuntimeError`，是为了让 `.code` 有一个自然的载体，同时保留 `except ValueError` 的兼容性。

### 2. 12 个错误码是稳定枚举

`missing_argument` · `input_not_found` · `input_not_a_file` · `unsupported_format` · `input_unreadable` · `input_empty` · `input_too_large` · `dependency_missing` · `model_load_failed` · `decode_failed` · `transcribe_failed` · `interrupted`

每个码的含义、触发条件与修复动作见 `../SKILL.md`「错误码」。这份清单在**代码 ↔ SKILL.md ↔ `../agents/openai.yaml`** 三方由 `../tests/test_contract_files.py::TestErrorCodeConsistency` 强制一致，改一处不改其余会直接红灯。

退出码只有四个：`0` 成功 / `1` 业务失败 / `2` 缺参数（仅 `missing_argument`）/ `130` 用户中断（仅 `interrupted`）。`env_check.py` 复用前三个，`validate.py` 只用 `0` 与 `1`。

### 3. 第 1 阶段刻意「什么都不做」

`segments[].text` 是**原始态**：无标点、含语气词、按换气停顿切分、未按语义合并。只做了两件极轻的规整——`strip()` 与丢弃空文本分段。

这不是没做完，是设计。**段间时间间隔是第 5 步判断句读的证据**，删掉就没了；保留原始态也就是保留可追溯性（用户随时能对照 `*.timeline.txt` 查原文）。因此：

- **不要**在第 1 阶段的输出上再加一层规则清理（加标点、删语气词、合并 segment 都属越界）
- **不要**跳过第 4 步校验。拿一份 `segments` 为空或时间戳错乱的 JSON 去做后处理，大模型不会报错，它会**编**——产出一份格式完美、内容全是幻觉的文稿
- 完整论证见 `methodology.md`

## 输出产物

| 路径 | 内容 | 谁消费 |
|---|---|---|
| `output/{name}.transcript.json` | 完整结果对象（8 个顶层字段 + `segments` + `outputs`） | **交接物**：第 4 步校验、第 5 步后处理 |
| `output/{name}.transcript.txt` | 分段按换行拼接，不含时间戳 | 检索、引用 |
| `output/{name}.timeline.txt` | 每行 `[mm:ss - mm:ss] 文本` | 回原音频对照、字幕初稿 |
| stdout | 唯一一个 JSON 对象 | subprocess 集成方 |
| `output/{name}.transcript.md` | 第 5 步的成稿 | **由调用方大模型写出**，本 skill 不产出 |

字段级契约见 `../SKILL.md`「输出契约」；机器可执行的校验版本是 `scripts/validate.py`。

`output/` 与 `*.transcript.*` / `*.timeline.txt` 已在 `../.gitignore` 中排除——转录稿是明文敏感数据，敏感度不低于原始录音。**不要提交，不要上传第三方服务，也不要粘贴进在线 LLM。**

## 配置与超参

超参全部外置在 `../config/transcribe_config.yaml`，脚本内不硬编码。优先级：**命令行 > 环境变量 > `config/*.yaml` > 代码内置默认值**。

PyYAML 缺失时静默降级到内置默认值（`scripts/config.py::DEFAULT_CONFIG`），行为与走 YAML 分支一致——两者同源，由 `../tests/test_config.py` 做一致性校验。所以缺 PyYAML 是**告警**而非阻塞。

可调项速查：

| 键 | 默认 | 什么时候改 |
|---|---|---|
| `model.size` | `base` | 准确率不够往上调（`small`/`medium`/`large-v3`），耗时按 2/5/10 倍增长 |
| `model.device` | `cpu` | 有 CUDA 时改 `auto` 或 `cuda`；Apple Silicon 的 MPS 未验证，保持 `cpu` |
| `model.compute_type` | `int8` | 报 `model_load_failed` 时改 `float32` |
| `model.path` | `null` | 想完全不触网就指向本地权重目录 |
| `download.sources` | modelscope → hf_mirror → hf_official | 内网环境可只留可用的通道 |
| `download.cache_root` | `null` | 想把权重放加密卷时改这里 |
| `transcribe.temperature` | `0.0` | **不要改**。贪心解码是刻意的：转录要可复现，不是要有创意 |
| `transcribe.beam_size` | `5` | 想提速可降到 `1`，准确率略降 |
| `transcribe.vad_filter` | `true` | 背景音乐多时可关掉试试 |
| `input.max_size_mb` | `4096` | 报 `input_too_large` 时调大 |
| `output.dir` | `output` | 敏感录音建议指到加密卷 |

## 模型来源

五级顺序回退，命中即止、不重复下载：

`explicit_path`（`--model-path` 或 `FASTER_WHISPER_MODEL_DIR`，完全不触网）→ `local_cache` → `modelscope` → `hf_mirror` → `hf_official`

默认仓库 `Systran/faster-whisper-base`，约 145MB。国内网络**无需配置代理**。同时识别 faster-whisper 与 ModelScope 两种缓存布局。允许的 / 禁止的来源清单见 `source_boundary.md` §一。

## 测试

```bash
pip install -r ../requirements-dev.txt -i https://mirrors.aliyun.com/pypi/simple/
cd .. && python3 -m pytest tests/ -q      # 353 passed + 2 skipped，约 0.4 秒
```

`requirements-dev.txt` **刻意不继承** `requirements.txt`：`faster-whisper` / `ctranslate2` / `modelscope` / `huggingface_hub` 在 `tests/` 内全部以桩件替代。好处有三——CI 十几秒跑完；契约测试本来不需要真引擎；**只有在不装重依赖的环境里，「缺依赖必须报 `dependency_missing`」这条负路径才天然可测**。

想跑一次真实转录，再单独装 `requirements.txt`。
