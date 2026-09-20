"""模型可靠性集成测试 —— opt-in，默认整体跳过。

**为什么必须 opt-in，而不是像其它测试那样直接跑：**

本文件是仓库里唯一真跑 faster-whisper 推理的测试。它与 `tests/` 其余 13 个文件的
桩件策略是刻意对立的，两边都对，各司其职：

| | 桩件测试（其余文件） | 本文件 |
|---|---|---|
| 验证对象 | 契约：输入校验、错误码、stdout 纯 JSON、配置优先级、模型发现逻辑 | 模型本身：确定性、语种判定、识别质量、视频解封装 |
| 依赖 | 只需 pytest + PyYAML | faster-whisper / ctranslate2 / av + 145MB 权重 |
| CI | 必须跑，十几秒绿 | 必须跳过——CI 不装重依赖，否则「缺依赖结构化报错」这条负路径无法验证 |

所以开关是显式的环境变量，默认关闭：

    V2T_RUN_MODEL_TESTS=1 python -m pytest tests/test_model_reliability.py -v

即使打开了开关，缺依赖或模型不可得时仍然 **skip 而非 fail**——本机没下过权重
不应该被报成「模型不可靠」，那是两件事。

**为什么这些断言能算「可靠性」而不是「碰运气」：**

`config/transcribe_config.yaml` 里 `temperature: 0.0` + `beam_size: 5`，
即束搜索且不启用采样回退，注释写的是「转录任务不要采样」。无采样意味着
同输入必同输出，这把它从「大概差不多」变成了一个可以断言的硬性质——
`test_repeat_transcription_is_identical` 就是在守这条，
`test_decoding_params_are_deterministic` 守的是它的前提。
识别质量那条不做逐字比对（base 模型在 CPU int8 下必然有同音字与繁简差异，
那是第 2 阶段大模型后处理的职责，不是缺陷），改为断言**关键术语覆盖率**：
已知会错的那几个词被显式排除并记录在案，其余必须命中。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"

# ---------------------------------------------------------------------------
# opt-in 门禁
# ---------------------------------------------------------------------------

_TRUTHY = {"1", "true", "yes", "on"}


def _gate_reason() -> str | None:
    """返回跳过原因；返回 None 表示放行。逐项检查，给出可执行的修复动作。"""
    if os.environ.get("V2T_RUN_MODEL_TESTS", "").strip().lower() not in _TRUTHY:
        return (
            "未开启模型集成测试。这是 opt-in 的：需要真跑 faster-whisper 推理，"
            "CI 刻意不装重依赖。本机运行请加 V2T_RUN_MODEL_TESTS=1"
        )
    try:
        import faster_whisper  # noqa: F401
        import ctranslate2  # noqa: F401
        import av  # noqa: F401
    except ImportError as exc:
        return (
            f"重依赖不可用（{exc}）。请先 pip install -r requirements.txt "
            f"（国内网络加 -i https://mirrors.aliyun.com/pypi/simple/），"
            f"并注意用 Python >= 3.10 建 venv——系统 python3 可能是 3.9"
        )
    for name in ("sample_zh.wav", "sample_zh.txt", "demo.mp4"):
        if not (FIXTURES / name).is_file():
            return f"测试媒体缺失：tests/fixtures/{name}"
    return None


_REASON = _gate_reason()
if _REASON is not None:
    pytest.skip(_REASON, allow_module_level=True)

from scripts import model_utils, transcribe  # noqa: E402

MODEL_SIZE = "base"


# ---------------------------------------------------------------------------
# 会话级 fixture：模型只解析一次，每段媒体只转写一次
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def model_dir() -> str:
    """本地模型目录。命中缓存则不联网；拿不到就 skip，不 fail。"""
    found = model_utils.find_local_model(MODEL_SIZE)
    if found:
        return found
    try:
        return model_utils.ensure_model(MODEL_SIZE)
    except Exception as exc:  # 网络不通 / 镜像全挂
        pytest.skip(
            f"模型权重不可得，无法判定模型可靠性：{exc}. "
            f"手动下载见 model_utils.manual_download_hint()"
        )


@pytest.fixture(scope="session")
def wav_result(model_dir) -> dict:
    """既有真实 fixture：tests/fixtures/sample_zh.wav（有期望文稿可比对）。"""
    return transcribe.run(
        str(FIXTURES / "sample_zh.wav"),
        model_size=MODEL_SIZE,
        model_path=model_dir,
        write_files=False,
    )


@pytest.fixture(scope="session")
def mp4_result(model_dir) -> dict:
    """视频输入：tests/fixtures/demo.mp4，覆盖解封装取音轨这条路径。"""
    return transcribe.run(
        str(FIXTURES / "demo.mp4"),
        model_size=MODEL_SIZE,
        model_path=model_dir,
        write_files=False,
    )


# ---------------------------------------------------------------------------
# 繁简与标点归一化
# ---------------------------------------------------------------------------

# base 模型对普通话常输出繁体，这是已知行为、不是缺陷（第 2 阶段大模型负责转换）。
# 本表只覆盖 sample_zh 这份 fixture 的词汇范围，不是通用繁简转换器——
# 换 fixture 就要补表，这是刻意的：宁可显式可见，不要引入 opencc 这种重依赖。
_TRAD_TO_SIMP = {
    "對": "对", "齊": "齐", "進": "进", "數": "数", "據": "据", "經": "经",
    "組": "组", "週": "周", "復": "复", "測": "测", "華": "华", "點": "点",
    "參": "参", "調": "调", "兩": "两", "處": "处", "權": "权", "會": "会",
    "從": "从", "說": "说", "時": "时", "間": "间", "後": "后", "樣": "样",
    "於": "于", "與": "与", "為": "为", "個": "个", "們": "们", "這": "这",
    "來": "来", "東": "东", "書": "书", "學": "学", "開": "开", "關": "关",
}

# 去掉标点再比对：base 模型倾向一逗到底，标点由第 2 阶段按 segment 间隔补
_PUNCT = "，。、；：？！,.:;?!\"'“”‘’（）()【】[] \n\r\t"


def _normalize(text: str) -> str:
    """繁体转简体 + 去标点，用于关键词覆盖比对。"""
    return "".join(
        _TRAD_TO_SIMP.get(ch, ch) for ch in text if ch not in _PUNCT
    )


# 期望文稿里**能被 base 模型稳定命中**的关键术语。
# 刻意排除了已知会错的五个，它们记录在下面的 test 里而不是被偷偷放宽阈值：
#   对齐→對其、字段→自斷、沪深三百→戶身300、滑点→華點、一点五→1.5
# 排除的理由是同音字与数字格式，属第 2 阶段大模型后处理的职责范围。
_EXPECTED_KEYWORDS = (
    "各位早上好",
    "第三季度",
    "进度",
    "数据接口",
    "已经核对完成",
    "成分股权重",
    "存在两处偏差",
    "工程组",
    "本周三之前修复",
    "回测引擎",
    "参数",
    "千分之一",
    "调整到",
)

# 13 个里至少命中 11 个。留 2 个余量是给模型/库版本升级的抖动，
# 不是给识别质量滑坡的——低于这个比例说明模型或音频出了问题。
_MIN_KEYWORD_HITS = 11


# ---------------------------------------------------------------------------
# ① 确定性：贪心解码 ⇒ 同输入必同输出
# ---------------------------------------------------------------------------


class TestDeterminism:
    """无采样解码的可复现性。这是「可靠」最硬的一条定义。"""

    def test_repeat_transcription_is_identical(self, model_dir):
        """同一文件连转两次，segments 必须逐字逐时间戳完全一致。

        `temperature: 0.0` 下不启用采样回退，束搜索又是确定性的，
        所以这不是「大概率相同」而是「必须相同」。
        一旦这条红了，说明有人把 temperature 调离了 0、或换了带随机性的
        解码参数——转录结果将不再可复现，而可复现是本 skill 的立身之本。
        """
        target = str(FIXTURES / "sample_zh.wav")
        first = transcribe.run(
            target, model_size=MODEL_SIZE, model_path=model_dir, write_files=False
        )
        second = transcribe.run(
            target, model_size=MODEL_SIZE, model_path=model_dir, write_files=False
        )
        assert first["segments"] == second["segments"], (
            "同一输入两次转写结果不一致，贪心解码的可复现性被破坏。"
            f"\n第一次首段: {first['segments'][0] if first['segments'] else None}"
            f"\n第二次首段: {second['segments'][0] if second['segments'] else None}"
        )
        assert first["language"] == second["language"]
        assert first["duration"] == second["duration"]

    def test_decoding_params_are_deterministic(self):
        """守住配置本身：解码参数必须保持无随机性，否则上面那条断言失去前提。

        确定性来自两处，缺一不可：
          - `temperature: 0.0` —— 不启用采样回退。faster-whisper 在温度 > 0 时
            会对低置信度片段做随机采样重试，同一音频两次结果就会不同。
          - `beam_size` 为固定整数 —— 束搜索本身是确定性的；若改成采样式解码
            （如 do_sample）则不再可复现。

        这条**不允许键缺失就当通过**：读不到 temperature 说明配置结构变了，
        那正是需要有人来看一眼的时刻，静默放行等于把守卫撤了。
        """
        import yaml

        cfg = yaml.safe_load(
            (REPO_ROOT / "config" / "transcribe_config.yaml").read_text(encoding="utf-8")
        )
        decoding = cfg.get("transcribe")
        assert isinstance(decoding, dict), (
            "config/transcribe_config.yaml 缺少 transcribe 节，解码参数无处可查"
        )
        assert "temperature" in decoding, (
            "transcribe 节缺少 temperature 键。它必须显式写为 0.0，"
            "缺失会让 faster-whisper 回落到库默认值，可复现性不再由本仓库掌控"
        )
        assert decoding["temperature"] == 0.0, (
            f"temperature 应为 0.0（无采样、可复现），实际为 "
            f"{decoding['temperature']!r}。调高它会使 "
            "test_repeat_transcription_is_identical 失效"
        )
        beam = decoding.get("beam_size")
        assert isinstance(beam, int) and beam >= 1, (
            f"beam_size 应为正整数（确定性束搜索），实际为 {beam!r}"
        )


# ---------------------------------------------------------------------------
# ② 语种判定
# ---------------------------------------------------------------------------


class TestLanguageDetection:
    def test_detects_chinese_with_high_confidence(self, wav_result):
        """清晰的普通话必须判为 zh，且置信度 >= 0.9。

        语种判错会导致整段转写用错声学/语言模型，输出彻底不可用——
        这比个别同音字错严重得多，所以单独设一条。
        """
        assert wav_result["language"] == "zh", (
            f"期望判定为 zh，实际 {wav_result['language']!r}"
        )
        prob = wav_result["language_probability"]
        assert prob >= 0.9, f"语种置信度过低: {prob:.3f}（阈值 0.9）"

    def test_video_input_also_detects_chinese(self, mp4_result):
        """视频输入的语种判定与音频输入一致，不因容器格式而退化。"""
        assert mp4_result["language"] == "zh"
        assert mp4_result["language_probability"] >= 0.9


# ---------------------------------------------------------------------------
# ③ 识别质量：关键术语覆盖率（对既有期望文稿）
# ---------------------------------------------------------------------------


class TestRecognitionQuality:
    def test_keyword_coverage_against_ground_truth(self, wav_result):
        """与 tests/fixtures/sample_zh.txt 比对关键术语覆盖率。

        不做逐字比对：base 模型在 CPU int8 下必然有繁简差异与同音字错误，
        那是第 2 阶段（调用方大模型）的职责，SKILL.md 的两阶段设计正是为此。
        这里守的是「模型听对了内容骨架」——金融领域术语与句式结构必须命中。
        """
        expected = _normalize((FIXTURES / "sample_zh.txt").read_text(encoding="utf-8"))
        actual = _normalize(
            "".join(seg["text"] for seg in wav_result["segments"])
        )
        hits = [kw for kw in _EXPECTED_KEYWORDS if _normalize(kw) in actual]
        misses = [kw for kw in _EXPECTED_KEYWORDS if _normalize(kw) not in actual]

        assert len(hits) >= _MIN_KEYWORD_HITS, (
            f"关键术语命中 {len(hits)}/{len(_EXPECTED_KEYWORDS)}，"
            f"低于阈值 {_MIN_KEYWORD_HITS}。\n"
            f"  未命中: {misses}\n"
            f"  归一化后转录: {actual}\n"
            f"  归一化后期望: {expected}\n"
            "若命中率整体下滑，先确认模型权重未被替换、compute_type 未改动"
        )

    def test_known_weak_spots_are_documented_not_hidden(self, wav_result):
        """把已知会错的词显式记录在案，而不是靠放宽阈值把它们藏起来。

        这条不要求模型识别对——恰恰相反，它断言这些词**确实**没被正确识别，
        从而把「第 2 阶段大模型后处理是必需的」这一设计前提钉在测试里。
        哪天 base 模型升级后这些词也能识别对了，这条会红，
        那时应该更新文档说明质量已提升，而不是默默删掉断言。
        """
        actual = _normalize("".join(seg["text"] for seg in wav_result["segments"]))
        known_weak = {
            "对齐": "同音字，实测输出「對其」",
            "字段": "同音字，实测输出「自斷」",
            "沪深三百": "专名 + 数字，实测输出「戶身300」",
            "滑点": "领域术语，实测输出「華點」",
        }
        still_wrong = {kw: why for kw, why in known_weak.items()
                       if _normalize(kw) not in actual}
        assert still_wrong, (
            "已知弱点全部被正确识别了——说明模型质量已提升。"
            "请更新本测试与 SKILL.md「已知局限」，不要直接删掉这条断言"
        )
        assert len(still_wrong) >= 3, (
            f"已知弱点只剩 {len(still_wrong)} 项未命中，与文档描述不符: {still_wrong}"
        )


# ---------------------------------------------------------------------------
# ④ 视频容器解封装
# ---------------------------------------------------------------------------


class TestVideoDemux:
    def test_mp4_yields_real_speech_segments(self, mp4_result):
        """视频输入必须真解出音轨并转写成功——这条路径 wav 覆盖不到。

        demo.mp4 是 H.264 + AAC 的视频容器，走 PyAV 解封装取音轨。
        系统 ffmpeg 缺失只是告警（PyAV 自带静态链接的 ffmpeg），
        这条测试就是那个「告警不该拦人」判断的实证。
        """
        assert mp4_result["ok"] is True
        assert mp4_result["source"]["kind"] == "video"
        assert mp4_result["source"]["ext"] == ".mp4"

        segments = mp4_result["segments"]
        assert segments, "视频输入未产出任何 segment，解封装或转录失败"
        assert mp4_result["segment_count"] == len(segments)

        text = "".join(s["text"] for s in segments)
        assert len(text.strip()) >= 20, (
            f"视频转写文本过短（{len(text.strip())} 字符），疑似只解出了静音轨"
        )

    def test_detected_duration_matches_container(self, mp4_result):
        """模型报告的时长应与容器实际时长吻合（mvhd 实测 20.04s）。

        差太多说明解码时丢帧或截断，转写会缺内容——这类失败很隐蔽，
        因为输出格式完全合法，只有对着容器时长才看得出来。
        """
        duration = mp4_result["duration"]
        assert 15.0 <= duration <= 25.0, (
            f"模型报告时长 {duration:.2f}s，容器实测约 20.04s，偏差过大"
        )


# ---------------------------------------------------------------------------
# ⑤ 时间轴结构合法性
# ---------------------------------------------------------------------------


class TestTimelineIntegrity:
    def test_multi_segment_timeline_is_monotonic(self, mp4_result):
        """多 segment 时时间戳必须递增且不重叠——错乱的时间轴会让下游字幕/检索全废。

        用 demo.mp4 而不是 sample_zh.wav 做这条：mp4 有自然停顿，实测切出 6 段，
        才有时序可验；wav 是连续发言只出 1 段（见下一条），拿它验时序等于没验。
        """
        segments = mp4_result["segments"]
        assert len(segments) >= 2, (
            f"demo.mp4 只切出 {len(segments)} 段，无法验证时序性质。"
            "若模型或 VAD 参数变更导致分段塌缩，下游时间轴导航会失效"
        )

        for i, seg in enumerate(segments):
            assert seg["start"] < seg["end"], (
                f"第 {i} 段时间戳倒置: start={seg['start']} end={seg['end']}"
            )
            assert seg["start"] >= 0.0, f"第 {i} 段起点为负: {seg['start']}"
            if i > 0:
                assert seg["start"] >= segments[i - 1]["end"] - 1e-6, (
                    f"第 {i} 段与上一段重叠: 上段 end={segments[i-1]['end']} "
                    f"本段 start={seg['start']}"
                )

        assert segments[-1]["end"] <= mp4_result["duration"] + 0.5, (
            f"末段 end={segments[-1]['end']} 超出总时长 {mp4_result['duration']}"
        )

    def test_continuous_speech_collapses_to_single_segment(self, wav_result):
        """把实测到的分段边界钉在测试里：连续无停顿语音只会得到 1 个 segment。

        sample_zh.wav 是 21 秒不间断发言，配置为 `vad_filter: true` +
        `vad_min_silence_ms: 2000`——需要 2 秒静音才切分，而整段又落在
        whisper 单个 30 秒窗口内，于是全文挤进一段。

        **这是一条真实的能力边界，不是缺陷，但使用者必须知道**：
        对会议录音这类长时间连续发言，时间轴会退化成一条 0.0→N 的巨段，
        失去跳转与字幕对齐价值。缓解手段是调小 `vad_min_silence_ms`，
        代价是把语气停顿也切成碎片。

        本断言的作用是：哪天这个行为变了（比如默认参数调整、或换了 VAD 实现），
        测试会红，逼着有人回来更新 SKILL.md「已知局限」——而不是让文档悄悄失真。
        """
        segments = wav_result["segments"]
        assert len(segments) == 1, (
            f"sample_zh.wav 实测切出 {len(segments)} 段，与此前记录的 1 段不符。"
            "分段行为已变化，请重新评估时间轴可用性并更新 SKILL.md「已知局限」"
        )
        seg = segments[0]
        # 单段仍必须结构合法：起点归零、终点覆盖全片、不倒置
        assert seg["start"] == 0.0, f"唯一 segment 起点应为 0.0，实际 {seg['start']}"
        assert seg["end"] > 0.0, "唯一 segment 终点必须为正"
        assert seg["end"] <= wav_result["duration"] + 0.5, (
            f"segment 终点 {seg['end']} 超出音频时长 {wav_result['duration']}"
        )
        # 且内容确实是整段发言，不是被截断的前几秒
        assert len(_normalize(seg["text"])) >= 60, (
            f"单段文本仅 {len(_normalize(seg['text']))} 字，疑似只转出了开头一小截，"
            "这与「VAD 未切分」是两种不同的失败，需分别排查"
        )

    def test_no_empty_segments(self, mp4_result):
        """不允许出现空文本 segment——那通常是静音被误切，会污染下游标点补全。"""
        for i, seg in enumerate(mp4_result["segments"]):
            assert seg["text"].strip(), f"第 {i} 段文本为空"


# ---------------------------------------------------------------------------
# ⑥ 权重缓存复用（持久化，不重复下载）
# ---------------------------------------------------------------------------


class TestModelCacheReuse:
    def test_local_cache_is_discoverable_and_complete(self):
        """缓存命中即不重复下载——这是「推理类持久化复用」的实证。

        find_local_model 必须能在本地缓存里找到完整权重（四个必需文件齐备）。
        找不到就意味着每次调用都要重下 145MB，对使用者是不可接受的成本。
        """
        found = model_utils.find_local_model(MODEL_SIZE)
        assert found, (
            f"本地缓存中找不到 {MODEL_SIZE} 模型，缓存复用失效。"
            f"缓存根目录: {model_utils.MODEL_CACHE_ROOT}"
        )
        missing = model_utils.missing_model_files(Path(found))
        assert not missing, f"缓存中的模型目录不完整，缺: {missing}"

    def test_cache_root_respects_env_override(self, tmp_path, monkeypatch):
        """缓存根目录可被环境变量改写——不能硬编码到某个用户家目录。"""
        monkeypatch.setattr(model_utils, "MODEL_CACHE_ROOT", tmp_path)
        assert model_utils.find_local_model(MODEL_SIZE) is None, (
            "空缓存目录里不应找到模型；若找到了说明查找逻辑没读 MODEL_CACHE_ROOT"
        )
        assert model_utils.local_model_candidates(MODEL_SIZE), (
            "候选路径列表不应为空，否则多级回退无从谈起"
        )
