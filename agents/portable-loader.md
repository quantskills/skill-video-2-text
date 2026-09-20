# skill-video-2-text

Offline speech-to-text for local audio and video files.

## Overview

A local transcription pipeline that turns a recording into a readable, searchable, quotable Markdown transcript. Stage 1 is a script: faster-whisper converts sound into timestamped raw segments. Stage 2 is the calling LLM: fix homophones, add punctuation, re-paragraph by semantic topic, write a summary. Everything runs offline — no cloud speech API, no account, no API key.

Suited to privacy-sensitive recordings (meetings, interviews, clinical consultations) precisely because the audio never leaves the machine.

## Architecture

```
env_check  →  validate_input  →  model_utils  →  WhisperModel  →  validate.py  →  LLM post-processing
(doctor)      (format guard)    (4-tier fetch)   (transcribe)     (contract gate)  (punctuation/summary)
                                                       ↓
                                    stdout: ONE JSON object
                                    output/: .transcript.json + .transcript.txt + .timeline.txt
```

Stage 1 deliberately does **no** semantic cleanup. Filler words, missing punctuation and breath-based segment boundaries are all preserved — the inter-segment gaps are evidence the LLM needs to place sentence breaks, and deleting them destroys that signal.

## Quick Start

```bash
# Install runtime deps (mainland China: use a mirror, pypi.org often times out)
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

# Environment self-check — exit 0 ready / 1 blocked / 2 warnings only
python3 scripts/env_check.py --self-check

# Transcribe
python3 scripts/transcribe.py ~/Downloads/meeting.m4a --language zh

# Validate the artifact against the output contract (do not skip)
python3 scripts/validate.py output/meeting.transcript.json --strict

# Run the test suite — needs only pytest + PyYAML, faster-whisper is stubbed out
pip install -r requirements-dev.txt -i https://mirrors.aliyun.com/pypi/simple/
python3 -m pytest tests/ -q
```

The first transcription run downloads `Systran/faster-whisper-base` (~145MB), preferring ModelScope (no proxy needed in China), then hf-mirror.com, then huggingface.co.

## Agent Integration

### Via subprocess

```python
import json
import subprocess
from pathlib import Path

skill_root = Path("/path/to/skill-video-2-text")

proc = subprocess.run(
    ["python3", str(skill_root / "scripts" / "transcribe.py"),
     str(audio_file), "--language", "zh", "--output-dir", str(out_dir)],
    capture_output=True, text=True, cwd=str(skill_root),
)

# stdout is EXACTLY ONE JSON object — progress and download logs go to stderr.
result = json.loads(proc.stdout)

if not result["ok"]:
    err = result["error"]
    # err["code"] is machine-actionable; err["hint"] is a ready-to-run fix command.
    raise RuntimeError(f'{err["code"]}: {err["message"]}\n{err["hint"]}')

for seg in result["segments"]:
    print(f'[{seg["start"]:.1f}-{seg["end"]:.1f}] {seg["text"]}')
```

### Via Python import

```python
import sys
sys.path.insert(0, "/path/to/skill-video-2-text")

from scripts.transcribe import run, validate_input, InputValidationError

try:
    info = validate_input("~/Downloads/meeting.m4a")   # cheap pre-flight, no model loaded
    print(info["kind"], info["ext"], info["size_bytes"])

    result = run(info["path"], language="zh", output_dir="./out")
    print(result["segment_count"], result["outputs"])
except InputValidationError as exc:
    print(exc.code, exc.message, exc.hint, sep="\n")
```

### Environment doctor

```python
from scripts.env_check import self_check

report = self_check()
# report["status"] in {"ready", "degraded", "blocked"}
# report["summary"]["blockers"] / ["warnings"] — names of failing checks
# report["checks"] — grouped items, each with {name, status, found, hint}
```

### Output validator

```python
from scripts.validate import validate_file

report = validate_file(Path("output/meeting.transcript.json"), strict=True)
# report["status"] in {"PASS", "FAIL"}
# report["errors"] / ["warnings"] / ["stats"]
```

## Input Formats

| Kind | Extensions |
|---|---|
| Audio | `.mp3` `.wav` `.m4a` `.flac` `.ogg` `.aac` `.wma` `.opus` |
| Video | `.mp4` `.mov` `.mkv` `.avi` `.webm` `.flv` `.wmv` `.m4v` |

Video audio tracks are extracted automatically by PyAV — no manual ffmpeg conversion. Extension is the first gate; actual decode capability is decided by PyAV/ffmpeg.

Input guards: exists → is a regular file → supported extension → readable → non-empty → under `input.max_size_mb` (default 4096).

## Output Contract

**stdout is exactly one JSON object.** Progress, model-download logs and dependency warnings all go to stderr. This is enforced by unit tests and CI.

Success:

```json
{
  "ok": true,
  "source": {"path": "/abs/meeting.m4a", "name": "meeting.m4a", "ext": ".m4a", "kind": "audio", "size_bytes": 9876543},
  "model_size": "base",
  "language": "zh",
  "language_probability": 0.99,
  "duration": 612.3,
  "segment_count": 2,
  "segments": [{"start": 0.0, "end": 3.2, "text": "..."}, {"start": 3.2, "end": 7.8, "text": "..."}],
  "outputs": {"json": "...", "plain_text": "...", "timeline": "..."}
}
```

Failure:

```json
{"ok": false, "error": {"code": "decode_failed", "message": "...", "hint": "install ffmpeg: brew install ffmpeg / winget install ffmpeg / sudo apt install ffmpeg"}}
```

Error codes: `missing_argument`, `input_not_found`, `input_not_a_file`, `unsupported_format`, `input_unreadable`, `input_empty`, `input_too_large`, `dependency_missing`, `model_load_failed`, `decode_failed`, `transcribe_failed`, `interrupted`.

Exit codes: `0` success, `1` runtime failure, `2` missing argument, `130` interrupted.

### Artifacts

| File | Content |
|---|---|
| `output/<name>.transcript.json` | The full result object — the single handoff artifact between stage 1 and stage 2 |
| `output/<name>.transcript.txt` | Segments joined by newline, no timestamps |
| `output/<name>.timeline.txt` | `[mm:ss - mm:ss] text` per line — subtitle draft material |

## Blocking Policy

The line between "must stop" and "just warn" is deliberate and test-guarded:

| Check | Missing → | Why |
|---|---|---|
| Python ≥ 3.10 | **blocker** (exit 1) | Cannot run at all |
| faster-whisper / ctranslate2 | **blocker** | Cannot transcribe |
| Model weights | **blocker** | Nothing to run inference with |
| ffmpeg (system) | warning (exit 2) | PyAV bundles statically-linked ffmpeg libraries; usually sufficient |
| modelscope / huggingface_hub | warning | Only affects download channels; irrelevant once the model is cached |
| PyYAML | warning | Config silently degrades to built-in defaults |

A missing ffmpeg must **not** halt the flow. Attempt transcription first; only escalate to an install prompt if it actually returns `decode_failed`.

## Model Sources

Tried in order, first hit wins:

1. `--model-path` / `FASTER_WHISPER_MODEL_DIR` — no download at all
2. Local cache — both `<cache_root>/<size>/` **and** `<cache_root>/_ms_cache/models/<owner>--<repo>/snapshots/<revision>/`
3. ModelScope — `pengzhendong` → `keepitsimple` → `Systran`
4. hf-mirror.com
5. huggingface.co

A directory counts as a usable model when it has `model.bin` + `config.json` + `tokenizer.json` + one of `vocabulary.txt`/`vocabulary.json`. `preprocessor_config.json` is optional (faster-whisper ships its own log-mel feature extractor).

Env overrides: `FASTER_WHISPER_MODEL_DIR`, `FASTER_WHISPER_CACHE_ROOT`.

## Tests

```bash
pytest tests/ -q   # 353 passed + 2 skipped, ~0.4s
```

Heavy dependencies (faster-whisper, ctranslate2, modelscope, huggingface_hub) are **entirely stubbed**, so the whole contract suite runs with only pytest + PyYAML installed. Ten files:

| File | Covers |
|---|---|
| `test_validate_input.py` | Format/existence/size/permission guards |
| `test_model_discovery.py` | Model location across both cache layouts + manual-download hint accuracy |
| `test_hf_endpoint.py` | hf-mirror endpoint switching on already-imported modules |
| `test_stdio_contract.py` | stdout purity, exit codes, structured errors |
| `test_transcribe_core.py` | kwarg assembly, device detection, error classification, `run()` contract |
| `test_validate.py` | Output contract + "validator must never crash on malformed input" |
| `test_config.py` | Deep merge, four-level degradation, YAML/built-in drift guard |
| `test_render.py` | Timestamp formatting and the three artifacts |
| `test_env_check.py` | The blocker-vs-warning decision line |
| `test_contract_files.py` | Release contract: every YAML/JSON file and embedded snippet parses, error codes agree across code + SKILL.md + openai.yaml, GPLv3/INSTALL/disclaimer compliance, zero-credential guarantee |

Five of these files are **regression guards for defects found during v0.1** — see `CHANGELOG.md` § Fixed.

## Configuration

`config/transcribe_config.yaml` centralises model / download / decode / input / output parameters. Precedence: CLI args > env vars > YAML > built-in defaults.

Notable defaults and why:

| Key | Value | Reason |
|---|---|---|
| `transcribe.temperature` | `0.0` | Greedy decoding — transcription must be reproducible, not creative |
| `transcribe.condition_on_previous_text` | `false` | Reduces repetition hallucination on long audio |
| `transcribe.vad_filter` | `true` | Skips silence and background noise |
| `model.compute_type` | `int8` | Fastest on CPU; switch to `float32` if `model_load_failed` occurs |
| `model.local_files_only` | `true` (hardcoded) | Never touch the network during transcription |

If PyYAML is missing, the config silently falls back to built-in defaults that are byte-identical to the YAML — a drift-guard test enforces this.

## Dependencies

- **Runtime**: Python 3.10+, faster-whisper ≥ 1.0, ctranslate2 ≥ 4.0, av ≥ 11.0, PyYAML ≥ 6.0, modelscope ≥ 1.9, huggingface_hub ≥ 0.23
- **Tests**: pytest, PyYAML (runtime deps **not** required)
- **Optional**: system ffmpeg (only on `decode_failed`); node (only to run `validate-qsh-form.mjs`)
- **Credentials**: none. This skill reads no `*_USERNAME`, `*_TOKEN` or `*_API_KEY`.

## Safety & Boundaries

- Audio never leaves the machine; no cloud speech API is called.
- Transcripts under `output/` are plaintext and sensitive — gitignored; do not commit or upload them (including pasting into an online LLM, which forfeits the entire privacy benefit).
- Transcription is imperfect. Human review is mandatory before any formal use (contractual, medical, legal, financial, published content).
- Recording other people's speech may be regulated in your jurisdiction; confirm lawful authority first.
- No speaker diarization, no real-time streaming, no hotword vocabulary. See `SKILL.md` § 已知局限.
- Research and educational use only. Not investment, legal, or medical advice.
