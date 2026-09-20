# skill-video-2-text

把本地音频/视频离线转写为带时间戳的原始语料。**第 1 阶段**脚本用 faster-whisper 出稿，**第 2 阶段**由调用方大模型纠错、加标点、按语义分段、写摘要。

零凭据、零云端 API、音频不离开本机。适合会议记录、访谈整理、课程笔记、字幕初稿，尤其是隐私敏感的录音（问诊、法律、人事）。

## ⚠️ 免责声明

- **仅供研究与教育用途**：本 skill 是一个本地语音转写工具，不构成任何形式的投资、法律或医疗建议。
- **不保证准确性**：自动语音识别存在固有错误率，同音字、专有名词、口音、背景噪声都会导致误识。任何正式用途（合同、医疗记录、法律证据、财务凭证、对外发布）之前**必须人工逐字复核**；未经复核的转录稿不得作为事实依据。
- **风险边界**：本工具不做说话人分离，无法区分"谁在说"；不做实时流式转录；不感知音频质量，静音、削波、串音会得到空或错乱的 `segments`。转录稿是**明文敏感数据**，其敏感度不低于原始录音——`output/` 已 gitignore，不要提交到版本库、不要上传第三方服务（包括粘贴进在线 LLM，那等于放弃本地转录的全部隐私优势）。
- **合规责任在使用者**：录制他人语音在多数司法辖区需要事先授权，处理问诊、法律、人事类录音还受额外保密义务约束。是否有权转录、转录后如何保管，由使用者自行确认并承担全部责任。
- **非官方背书**：本项目为 QuantSkills 社区项目，与 OpenAI、Hugging Face、ModelScope、Systran 均无关联，未经专业审计或任何监管机构认证。模型权重按其各自原始许可条款分发。

## 目录结构

```
├── SKILL.md                                ← 技能说明书（含错误码表、退出码、qsh-form 表单块）
├── README.md                               ← 本文件
├── README.en.md                            ← English version
├── LICENSE                                 ← GPL-3.0 全文
├── INSTALL.md                              ← 五平台安装指南
├── CHANGELOG.md                            ← Keep a Changelog + SemVer
├── skill.json                              ← Skill 元数据（form / entry / runtime / auth_required）
├── requirements.txt                        ← 运行期依赖
├── requirements-dev.txt                    ← 测试依赖（刻意不继承运行期依赖）
├── config/
│   └── transcribe_config.yaml              ← 模型/下载/解码/输入/输出五节超参外置
├── scripts/
│   ├── transcribe.py                       ← 主入口 CLI，含 run() + validate_input() + InputValidationError
│   ├── model_utils.py                      ← 模型发现与获取，五级顺序回退
│   ├── env_check.py                        ← 运行环境自检，--self-check [--json]
│   ├── config.py                           ← 四级配置优先级，PyYAML 缺失时降级
│   ├── validate.py                         ← 输出契约校验器，--strict
│   └── validate-qsh-form.mjs               ← SKILL.md 表单块校验器
├── tests/                                  ← 11 个文件；353 passed + 2 skipped，重依赖全部桩替换
│   └── fixtures/                           ← sample_zh.wav + 生成脚本
├── agents/
│   ├── openai.yaml                         ← OpenAPI 3.1 工具定义（x-steps / x-error-codes / x-model-sources）
│   ├── cursor-rule.mdc                     ← Cursor rule
│   └── portable-loader.md                  ← 通用可移植加载说明
├── references/
│   ├── api_guide.md                        ← 总入口索引
│   ├── data_guide.md                       ← 输入格式指南（支持格式、好结果的条件、常见陷阱）
│   ├── methodology.md                      ← 为什么是"脚本转录 + 大模型后处理"两阶段
│   ├── need_used_api.md                    ← 依赖接口清单与对外契约
│   └── source_boundary.md                  ← 模型来源与输入来源的边界
└── .github/workflows/validate.yml          ← CI：qsh-form + pytest + 5 步负路径反向断言
```

## 快速开始

```bash
# 1. 安装运行期依赖（国内务必走镜像，pypi.org 直连经常超时）
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

# 2. 环境自检 —— 退出 0=就绪 1=阻塞 2=仅告警仍可用
python3 scripts/env_check.py --self-check --json

# 3. 转录
python3 scripts/transcribe.py ~/Downloads/meeting.m4a --language zh

# 4. 校验产物契约（不要跳过，见下文）
python3 scripts/validate.py output/meeting.transcript.json --strict

# 5. 由大模型做后处理，写出 meeting.transcript.md
```

跑单元测试**不需要**装 faster-whisper（353 例全部走桩件）：

```bash
pip install -r requirements-dev.txt -i https://mirrors.aliyun.com/pypi/simple/
python3 -m pytest tests/ -q          # 约 1 秒
```

首次转录会自动下载 `Systran/faster-whisper-base`（约 145MB）。

## 核心设计要点

1. **两阶段，中间不做任何清理**：语音模型负责「声音 → 带时间戳的粗糙文字」，大模型负责「粗糙文字 → 可读文稿」。第 1 阶段刻意保留语气词、缺失的标点、按换气停顿切分的 segment——**段间时间间隔是第 2 阶段判断句读的证据**，删掉就没了。详见 `references/methodology.md`。

2. **stdout 有且仅有一个 JSON 对象**：进度、模型下载日志、依赖告警全部走 stderr。机器消费方可以放心 `json.loads(stdout)`。这条契约由 `test_stdio_contract.py` 与 CI 负路径断言双重把守——它曾是 v0.1 的真实缺陷。

3. **失败返回可机读的 `error.code`**：12 个错误码构成稳定枚举，每个都带 `message`（给人看）与 `hint`（给可直接执行的修复命令）。调用方读 `code` 分支处理，不要贴 traceback。

4. **模型五级回退**：`explicit_path` → `local_cache` → `modelscope` → `hf_mirror` → `hf_official`，命中即不重复下载。国内网络无需任何代理配置。同时识别 faster-whisper 与 ModelScope 两种缓存布局。

5. **阻塞与告警刻意分线**：Python ≥ 3.10、faster-whisper / ctranslate2、模型权重缺失为**阻塞项**（退出 1）；系统 ffmpeg、modelscope / huggingface_hub、PyYAML 缺失仅为**告警**（退出 2）。PyAV 自带静态链接的 ffmpeg，缺它不该在转录前就拦住用户——先试，真的返回 `decode_failed` 再提示安装。

6. **第 4 步不能跳过**：拿一份 `segments` 为空或时间戳错乱的 JSON 去做后处理，大模型会写出一份**格式完美、语气通顺、内容全是编的**文档。这比直接报错危险得多。`validate.py` 报 FAIL 时必须停下来。

7. **零凭据，有 AST 级断言**：`scripts/` 不读取任何 `*_USERNAME` / `*_TOKEN` / `*_API_KEY`，只读 `HF_ENDPOINT` / `FASTER_WHISPER_MODEL_DIR` / `FASTER_WHISPER_CACHE_ROOT` 三个无害变量。`test_contract_files.py` 遍历 AST 精确断言这一点，防止后续改动偷偷引入凭据依赖。

## 支持的运行时平台

| 平台 | 安装指南 |
|---|---|
| Claude Code | `INSTALL.md` § Claude Code |
| Codex (OpenAI) | `INSTALL.md` § Codex |
| Cursor | `INSTALL.md` § Cursor |
| Hermes | `INSTALL.md` § Hermes |
| OpenClaw | `INSTALL.md` § OpenClaw |

## 验收状态

- `scripts/transcribe.py` 独立可运行，`run()` 可 import ✅
- 全绿：353 passed + 2 skipped（pytest 9.x / Python 3.11，约 0.4 秒）。两例跳过分别是「校验器与标杆仓库同源」比对（标杆不在本地时自动跳过）与 `tests/test_model_reliability.py` 整个文件——后者是 opt-in 的真模型集成测试，见下条 ✅
- 模型可靠性集成测试（13 例，opt-in）：`V2T_RUN_MODEL_TESTS=1 python3 -m pytest tests/test_model_reliability.py -v`。需先 `pip install -r requirements.txt` 且 venv 为 Python ≥ 3.10。实测 13 passed / 约 5 秒，覆盖确定性、语种置信度、关键术语覆盖率、视频解封装、时间轴完整性、权重缓存复用六类 ✅
- 重依赖全部桩替换，测试只需 pytest + PyYAML，约 1 秒跑完 ✅
- stdout 纯 JSON 契约、12 个错误码在「代码 ↔ SKILL.md ↔ openai.yaml」三方一致 ✅
- `env_check.py --self-check` 阻塞项如实报告 `blocked` 并非 0 退出 ✅
- `validate.py` 能拒绝畸形产物且不自身崩溃 ✅
- 零凭据（AST 级断言）✅
- CI 含 5 步负路径反向断言 ✅

## 局限与后续优化方向

| 局限 | 说明 | 后续 |
|---|---|---|
| 仅 `base` 模型端到端验证 | `tiny`/`small`/`medium`/`large-v3` 参数已适配但未实跑验证 | v0.2 补端到端验证 |
| 无说话人分离 | 输出不含"谁在说"，多人对话需人工区分 | 评估 pyannote 集成 |
| 无实时流式 | 只支持整文件转录，不支持边说边出字 | 需求驱动 |
| 无热词表 | 专有名词只能靠 `--initial-prompt` 软引导 | 评估 vocabulary 偏置 |
| 音乐/歌唱转录效果差 | Whisper 面向语音训练，歌唱会产出幻觉歌词 | 不在范围内 |
| 长音频受内存限制 | 数小时录音一次性载入，CPU int8 下耗时长 | v0.2 增加分块转录 |

## 依赖

- Python 3.10+
- faster-whisper ≥ 1.0、ctranslate2 ≥ 4.0（转录核心）
- av ≥ 11.0（视频音轨解码）
- PyYAML ≥ 6.0（配置加载，缺失时降级到内置默认值）
- modelscope ≥ 1.9 或 huggingface_hub ≥ 0.23（首次下载模型）
- 可选：系统 ffmpeg（仅在返回 `decode_failed` 时）、node（仅跑 `validate-qsh-form.mjs`）
- 测试：pytest、PyYAML（**不需要**运行期重依赖）
- **凭据：无**

## License

GPL-3.0 — 详见 [LICENSE](LICENSE)
