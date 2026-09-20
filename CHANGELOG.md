# Changelog

本文件记录 `skill-video-2-text` 的所有重要变更。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [0.1.1] - 2026-09-20

补上模型层的真实验证。此前 354 例全部走桩件，契约（输入校验、错误码、stdout 纯净、
配置优先级、模型发现逻辑）覆盖得很密，但**模型本身是否可靠从未被实测过**——
`faster-whisper` 装齐后到底能不能出正确结果，没有任何一条测试回答这个问题。
本轮零运行期行为变化，只加测试与文档。

### Added

- `tests/test_model_reliability.py` — 13 例模型可靠性集成测试，**opt-in**。
  开关是显式环境变量 `V2T_RUN_MODEL_TESTS=1`，默认整体 module-level skip；
  即使开了开关，缺重依赖或模型权重不可得时仍 **skip 而非 fail**
  （本机没下过权重不该被报成「模型不可靠」，那是两件事）。六类断言：
  - **确定性** — 同输入连转两次，segments 逐字逐时间戳完全一致。
    `temperature: 0.0` + `beam_size: 5` 不启用采样回退，所以这是「必须相同」
    而非「大概率相同」。另有 `test_decoding_params_are_deterministic` 守配置前提，
    且**不允许键缺失就当通过**（读不到 temperature 说明配置结构变了，静默放行等于撤守卫）。
  - **语种判定** — 清晰普通话必须判为 `zh` 且 `language_probability >= 0.9`；
    视频输入不因容器格式退化。语种判错会让整段转写用错声学模型，比个别同音字严重得多。
  - **识别质量** — 与既有 `tests/fixtures/sample_zh.txt` 期望文稿比对**关键术语覆盖率**
    （13 个术语至少命中 11 个）。刻意不做逐字比对：繁简与同音字差异是第 2 阶段的职责。
  - **视频解封装** — `demo.mp4`（H.264 + AAC）必须真解出音轨并转写成功，
    且模型报告时长与容器实测吻合。这是「系统 ffmpeg 缺失只该告警不该阻塞」的实证。
  - **时间轴完整性** — 多段时时间戳单调、不重叠、不越界；无空文本段。
  - **权重缓存复用** — `find_local_model` 能在本地缓存命中完整权重（四文件齐备），
    且缓存根目录可被环境变量改写，不硬编码到某个用户家目录。
- `SKILL.md`「已知局限」新增两条**实测**边界（详见下文 Changed 里的发现），
  「验收要求」新增模型可靠性一行，并修正「种子可复现性」的措辞
  （原写「贪心解码」，实际是 `beam_size: 5` 束搜索 + `temperature: 0.0` 不采样）。

### Changed

- `demo.mp4` 从 skill 根目录移到 `tests/fixtures/demo.mp4`。它是**测试输入**而非交付物
  ——与既有的 `sample_zh.wav` / `sample_zh.txt` / `make_sample_zh.sh` 同属真实媒体 fixture，
  且覆盖 wav 覆盖不到的视频解封装路径。放在根目录会被误认成交付视频。
- 实测发现并记录两条模型行为边界（此前文档未提，均由新测试钉住）：
  1. **连续语音不分段**：21 秒不间断发言只切出 1 个 segment（0.0→21.0s 全文挤一段）。
     因 `vad_min_silence_ms: 2000` 需 2 秒静音才切，而整段落在 whisper 单个 30s 窗口内。
     对会议录音这类长连续发言，`timeline` 会退化成巨段、失去跳转与字幕对齐价值。
     同样时长的 `demo.mp4` 有自然停顿，切出 6 段，可作对照。
  2. **`base` 模型输出繁体与同音字错**：内容骨架正确，但「對其/自斷/戶身300/華點」
     应为「对齐/字段/沪深三百/滑点」，数字写成「千分之1.5」，标点一逗到底。
     这实证了两阶段设计的必要性，而非脚本缺陷。
     `test_known_weak_spots_are_documented_not_hidden` 反过来断言这些词**确实**没被识别对
     ——哪天模型升级后识别对了，测试会红，逼人回来更新文档而不是默默删断言。
- 文档中写死的测试数全部更新为 pytest 实际打印的摘要行，避免「collected」与
  「passed + skipped」两套口径对不上：11 处，涉及 README 双语、SKILL.md、INSTALL.md、
  `references/api_guide.md`、`agents/portable-loader.md`。
  开关关闭（CI 默认）为 `353 passed + 2 skipped`，打开为 `366 passed + 1 skipped`。
  本 CHANGELOG 0.1.0 条目里的「354 例，10 个文件」**刻意不改**——那是当时的准确记录，
  按 Keep a Changelog 惯例不改写历史条目。

### 验证记录

真依赖环境为独立 venv（`/opt/homebrew/bin/python3.11` 建，Python 3.11.16），
装 `requirements.txt` 得 faster-whisper 1.2.1 / ctranslate2 4.8.2 / av 18.1.0 / huggingface_hub 1.32.0。
模型经 ModelScope 通道落地（`huggingface.co` 在本机不可达），五级回退按设计选到能通的一级。

| 场景 | 结果 |
|---|---|
| 首次转录 `demo.mp4`（含 145MB 下载） | 退出 0，6 段，16.0s |
| 二次转录（缓存命中） | 退出 0，**1.43s**，stderr 零下载进度 |
| `validate.py --strict` | `status: PASS`，0 errors / 0 warnings |
| `env_check` 装依赖前 → 后 | `blocked`/`ok:false`/退出 1 → `degraded`/`ok:true`/退出 2（仅 `ffmpeg` 告警） |
| 集成测试（开关开） | **13 passed in 4.93s** |
| 开发 venv 全量（开关关） | **353 passed, 2 skipped** —— 桩件 venv 未被污染，`dependency_missing` 负路径仍有效 |

> 踩坑记录：`python3 -m venv` 默认取到 CommandLineTools 的 **Python 3.9.6**，
> 低于 `skill.json` 要求的 `>=3.10`，导致 `python` 自身成为 blocker、pip 静默装不上
> （日志 0 行、后台任务却报 exit 0——那是 `nohup &` 启动器的退出码，不是 pip 的）。
> 必须显式用 `/opt/homebrew/bin/python3.11` 建 venv。

## [0.1.0] - 2026-09-20

首个可交付版本。两阶段设计：脚本负责出带时间戳的原始语料，标点/分段/摘要交给调用方大模型。

### Added

- `scripts/transcribe.py` — 主入口。提供可 import 的 `run()` 与 `validate_input()`，
  以及 `InputValidationError(ValueError)`（携带 `code` / `message` / `hint`）。
- `scripts/model_utils.py` — 模型发现与获取，五级顺序回退：
  `explicit_path` → `local_cache` → `modelscope` → `hf_mirror` → `hf_official`。
  命中即不重复下载。
- `scripts/env_check.py` — 运行环境自检，`--self-check [--json]`，
  分组 `runtime` / `dependencies` / `decoder` / `model` / `config`。
- `scripts/config.py` — 四级配置优先级：命令行 > 环境变量 > `config/*.yaml` > 内置默认值；
  PyYAML 缺失时静默降级到内置默认值，不影响主流程。
- `scripts/validate.py` — 输出契约校验器，`--strict` 加严告警；退出码 0 = PASS，1 = FAIL。
- `config/transcribe_config.yaml` — 模型 / 下载 / 解码 / 输入 / 输出五节超参外置。
  `temperature: 0.0` 贪心解码，转录要可复现而不是有创意。
- `agents/` — `openai.yaml`（OpenAPI 3.1 工具定义）、`cursor-rule.mdc`、`portable-loader.md`。
- `tests/` — 354 例，10 个文件。重依赖全部以桩件替代，只需 pytest + PyYAML 即可跑完。
- `.github/workflows/validate.yml` — qsh-form 校验 + pytest + 5 步负路径反向断言。
- 阻塞/告警判定线（刻意区分，有测试把守）：
  Python ≥ 3.10、faster-whisper / ctranslate2、模型权重缺失为**阻塞项**（退出 1）；
  系统 ffmpeg、modelscope / huggingface_hub、PyYAML 缺失仅为**告警**（退出 2）——
  PyAV 自带静态链接的 ffmpeg，通常够用，不该在转录前就拦住用户。

### Fixed

以下五项均为 v0.1 开发期发现的真实缺陷，每项都配了回归测试：

- **stdout 被进度日志污染** — `model_utils.ensure_model()` 曾把「发现本地已缓存的模型」
  这类进度打到 stdout，与结果 JSON 混在一起，机器消费方 `json.loads(stdout)` 必然失败，
  与文档承诺自相矛盾。现契约固定为：**stdout 有且仅有一个 JSON 对象**，
  进度、下载日志、告警一律走 stderr。（`test_stdio_contract.py`）
- **hf-mirror 兜底层实际仍打官方站** — 原实现先 `from huggingface_hub import snapshot_download`
  再设 `os.environ["HF_ENDPOINT"]`；但 huggingface_hub 在 import 时就把该值固化成模块级常量，
  并被 `hf_api` 等子模块 from-import 复制了一份，于是镜像层形同虚设、国内必然超时。
  现改为切换时同步改写已导入模块树上的每一份 ENDPOINT。（`test_hf_endpoint.py`）
- **ModelScope 缓存布局未被识别** — 只认 `<cache_root>/<size>/`，漏了 ModelScope
  `snapshot_download` 的真实落点 `<cache_root>/_ms_cache/models/<owner>--<repo>/snapshots/<revision>/`，
  导致已下载过的模型每次运行都重新下载。两种布局现均视为命中。（`test_model_discovery.py`）
- **`agents/openai.yaml` 有两处 YAML 语法错误** — `x-model-sources` 在同一层级混用序列项与映射键；
  `description` 以引号开头后又跟尾随文本。文件根本无法解析，下游平台会静默加载失败，
  而这类错误没有任何运行时征兆，此前只靠人工检查发现。现已由契约测试把守。（`test_contract_files.py`）
- **requirements 行内注释被 pip 当成版本约束** — 注释未用「空白 + `#`」分隔时，
  pip 会把它并入上一行的版本表达式。现所有注释独立成行，并有语法解析测试兜底。（`test_contract_files.py`）

### Notes

- **零凭据**：本 skill 不读取任何 `*_USERNAME` / `*_TOKEN` / `*_API_KEY`，
  只读 `HF_ENDPOINT` / `FASTER_WHISPER_MODEL_DIR` / `FASTER_WHISPER_CACHE_ROOT` 三个无害变量，
  且有 AST 级测试精确断言这一点。
- **转录稿默认不入库**：`output/` 与 `*.transcript.*` 已 gitignore。录音不出本机是本 skill 的
  核心卖点，把转录稿提交到远端等于把这个卖点作废。
- 已知局限见 `SKILL.md` §「已知局限」：无说话人分离、无实时流式、无热词表。
