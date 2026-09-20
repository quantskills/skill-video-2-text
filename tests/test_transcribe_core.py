"""转录核心逻辑测试 —— 参数装配、设备探测、错误归类、run() 契约。

全部用桩件替代 faster-whisper，因此**不需要安装任何重依赖**即可运行。
"""
import sys
import types

import pytest

from scripts import transcribe
from scripts.transcribe import InputValidationError


class _FakeMeta:
    language = "zh"
    language_probability = 0.9876
    duration = 7.8


class _FakeSeg:
    def __init__(self, start, end, text):
        self.start, self.end, self.text = start, end, text


class _FakeModel:
    """替代 WhisperModel：要么返回预置分段，要么抛指定异常。"""

    def __init__(self, segments=None, error=None):
        self._segments = segments or []
        self._error = error
        self.received_kwargs = None
        self.received_path = None

    def transcribe(self, path, **kwargs):
        self.received_path = path
        self.received_kwargs = kwargs
        if self._error:
            raise self._error
        return iter(self._segments), _FakeMeta()


@pytest.fixture
def mp3(tmp_path):
    path = tmp_path / "demo.mp3"
    path.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 4096)
    return path


@pytest.fixture
def stub_model(monkeypatch, mp3):
    """把模型加载桩掉，返回一个可注入分段/异常的工厂。"""
    holder = {}

    def _install(segments=None, error=None):
        model = _FakeModel(segments, error)
        holder["model"] = model
        monkeypatch.setattr(transcribe, "_load_whisper_model", lambda cfg, size, path: model)
        return model

    return _install


class TestBuildTranscribeKwargs:
    def test_defaults_from_config(self, cfg):
        kwargs = transcribe._build_transcribe_kwargs(cfg, None, None)
        assert kwargs["beam_size"] == 5
        assert kwargs["best_of"] == 5
        assert kwargs["temperature"] == 0.0
        assert kwargs["condition_on_previous_text"] is False

    def test_vad_parameters_present_when_enabled(self, cfg):
        kwargs = transcribe._build_transcribe_kwargs(cfg, None, None)
        assert kwargs["vad_filter"] is True
        assert kwargs["vad_parameters"] == {"min_silence_duration_ms": 2000}

    def test_vad_parameters_absent_when_disabled(self, cfg):
        cfg["transcribe"]["vad_filter"] = False
        kwargs = transcribe._build_transcribe_kwargs(cfg, None, None)
        assert kwargs["vad_filter"] is False
        assert "vad_parameters" not in kwargs

    def test_explicit_language_wins(self, cfg):
        cfg["transcribe"]["language"] = "en"
        assert transcribe._build_transcribe_kwargs(cfg, "zh", None)["language"] == "zh"

    def test_config_language_used_when_arg_none(self, cfg):
        cfg["transcribe"]["language"] = "en"
        assert transcribe._build_transcribe_kwargs(cfg, None, None)["language"] == "en"

    def test_no_language_key_when_auto(self, cfg):
        assert "language" not in transcribe._build_transcribe_kwargs(cfg, None, None)

    def test_initial_prompt_arg_wins(self, cfg):
        cfg["transcribe"]["initial_prompt"] = "配置里的"
        kwargs = transcribe._build_transcribe_kwargs(cfg, None, "参数里的")
        assert kwargs["initial_prompt"] == "参数里的"

    def test_initial_prompt_from_config(self, cfg):
        cfg["transcribe"]["initial_prompt"] = "量枢学院 量化交易"
        assert transcribe._build_transcribe_kwargs(cfg, None, None)["initial_prompt"] == "量枢学院 量化交易"

    def test_no_prompt_key_when_unset(self, cfg):
        assert "initial_prompt" not in transcribe._build_transcribe_kwargs(cfg, None, None)

    def test_types_are_coerced(self, cfg):
        """YAML 里写成字符串的数字也要能用。"""
        cfg["transcribe"]["beam_size"] = "3"
        cfg["transcribe"]["vad_min_silence_ms"] = "1500"
        kwargs = transcribe._build_transcribe_kwargs(cfg, None, None)
        assert kwargs["beam_size"] == 3
        assert kwargs["vad_parameters"] == {"min_silence_duration_ms": 1500}


class TestDetectDevice:
    def test_cpu_when_ctranslate2_missing(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "ctranslate2", None)
        assert transcribe._detect_device() == "cpu"

    def test_cpu_when_no_cuda(self, monkeypatch):
        fake = types.ModuleType("ctranslate2")
        fake.get_cuda_device_count = lambda: 0
        monkeypatch.setitem(sys.modules, "ctranslate2", fake)
        assert transcribe._detect_device() == "cpu"

    def test_cuda_when_available(self, monkeypatch):
        fake = types.ModuleType("ctranslate2")
        fake.get_cuda_device_count = lambda: 1
        monkeypatch.setitem(sys.modules, "ctranslate2", fake)
        assert transcribe._detect_device() == "cuda"

    def test_falls_back_when_probe_raises(self, monkeypatch):
        fake = types.ModuleType("ctranslate2")

        def _boom():
            raise RuntimeError("探测失败")

        fake.get_cuda_device_count = _boom
        monkeypatch.setitem(sys.modules, "ctranslate2", fake)
        assert transcribe._detect_device() == "cpu"

    def test_no_torch_dependency(self):
        """设备探测刻意不引入 torch —— 本 Skill 只依赖 ctranslate2，
        多拖一个 torch 会让安装体积从几百 MB 涨到 2GB+。"""
        import inspect
        assert "import torch" not in inspect.getsource(transcribe._detect_device)
        assert "import torch" not in inspect.getsource(transcribe)


class TestTranscribeResultShape:
    def test_result_has_contract_keys(self, mp3, stub_model):
        stub_model([_FakeSeg(0.0, 3.2, " 你好 ")])
        result = transcribe.transcribe(mp3)
        for key in ("ok", "source", "model_size", "language", "language_probability",
                    "duration", "segment_count", "segments"):
            assert key in result, f"缺少契约字段: {key}"

    def test_ok_is_true(self, mp3, stub_model):
        stub_model([_FakeSeg(0.0, 1.0, "x")])
        assert transcribe.transcribe(mp3)["ok"] is True

    def test_text_is_stripped(self, mp3, stub_model):
        """契约要求 text 已 strip，validate.py --strict 会检查这一点。"""
        stub_model([_FakeSeg(0.0, 1.0, "  带空白  ")])
        assert transcribe.transcribe(mp3)["segments"][0]["text"] == "带空白"

    def test_empty_segments_dropped(self, mp3, stub_model):
        stub_model([_FakeSeg(0.0, 1.0, "   "), _FakeSeg(1.0, 2.0, "有效")])
        segments = transcribe.transcribe(mp3)["segments"]
        assert len(segments) == 1
        assert segments[0]["text"] == "有效"

    def test_timestamps_rounded_to_2dp(self, mp3, stub_model):
        stub_model([_FakeSeg(0.123456, 1.987654, "x")])
        seg = transcribe.transcribe(mp3)["segments"][0]
        assert seg["start"] == 0.12
        assert seg["end"] == 1.99

    def test_probability_rounded_to_3dp(self, mp3, stub_model):
        stub_model([_FakeSeg(0.0, 1.0, "x")])
        assert transcribe.transcribe(mp3)["language_probability"] == 0.988

    def test_segment_count_matches(self, mp3, stub_model):
        stub_model([_FakeSeg(0.0, 1.0, "a"), _FakeSeg(1.0, 2.0, "b")])
        result = transcribe.transcribe(mp3)
        assert result["segment_count"] == len(result["segments"]) == 2

    def test_absolute_path_passed_to_model(self, mp3, stub_model):
        model = stub_model([_FakeSeg(0.0, 1.0, "x")])
        transcribe.transcribe(mp3)
        assert model.received_path == str(mp3.resolve())

    def test_language_forwarded_to_model(self, mp3, stub_model):
        model = stub_model([_FakeSeg(0.0, 1.0, "x")])
        transcribe.transcribe(mp3, language="zh")
        assert model.received_kwargs["language"] == "zh"

    def test_no_print_from_transcribe(self, mp3, stub_model, capsys):
        """核心函数必须静默，把输出通道留给 CLI 层。"""
        stub_model([_FakeSeg(0.0, 1.0, "x")])
        transcribe.transcribe(mp3)
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""


class TestErrorClassification:
    @pytest.mark.parametrize("message", [
        "Invalid data found when processing input",
        "Unknown format or invalid data",
        "decoder error occurred",
        "moov atom not found",
    ])
    def test_decode_errors_mapped(self, mp3, stub_model, message):
        stub_model(error=RuntimeError(message))
        with pytest.raises(InputValidationError) as exc:
            transcribe.transcribe(mp3)
        assert exc.value.code == "decode_failed"
        assert "ffmpeg" in exc.value.hint

    def test_generic_failure_mapped(self, mp3, stub_model):
        stub_model(error=RuntimeError("模型内部炸了"))
        with pytest.raises(InputValidationError) as exc:
            transcribe.transcribe(mp3)
        assert exc.value.code == "transcribe_failed"

    def test_hint_mentions_per_os_install(self, mp3, stub_model):
        stub_model(error=RuntimeError("Invalid data found when processing input"))
        with pytest.raises(InputValidationError) as exc:
            transcribe.transcribe(mp3)
        for token in ("brew install ffmpeg", "winget install ffmpeg", "apt install ffmpeg"):
            assert token in exc.value.hint

    def test_original_message_preserved(self, mp3, stub_model):
        stub_model(error=RuntimeError("具体的底层报错"))
        with pytest.raises(InputValidationError) as exc:
            transcribe.transcribe(mp3)
        assert "具体的底层报错" in exc.value.message

    def test_model_load_failure_mapped(self, mp3, monkeypatch):
        monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(
            WhisperModel=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("int8 不支持"))))
        monkeypatch.setattr(transcribe.model_utils, "ensure_model", lambda **k: "/tmp/model")
        with pytest.raises(InputValidationError) as exc:
            transcribe.transcribe(mp3)
        assert exc.value.code == "model_load_failed"
        assert "compute_type" in exc.value.hint


class TestRunContract:
    def test_returns_result_with_outputs(self, mp3, stub_model, tmp_path):
        stub_model([_FakeSeg(0.0, 3.2, "你好")])
        result = transcribe.run(str(mp3), output_dir=str(tmp_path))
        assert result["ok"] is True
        assert set(result["outputs"]) == {"json", "plain_text", "timeline"}

    def test_no_write_files(self, mp3, stub_model):
        stub_model([_FakeSeg(0.0, 1.0, "x")])
        assert transcribe.run(str(mp3), write_files=False)["outputs"] == {}

    def test_relative_output_dir_resolved_against_repo_root(self, mp3, stub_model, monkeypatch, tmp_path):
        """相对路径必须落到 skill 仓库内，而不是调用方的 cwd —— 否则装到 ~/.claude/skills 后行为漂移。"""
        monkeypatch.setattr(transcribe, "REPO_ROOT", tmp_path)
        stub_model([_FakeSeg(0.0, 1.0, "x")])
        result = transcribe.run(str(mp3), output_dir="output")
        assert result["outputs"]["json"].startswith(str(tmp_path))

    def test_absolute_output_dir_kept(self, mp3, stub_model, tmp_path):
        stub_model([_FakeSeg(0.0, 1.0, "x")])
        target = tmp_path / "elsewhere"
        result = transcribe.run(str(mp3), output_dir=str(target))
        assert result["outputs"]["json"].startswith(str(target))

    def test_accepts_unknown_kwargs(self, mp3, stub_model):
        """**kwargs 预留位：调用方多传参数不该炸，保证向前兼容。"""
        stub_model([_FakeSeg(0.0, 1.0, "x")])
        assert transcribe.run(str(mp3), write_files=False, future_option=1)["ok"] is True

    def test_propagates_validation_error(self, tmp_path):
        with pytest.raises(InputValidationError) as exc:
            transcribe.run(str(tmp_path / "nope.mp3"))
        assert exc.value.code == "input_not_found"

    def test_config_path_honoured(self, mp3, stub_model, tmp_path):
        config_file = tmp_path / "custom.yaml"
        config_file.write_text("model:\n  size: small\n", encoding="utf-8")
        model = stub_model([_FakeSeg(0.0, 1.0, "x")])
        result = transcribe.run(str(mp3), config=str(config_file), write_files=False)
        assert result["model_size"] == "small"
        assert model is not None
