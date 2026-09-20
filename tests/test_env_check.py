"""环境自检测试 —— 重点是"什么算阻塞项、什么只是告警"这条判定线。"""
import sys
import types

import pytest

from scripts import env_check, model_utils


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(model_utils, "MODEL_CACHE_ROOT", tmp_path)
    monkeypatch.delenv("FASTER_WHISPER_MODEL_DIR", raising=False)
    return tmp_path


class TestCheckPython:
    def test_ok_when_at_or_above_minimum(self, monkeypatch):
        monkeypatch.setattr(env_check, "MIN_PYTHON", (3, 9))
        result = env_check.check_python()
        assert result["status"] == "ok"
        assert result["hint"] == ""

    def test_blocker_below_minimum(self, monkeypatch):
        monkeypatch.setattr(env_check, "MIN_PYTHON", (99, 0))
        result = env_check.check_python()
        assert result["status"] == "blocker"
        assert "brew install python" in result["hint"]

    def test_reports_actual_version(self):
        import platform
        assert env_check.check_python()["found"] == platform.python_version()

    def test_minimum_is_3_10(self):
        """与 skill.json 的 runtime 声明、README 的依赖说明必须一致。"""
        assert env_check.MIN_PYTHON == (3, 10)


class TestCheckFfmpeg:
    """SKILL.md 第 2 步的核心约定：PyAV 自带解码库，缺 ffmpeg 不该拦停流程。"""

    def test_missing_ffmpeg_is_warning_not_blocker(self, monkeypatch):
        monkeypatch.setattr(env_check.shutil, "which", lambda name: None)
        result = env_check.check_ffmpeg()
        assert result["status"] == "warning"
        assert result["status"] != "blocker"

    def test_hint_says_try_first_install_later(self, monkeypatch):
        monkeypatch.setattr(env_check.shutil, "which", lambda name: None)
        hint = env_check.check_ffmpeg()["hint"]
        assert "先直接尝试转录" in hint
        assert "decode_failed" in hint

    def test_hint_covers_three_os(self, monkeypatch):
        monkeypatch.setattr(env_check.shutil, "which", lambda name: None)
        hint = env_check.check_ffmpeg()["hint"]
        for token in ("brew install ffmpeg", "winget install ffmpeg", "apt install ffmpeg"):
            assert token in hint

    def test_present_ffmpeg_is_ok(self, monkeypatch):
        monkeypatch.setattr(env_check.shutil, "which", lambda name: "/usr/local/bin/ffmpeg")
        result = env_check.check_ffmpeg()
        assert result["status"] == "ok"
        assert result["found"] == "/usr/local/bin/ffmpeg"


class TestCheckDependencies:
    def test_items_keyed_by_name(self):
        items = {item["name"]: item for item in env_check.check_dependencies()}
        for name in env_check.HARD_DEPENDENCIES + env_check.SOFT_DEPENDENCIES:
            assert name in items

    def test_missing_hard_dependency_is_blocker(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "faster_whisper", None)
        items = {item["name"]: item for item in env_check.check_dependencies()}
        assert items["faster_whisper"]["status"] == "blocker"
        assert "requirements.txt" in items["faster_whisper"]["hint"]

    def test_missing_soft_dependency_is_warning(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "modelscope", None)
        items = {item["name"]: item for item in env_check.check_dependencies()}
        assert items["modelscope"]["status"] == "warning"

    def test_soft_dependency_hint_explains_impact(self, monkeypatch):
        """告警要说清"影响什么"，而不是只说"没装"。"""
        monkeypatch.setitem(sys.modules, "modelscope", None)
        items = {item["name"]: item for item in env_check.check_dependencies()}
        assert "本地已缓存模型时不影响转录" in items["modelscope"]["hint"]

    def test_hard_dependencies_are_minimal(self):
        """刻意只把 faster_whisper + ctranslate2 列为硬性依赖，
        modelscope / huggingface_hub 都只影响下载通道，不该拦停转录。"""
        assert set(env_check.HARD_DEPENDENCIES) == {"faster_whisper", "ctranslate2"}

    def test_installed_module_reports_version(self, monkeypatch):
        fake = types.ModuleType("faster_whisper")
        fake.__version__ = "1.2.3"
        monkeypatch.setitem(sys.modules, "faster_whisper", fake)
        items = {item["name"]: item for item in env_check.check_dependencies()}
        assert items["faster_whisper"]["status"] == "ok"
        assert items["faster_whisper"]["found"] == "1.2.3"

    def test_module_without_version_reports_unknown(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "ctranslate2", types.ModuleType("ctranslate2"))
        items = {item["name"]: item for item in env_check.check_dependencies()}
        assert items["ctranslate2"]["found"] == "unknown"


class TestCheckModel:
    def test_found_model_is_ok(self, isolated_cache, make_model_dir):
        make_model_dir(isolated_cache / "base")
        result = env_check.check_model("base")
        assert result["status"] == "ok"
        assert result["found"] == str(isolated_cache / "base")

    def test_modelscope_cache_counts_as_found(self, isolated_cache):
        """回归：ModelScope 下载的模型必须被自检认可。"""
        snap = (isolated_cache / "_ms_cache" / "models"
                / "pengzhendong--faster-whisper-base" / "snapshots" / "master")
        snap.mkdir(parents=True)
        for name in ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt"):
            (snap / name).write_text("stub", encoding="utf-8")
        result = env_check.check_model("base")
        assert result["status"] == "ok"
        assert result["found"] == str(snap)

    def test_missing_model_is_blocker(self, isolated_cache):
        result = env_check.check_model("base")
        assert result["status"] == "blocker"
        assert "自动下载" in result["hint"]

    def test_hint_lists_scanned_locations(self, isolated_cache):
        hint = env_check.check_model("base")["hint"]
        assert str(isolated_cache / "base") in hint

    def test_incomplete_dir_reported_with_missing_files(self, isolated_cache):
        broken = isolated_cache / "base"
        broken.mkdir()
        (broken / "model.bin").write_text("stub", encoding="utf-8")
        hint = env_check.check_model("base")["hint"]
        assert "不完整" in hint
        assert "config.json" in hint

    def test_respects_model_size(self, isolated_cache, make_model_dir):
        make_model_dir(isolated_cache / "small")
        assert env_check.check_model("small")["status"] == "ok"
        assert env_check.check_model("base")["status"] == "blocker"


class TestSelfCheck:
    @pytest.fixture
    def all_green(self, monkeypatch, isolated_cache, make_model_dir):
        make_model_dir(isolated_cache / "base")
        monkeypatch.setattr(env_check, "MIN_PYTHON", (3, 0))
        monkeypatch.setattr(env_check, "check_dependencies", lambda: [
            {"name": n, "status": "ok", "found": "1.0", "hint": ""}
            for n in env_check.HARD_DEPENDENCIES + env_check.SOFT_DEPENDENCIES
        ])
        monkeypatch.setattr(env_check.shutil, "which", lambda name: "/usr/bin/ffmpeg")
        return isolated_cache

    def test_ready_when_all_green(self, all_green):
        report = env_check.self_check()
        assert report["status"] == "ready"
        assert report["ok"] is True
        assert report["exit_code"] == 0
        assert report["summary"]["blockers"] == []
        assert report["summary"]["warnings"] == []

    def test_degraded_on_warning_only(self, all_green, monkeypatch):
        monkeypatch.setattr(env_check.shutil, "which", lambda name: None)
        report = env_check.self_check()
        assert report["status"] == "degraded"
        assert report["ok"] is True
        assert report["exit_code"] == 2
        assert "ffmpeg" in report["summary"]["warnings"]

    def test_blocked_on_missing_model(self, all_green, monkeypatch):
        monkeypatch.setattr(model_utils, "MODEL_CACHE_ROOT", all_green / "empty")
        report = env_check.self_check()
        assert report["status"] == "blocked"
        assert report["ok"] is False
        assert report["exit_code"] == 1

    def test_blocker_outranks_warning(self, monkeypatch, isolated_cache):
        monkeypatch.setattr(env_check, "MIN_PYTHON", (99, 0))
        monkeypatch.setattr(env_check.shutil, "which", lambda name: None)
        report = env_check.self_check()
        assert report["status"] == "blocked"
        assert report["summary"]["blockers"] and report["summary"]["warnings"]

    def test_groups_present(self, all_green):
        groups = [g["group"] for g in env_check.self_check()["checks"]]
        assert groups == ["runtime", "dependencies", "decoder", "model", "config"]

    def test_platform_recorded(self, all_green):
        import platform
        assert env_check.self_check()["platform"]["system"] == platform.system()

    def test_model_size_from_config(self, isolated_cache, make_model_dir):
        make_model_dir(isolated_cache / "small")
        config_file = isolated_cache / "small.yaml"
        config_file.write_text("model:\n  size: small\n", encoding="utf-8")
        report = env_check.self_check(config_path=str(config_file))
        model_items = [i for g in report["checks"] if g["group"] == "model" for i in g["items"]]
        assert model_items[0]["name"] == "faster-whisper-small"


class TestRenderText:
    def test_contains_status_header(self, isolated_cache):
        text = env_check.render_text(env_check.self_check())
        assert "环境自检" in text

    def test_marks_each_status(self, isolated_cache, make_model_dir):
        make_model_dir(isolated_cache / "base")
        text = env_check.render_text(env_check.self_check())
        assert "❌" in text or "⚠" in text or "✅" in text

    def test_ends_with_conclusion(self, isolated_cache):
        assert "结论:" in env_check.render_text(env_check.self_check())

    def test_hints_are_rendered(self, isolated_cache):
        text = env_check.render_text(env_check.self_check())
        assert "→" in text

    def test_handles_multiline_hint(self, isolated_cache):
        """多行 hint（如模型未找到时的扫描位置清单）每行都要缩进渲染。"""
        report = env_check.self_check()
        text = env_check.render_text(report)
        assert "已扫描位置" in text


class TestModuleVersionHelper:
    def test_returns_none_for_missing(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "definitely_not_installed_xyz", None)
        assert env_check._module_version("definitely_not_installed_xyz") is None

    def test_returns_version_string(self, monkeypatch):
        fake = types.ModuleType("fake_pkg")
        fake.__version__ = "9.9"
        monkeypatch.setitem(sys.modules, "fake_pkg", fake)
        assert env_check._module_version("fake_pkg") == "9.9"

    def test_unknown_when_no_version_attr(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "fake_pkg2", types.ModuleType("fake_pkg2"))
        assert env_check._module_version("fake_pkg2") == "unknown"
