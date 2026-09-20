# skill-video-2-text

Offline speech-to-text for local audio and video files. **Stage 1** is a script: faster-whisper turns sound into timestamped raw segments. **Stage 2** is the calling LLM: fix homophones, add punctuation, re-paragraph by semantic topic, write a summary.

No credentials, no cloud speech API, audio never leaves the machine. Suited to meeting notes, interview transcripts, lecture notes and subtitle drafts — especially privacy-sensitive recordings (clinical consultations, legal, HR).

## ⚠️ Disclaimer

- **Research and educational use only**: this skill is a local speech-to-text utility. It does NOT constitute investment, legal, or medical advice of any kind.
- **No accuracy guarantee**: automatic speech recognition has an inherent error rate. Homophones, proper nouns, accents and background noise all cause misrecognition. Human word-by-word review is **mandatory** before any formal use (contracts, medical records, legal evidence, financial documents, published content). An unreviewed transcript must not be treated as a factual record.
- **Risk boundaries**: no speaker diarization — the output cannot tell you *who* said what. No real-time streaming. No awareness of audio quality: silence, clipping and crosstalk yield empty or scrambled `segments`. Transcripts are **sensitive plaintext**, no less sensitive than the original recording — `output/` is gitignored; do not commit it, do not upload it to third-party services (including pasting it into an online LLM, which forfeits the entire privacy benefit of local transcription).
- **Compliance is the user's responsibility**: recording other people's speech requires prior authorization in most jurisdictions, and clinical / legal / HR recordings carry additional confidentiality duties. Whether you may transcribe, and how you must store the result, is for you to confirm and bear.
- **No official endorsement**: this is a QuantSkills community project. It is not affiliated with OpenAI, Hugging Face, ModelScope, or Systran, and has not been professionally audited or certified by any regulatory body. Model weights are distributed under their own original license terms.

## Directory Structure

```
├── SKILL.md                                ← Skill specification (error-code table, exit codes, qsh-form block)
├── README.md                               ← Chinese README
├── README.en.md                            ← This file
├── LICENSE                                 ← Full GPLv3 text
├── INSTALL.md                              ← Five-platform install guide
├── CHANGELOG.md                            ← Keep a Changelog + SemVer
├── skill.json                              ← Skill metadata (form / entry / runtime / auth_required)
├── requirements.txt                        ← Runtime dependencies
├── requirements-dev.txt                    ← Test dependencies (deliberately does NOT inherit runtime deps)
├── config/
│   └── transcribe_config.yaml              ← Model / download / decode / input / output parameters
├── scripts/
│   ├── transcribe.py                       ← Main CLI: run() + validate_input() + InputValidationError
│   ├── model_utils.py                      ← Model discovery and fetch, five-tier fallback
│   ├── env_check.py                        ← Environment doctor, --self-check [--json]
│   ├── config.py                           ← Four-level config precedence, degrades without PyYAML
│   ├── validate.py                         ← Output-contract validator, --strict
│   └── validate-qsh-form.mjs               ← SKILL.md form-block validator
├── tests/                                  ← 11 files; 353 passed + 2 skipped, heavy deps fully stubbed
│   └── fixtures/                           ← sample_zh.wav + generation script
├── agents/
│   ├── openai.yaml                         ← OpenAPI 3.1 tool definition (x-steps / x-error-codes / x-model-sources)
│   ├── cursor-rule.mdc                     ← Cursor rule
│   └── portable-loader.md                  ← Portable loader guide
├── references/
│   ├── api_guide.md                        ← Entry-point index
│   ├── data_guide.md                       ← Input format guide (supported formats, what makes a good result, pitfalls)
│   ├── methodology.md                      ← Why "script transcribes + LLM post-processes"
│   ├── need_used_api.md                    ← Dependency interfaces and the exposed contract
│   └── source_boundary.md                  ← Model-source and input-source boundaries
└── .github/workflows/validate.yml          ← CI: qsh-form + pytest + 5 negative-path assertions
```

## Quick Start

```bash
# 1. Install runtime deps (mainland China: use a mirror, pypi.org often times out)
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

# 2. Environment self-check — exit 0 ready / 1 blocked / 2 warnings only
python3 scripts/env_check.py --self-check --json

# 3. Transcribe
python3 scripts/transcribe.py ~/Downloads/meeting.m4a --language zh

# 4. Validate the artifact against the output contract (do not skip)
python3 scripts/validate.py output/meeting.transcript.json --strict

# 5. The LLM does post-processing and writes meeting.transcript.md
```

Running the test suite does **not** require faster-whisper (all 353 cases use stubs):

```bash
pip install -r requirements-dev.txt -i https://mirrors.aliyun.com/pypi/simple/
python3 -m pytest tests/ -q          # about one second
```

The first transcription run downloads `Systran/faster-whisper-base` (~145MB).

## Core Design

1. **Two stages, no cleanup in between.** The acoustic model does *sound → timestamped rough text*; the LLM does *rough text → readable document*. Stage 1 deliberately preserves filler words, missing punctuation and breath-based segment boundaries — **the inter-segment gaps are the evidence stage 2 needs to place sentence breaks**, and deleting them destroys that signal. See `references/methodology.md`.

2. **stdout is exactly one JSON object.** Progress, model-download logs and dependency warnings all go to stderr, so `json.loads(stdout)` is always safe. Guarded by both `test_stdio_contract.py` and a CI negative-path assertion — this was a real v0.1 defect.

3. **Failures return a machine-readable `error.code`.** Twelve codes form a stable enum, each carrying a `message` (for humans) and a `hint` (a ready-to-run fix command). Branch on `code`; do not paste tracebacks.

4. **Five-tier model resolution.** `explicit_path` → `local_cache` → `modelscope` → `hf_mirror` → `hf_official`, first hit wins, so a cached model is never re-downloaded. Works behind the GFW with no proxy configuration, and recognizes both the faster-whisper and the ModelScope cache layouts.

5. **The blocker/warning line is deliberate.** Missing Python ≥ 3.10, faster-whisper / ctranslate2, or model weights are **blockers** (exit 1). Missing system ffmpeg, modelscope / huggingface_hub, or PyYAML are only **warnings** (exit 2). PyAV bundles statically-linked ffmpeg libraries, so a missing ffmpeg must not halt the flow — attempt transcription first, and only escalate to an install prompt on an actual `decode_failed`.

6. **Step 4 must not be skipped.** Feeding an empty or timestamp-scrambled `segments` array to the LLM produces a document that is *perfectly formatted, fluently worded, and entirely fabricated*. That is far more dangerous than an outright error. Stop whenever `validate.py` reports FAIL.

7. **Zero credentials, asserted at AST level.** `scripts/` reads no `*_USERNAME`, `*_TOKEN` or `*_API_KEY` — only the three harmless variables `HF_ENDPOINT`, `FASTER_WHISPER_MODEL_DIR` and `FASTER_WHISPER_CACHE_ROOT`. `test_contract_files.py` walks the AST to enforce this, so a future change cannot silently introduce a credential dependency.

## Supported Runtimes

| Platform | Install Guide |
|---|---|
| Claude Code | `INSTALL.md` § Claude Code |
| Codex (OpenAI) | `INSTALL.md` § Codex |
| Cursor | `INSTALL.md` § Cursor |
| Hermes | `INSTALL.md` § Hermes |
| OpenClaw | `INSTALL.md` § OpenClaw |

## Verification Status

- `scripts/transcribe.py` runs standalone; `run()` is importable ✅
- All green: 353 passed + 2 skipped (pytest 9.x / Python 3.11, ~0.4s). The two skips are the "validator byte-identical to benchmark" check (self-skips when the benchmark repo is absent) and the whole of `tests/test_model_reliability.py` — an opt-in real-model integration suite, see next bullet ✅
- Model-reliability integration tests (13 cases, opt-in): `V2T_RUN_MODEL_TESTS=1 python3 -m pytest tests/test_model_reliability.py -v`. Requires `pip install -r requirements.txt` in a Python >= 3.10 venv. Measured: 13 passed in ~5s, covering determinism, language-detection confidence, keyword coverage against ground truth, video demuxing, timeline integrity and weight-cache reuse ✅
- Heavy deps fully stubbed — the suite needs only pytest + PyYAML and runs in ~1s ✅
- stdout purity, and the 12 error codes agree across code ↔ SKILL.md ↔ openai.yaml ✅
- `env_check.py --self-check` honestly reports `blocked` and exits non-zero ✅
- `validate.py` rejects malformed transcripts without crashing ✅
- Zero credentials (AST-level assertion) ✅
- CI includes 5 negative-path assertions ✅

## Limitations & Future Work

| Limitation | Detail | Plan |
|---|---|---|
| Only `base` verified end-to-end | `tiny`/`small`/`medium`/`large-v3` are wired up but not E2E tested | v0.2 |
| No speaker diarization | Output does not say who spoke; multi-speaker audio needs manual attribution | Evaluate pyannote |
| No real-time streaming | Whole-file transcription only | Demand-driven |
| No hotword vocabulary | Proper nouns can only be nudged via `--initial-prompt` | Evaluate vocabulary biasing |
| Poor on music/singing | Whisper is trained on speech; singing yields hallucinated lyrics | Out of scope |
| Long audio is memory-bound | Hours-long recordings load at once; slow on CPU int8 | v0.2 chunked transcription |

## Dependencies

- Python 3.10+
- faster-whisper ≥ 1.0, ctranslate2 ≥ 4.0 (transcription core)
- av ≥ 11.0 (video audio-track decoding)
- PyYAML ≥ 6.0 (config loading; degrades to built-in defaults when absent)
- modelscope ≥ 1.9 or huggingface_hub ≥ 0.23 (first-run model download)
- Optional: system ffmpeg (only on `decode_failed`); node (only to run `validate-qsh-form.mjs`)
- Tests: pytest, PyYAML (runtime deps **not** required)
- **Credentials: none**

## License

GPL-3.0 — see [LICENSE](LICENSE)
