# 多平台安装指南 / Multi-Platform Install Guide

本仓库包含一个离线音视频转写 skill：

- `skill-video-2-text`：用本地 faster-whisper 把音频/视频转成带时间戳的原始语料，标点、分段、摘要交给调用方大模型完成。

**Credentials: None.** 本 skill 无需任何账号、无需 API key、不调用任何云端语音识别接口。音频全程不离开本机。

要求：Python 3.10+。运行期依赖见 `requirements.txt`；跑测试只需 `requirements-dev.txt`（pytest + PyYAML）。

---

## Claude Code

把整个仓库目录复制到 Claude Code 的 skills 目录：

```bash
# macOS / Linux
cp -R skill-video-2-text ~/.claude/skills/
cd ~/.claude/skills/skill-video-2-text
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
```

```powershell
# Windows (PowerShell)
New-Item -ItemType Directory -Force "$HOME\.claude\skills"
Copy-Item ".\" "$HOME\.claude\skills\skill-video-2-text" -Recurse -Force
```

安装后在 Claude Code 中调用：

```
/skill-video-2-text
```

也可由任意 agent 通过 Python import 调用：

```python
import sys
sys.path.insert(0, "~/.claude/skills/skill-video-2-text")
from scripts.transcribe import run, validate_input, InputValidationError
```

---

## Codex

`agents/openai.yaml` 是一份 OpenAPI 3.1 工具定义，可直接作为 Codex 的 function/tool 声明加载。核心工具签名如下：

```json
{
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "skill_video_to_text",
        "description": "Offline speech-to-text for a local audio or video file. Returns exactly one JSON object on stdout containing timestamped raw segments; progress and download logs go to stderr. Punctuation, paragraphing and summarization are left to the calling LLM. No account, no API key, no cloud speech API.",
        "parameters": {
          "type": "object",
          "properties": {
            "input_path": {
              "type": "string",
              "description": "Absolute path to a local audio or video file."
            },
            "language": {
              "type": "string",
              "description": "BCP-47 code such as zh or en. Omit to auto-detect; specifying it is faster and more accurate."
            },
            "model_size": {
              "type": "string",
              "enum": ["tiny", "base", "small", "medium", "large-v3"],
              "description": "Whisper model size. Only base is end-to-end verified."
            },
            "output_dir": {
              "type": "string",
              "description": "Directory for the three artifacts. Relative paths resolve against the skill root."
            }
          },
          "required": ["input_path"]
        }
      }
    }
  ]
}
```

本地直接运行：

```bash
cd skill-video-2-text
python3 scripts/env_check.py --self-check        # 退出 0=就绪 1=阻塞 2=仅告警
python3 scripts/transcribe.py ~/Downloads/meeting.m4a --language zh
python3 scripts/validate.py output/meeting.transcript.json --strict
```

---

## Cursor

### 作为 Cursor Rule

仓库已附带 `agents/cursor-rule.mdc`。复制到项目的 `.cursor/rules/` 即可：

```bash
mkdir -p .cursor/rules
cp skill-video-2-text/agents/cursor-rule.mdc .cursor/rules/
```

其 frontmatter 为：

```yaml
description: 音频/视频转文字 — 本地 faster-whisper 转录 + 大模型后处理（纠错/标点/分段/摘要），全程离线，无需账号
globs: []
alwaysApply: false
```

### 作为 Cursor Tool

```json
{
  "name": "video-to-text",
  "description": "Offline local audio/video transcription via faster-whisper",
  "command": "python3",
  "args": [
    "${WORKSPACE}/skill-video-2-text/scripts/transcribe.py",
    "${input_path}",
    "--language", "${language}",
    "--output-dir", "${WORKSPACE}/skill-video-2-text/output"
  ],
  "env": {}
}
```

`env` 刻意为空对象：本 skill 不读取任何凭据类环境变量。

---

## Hermes

在 Hermes skill 注册表中登记：

```yaml
skills:
  video-to-text:
    name: skill-video-2-text
    type: utility
    runtime: python3
    entry: skill-video-2-text/scripts/transcribe.py
    description: >
      Offline speech-to-text for local audio and video files using
      faster-whisper. Emits timestamped raw segments as a single JSON
      object on stdout; punctuation, paragraphing and summarization are
      performed by the calling LLM.
    input_schema:
      type: object
      properties:
        input_path:
          type: string
          description: "Absolute path to a local audio/video file"
        language:
          type: string
          description: "BCP-47 code such as zh or en; omit to auto-detect"
        model_size:
          type: string
          enum: [tiny, base, small, medium, large-v3]
        output_dir:
          type: string
      required: [input_path]
    constraints:
      - Python 3.10+ required
      - no credentials required
      - transcripts under output/ are sensitive; never commit them
      - research and educational use only
```

调用：

```python
response = hermes.call_skill("video-to-text", {
    "input_path": "/abs/path/meeting.m4a",
    "language": "zh",
})
# response 为唯一一个 JSON 对象：{"ok": true, "segments": [...], "outputs": {...}}
```

---

## OpenClaw

```yaml
- name: video_to_text
  description: >
    Offline transcription of local audio and video files with faster-whisper.
    Five-tier model resolution (explicit path, local cache, ModelScope,
    hf-mirror, huggingface.co) so the first run works behind the GFW.
  type: local
  runtime: python3
  entrypoint: video-to-text/scripts/transcribe.py
  schema:
    input:
      type: object
      required: [input_path]
      properties:
        input_path: {type: string, description: "Local audio/video file path"}
        language: {type: string, description: "zh / en / ... omit to auto-detect"}
        model_size: {type: string, default: "base"}
        model_path: {type: string, description: "Explicit local model directory"}
        initial_prompt: {type: string, description: "Domain hint to improve proper nouns"}
        output_dir: {type: string, default: "output"}
        no_write: {type: boolean, default: false}
  env: {}
  exit_codes:
    0: success
    1: runtime failure, see error.code
    2: missing or illegal argument
    130: interrupted by user
  notes: |
    No credentials. Audio never leaves the machine.
    stdout is exactly one JSON object; all logs go to stderr.
    Research and educational use only.
```

---

## 环境准备（所有平台通用）

**无需任何账号或 API key。** 只有三个无害的环境变量可选：

| 变量 | 作用 |
|---|---|
| `HF_ENDPOINT` | 覆盖 HuggingFace 端点；默认按配置走 hf-mirror.com |
| `FASTER_WHISPER_MODEL_DIR` | 显式指定本地模型目录，命中则完全不下载 |
| `FASTER_WHISPER_CACHE_ROOT` | 覆盖模型缓存根目录，默认 `~/.cache/faster-whisper-models` |

安装与自检：

```bash
cd skill-video-2-text

# 运行期依赖（国内建议走镜像）
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

# 环境自检：退出 0=就绪 1=阻塞 2=仅告警仍可用
python3 scripts/env_check.py --self-check --json
```

首次转录会自动下载 `Systran/faster-whisper-base`（约 145MB），顺序为 ModelScope（国内无需代理）→ hf-mirror.com → huggingface.co，命中本地缓存即不重复下载。

跑测试**不需要**装运行期重依赖：

```bash
pip install -r requirements-dev.txt -i https://mirrors.aliyun.com/pypi/simple/
python3 -m pytest tests/ -q          # 353 passed + 2 skipped，约 0.4 秒
```

系统 ffmpeg 为可选项：PyAV 自带静态链接的 ffmpeg 解码库，多数情况够用。自检把缺 ffmpeg 判为告警而非阻塞，只有真的返回 `decode_failed` 时才需要安装：

```bash
brew install ffmpeg          # macOS
winget install ffmpeg        # Windows
sudo apt install ffmpeg      # Debian / Ubuntu
```

---

## 使用边界（所有平台通用）

- **仅供研究与教育用途** —— 不构成投资、法律或医疗建议。
- **转录不完美**：任何正式用途（合同、医疗、法律、财务、对外发布）之前必须人工复核。
- **录音可能受法律约束**：录制他人语音在多数司法辖区需要授权，请先确认合法性。
- **转录稿是敏感明文**：`output/` 已 gitignore，不要提交、不要上传第三方服务（包括粘贴进在线 LLM）。
- 不支持说话人分离、实时流式、热词表。详见 `SKILL.md` §「已知局限」。
- 架构与输出契约见 `SKILL.md`，项目概览见 `README.md` / `README.en.md`。
