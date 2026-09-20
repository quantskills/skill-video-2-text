"""模型定位与下载指引测试 —— 含 v0.1 缓存路径缺陷的回归。"""
import pytest

from scripts import model_utils


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    """把缓存根目录指到临时目录，并清掉环境变量覆盖，避免测试互相污染。"""
    monkeypatch.setattr(model_utils, "MODEL_CACHE_ROOT", tmp_path)
    monkeypatch.delenv("FASTER_WHISPER_MODEL_DIR", raising=False)
    monkeypatch.delenv("FASTER_WHISPER_CACHE_ROOT", raising=False)
    return tmp_path


def _ms_snapshot(cache_root, namespace="pengzhendong", revision="master",
                 files=("model.bin", "config.json", "tokenizer.json", "vocabulary.txt")):
    """造一个 ModelScope snapshot_download 的真实落点结构。"""
    snap = cache_root / "_ms_cache" / "models" / f"{namespace}--faster-whisper-base" / "snapshots" / revision
    snap.mkdir(parents=True, exist_ok=True)
    for name in files:
        (snap / name).write_text("stub", encoding="utf-8")
    return snap


class TestMissingModelFiles:
    def test_complete_dir_reports_nothing(self, tmp_path):
        d = tmp_path / "ok"
        d.mkdir()
        for name in ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt"):
            (d / name).write_text("x", encoding="utf-8")
        assert model_utils.missing_model_files(d) == []

    def test_nonexistent_dir_reports_all(self, tmp_path):
        missing = model_utils.missing_model_files(tmp_path / "ghost")
        assert "model.bin" in missing
        assert "vocabulary.(txt|json)" in missing

    def test_partial_download_detected(self, tmp_path):
        d = tmp_path / "half"
        d.mkdir()
        (d / "model.bin").write_text("x", encoding="utf-8")
        assert set(model_utils.missing_model_files(d)) >= {"config.json", "tokenizer.json"}

    def test_vocab_json_accepted(self, tmp_path):
        """large-v3 用 vocabulary.json 而不是 .txt，两者都该被接受。"""
        d = tmp_path / "v3"
        d.mkdir()
        for name in ("model.bin", "config.json", "tokenizer.json", "vocabulary.json"):
            (d / name).write_text("x", encoding="utf-8")
        assert model_utils.missing_model_files(d) == []

    def test_preprocessor_config_is_optional(self, tmp_path):
        """faster-whisper 自带特征提取器，缺 preprocessor_config.json 不该判为不完整。"""
        d = tmp_path / "nopc"
        d.mkdir()
        for name in ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt"):
            (d / name).write_text("x", encoding="utf-8")
        assert "preprocessor_config.json" not in model_utils.missing_model_files(d)
        assert "preprocessor_config.json" in model_utils.OPTIONAL_MODEL_FILES


class TestModelscopeDiscovery:
    """v0.1 缺陷回归：模型下载落在 _ms_cache/... 但 find_local_model 只扫 <root>/base，
    导致模型明明在本地却每次重新下载，断网时直接 exit(1)。"""

    def test_snapshot_dir_is_listed(self, isolated_cache):
        snap = _ms_snapshot(isolated_cache)
        assert snap in model_utils.modelscope_snapshot_dirs()

    def test_find_local_model_sees_modelscope_cache(self, isolated_cache):
        """核心回归：ModelScope 下载过的模型必须能被 find_local_model 找到。"""
        snap = _ms_snapshot(isolated_cache)
        assert model_utils.find_local_model("base") == str(snap)

    def test_incomplete_snapshot_not_returned(self, isolated_cache):
        _ms_snapshot(isolated_cache, files=("model.bin",))
        assert model_utils.find_local_model("base") is None

    def test_base_dir_still_found(self, isolated_cache, make_model_dir):
        """hf-mirror / HF 官方的 local_dir 落点必须继续有效（不能修一个坏另一个）。"""
        base = make_model_dir(isolated_cache / "base")
        assert model_utils.find_local_model("base") == str(base)

    def test_explicit_base_dir_wins_over_modelscope(self, isolated_cache, make_model_dir):
        make_model_dir(isolated_cache / "base")
        _ms_snapshot(isolated_cache)
        assert model_utils.find_local_model("base") == str(isolated_cache / "base")

    def test_no_cache_at_all(self, isolated_cache):
        assert model_utils.modelscope_snapshot_dirs() == []
        assert model_utils.find_local_model("base") is None

    def test_multiple_revisions_all_scanned(self, isolated_cache):
        _ms_snapshot(isolated_cache, revision="master", files=("model.bin",))
        good = _ms_snapshot(isolated_cache, namespace="keepitsimple", revision="v1")
        assert model_utils.find_local_model("base") == str(good)


class TestLocalModelCandidates:
    def test_env_override_has_top_priority(self, isolated_cache, monkeypatch, make_model_dir):
        custom = make_model_dir(isolated_cache / "custom")
        monkeypatch.setenv("FASTER_WHISPER_MODEL_DIR", str(custom))
        assert model_utils.local_model_candidates("base")[0] == custom

    def test_size_specific_dir_included(self, isolated_cache):
        assert (isolated_cache / "small") in model_utils.local_model_candidates("small")

    def test_returns_list_of_paths(self, isolated_cache):
        candidates = model_utils.local_model_candidates("base")
        assert isinstance(candidates, list)
        assert all(isinstance(c, type(isolated_cache)) for c in candidates)


class TestManualDownloadHint:
    """v0.1 缺陷回归：指引把 vocabulary.json / preprocessor_config.json 列为必需，
    但 ModelScope 上 base 仓库实际给的是 vocabulary.txt，且没有 preprocessor_config.json。"""

    def test_lists_required_files_from_single_source(self):
        hint = model_utils.manual_download_hint("base")
        for name in model_utils.REQUIRED_MODEL_FILES:
            assert name in hint

    def test_vocab_presented_as_alternatives(self):
        hint = model_utils.manual_download_hint("base")
        assert "vocabulary.txt" in hint
        assert "vocabulary.json" in hint
        assert "二者有其一即可" in hint

    def test_preprocessor_marked_optional(self):
        hint = model_utils.manual_download_hint("base")
        assert "可选文件" in hint
        assert "preprocessor_config.json" in hint

    def test_mentions_size_and_env_override(self, isolated_cache):
        hint = model_utils.manual_download_hint("small")
        assert "faster-whisper-small" in hint
        assert "FASTER_WHISPER_MODEL_DIR" in hint
        assert str(isolated_cache / "small") in hint

    def test_points_at_mirror_not_official(self):
        assert "hf-mirror.com" in model_utils.manual_download_hint("base")

    def test_hint_matches_actually_downloaded_files(self, isolated_cache):
        """指引里承诺的必需文件，必须真的能从 ModelScope 仓库拿到。"""
        snap = _ms_snapshot(isolated_cache)
        actual = {p.name for p in snap.iterdir()}
        hint = model_utils.manual_download_hint("base")
        for name in model_utils.REQUIRED_MODEL_FILES:
            assert name in hint
            assert name in actual
        assert actual & set(model_utils.VOCAB_FILE_CANDIDATES)


class TestEnsureModel:
    def test_reuses_local_cache_without_network(self, isolated_cache, make_model_dir, monkeypatch, capsys):
        base = make_model_dir(isolated_cache / "base")

        def _boom(*a, **k):  # 一旦走到下载就必须失败，证明没发起网络请求
            raise AssertionError("本地已有模型，不应该触发下载")

        monkeypatch.setattr(model_utils, "_try_modelscope", _boom)
        monkeypatch.setattr(model_utils, "_try_hf_mirror", _boom)
        monkeypatch.setattr(model_utils, "_try_hf_official", _boom)

        assert model_utils.ensure_model("base") == str(base)
        assert capsys.readouterr().out == ""

    def test_reuses_modelscope_cache_without_network(self, isolated_cache, monkeypatch):
        snap = _ms_snapshot(isolated_cache)
        monkeypatch.setattr(model_utils, "_try_modelscope",
                            lambda *a, **k: pytest.fail("不应触发下载"))
        assert model_utils.ensure_model("base") == str(snap)

    def test_explicit_model_path_honoured(self, isolated_cache, make_model_dir):
        custom = make_model_dir(isolated_cache / "custom")
        assert model_utils.ensure_model("base", model_path=str(custom)) == str(custom)

    def test_incomplete_explicit_path_exits(self, isolated_cache, tmp_path):
        broken = tmp_path / "broken"
        broken.mkdir()
        (broken / "model.bin").write_text("x", encoding="utf-8")
        with pytest.raises(SystemExit) as exc:
            model_utils.ensure_model("base", model_path=str(broken))
        assert exc.value.code == 1

    def test_progress_goes_to_stderr(self, isolated_cache, make_model_dir, capsys):
        """v0.1 缺陷回归：进度信息绝不能进 stdout，否则上层 JSON 无法解析。"""
        make_model_dir(isolated_cache / "base")
        model_utils.ensure_model("base")
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "发现本地已缓存的模型" in captured.err
