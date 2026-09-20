"""配置加载测试 —— 含 YAML 与内置默认值的一致性防漂移校验。"""
import sys

import pytest

from scripts import config as config_mod

REAL_CONFIG = config_mod.REPO_ROOT / "config" / "transcribe_config.yaml"


def _key_tree(mapping, prefix=""):
    """递归收集所有叶子键路径，用于结构对比。"""
    paths = set()
    for key, value in mapping.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            paths |= _key_tree(value, path)
        else:
            paths.add(path)
    return paths


class TestDefaults:
    def test_missing_file_falls_back(self, tmp_path):
        cfg = config_mod.load_config(str(tmp_path / "ghost.yaml"))
        assert cfg == config_mod.DEFAULT_CONFIG

    def test_fallback_is_a_deep_copy(self, tmp_path):
        """返回副本，调用方改动不能污染模块级常量。"""
        cfg = config_mod.load_config(str(tmp_path / "ghost.yaml"))
        cfg["model"]["size"] = "large-v3"
        assert config_mod.DEFAULT_CONFIG["model"]["size"] == "base"

    def test_default_sections_present(self):
        assert set(config_mod.DEFAULT_CONFIG) == {
            "model", "download", "transcribe", "input", "output"
        }

    def test_default_model_is_base_cpu_int8(self):
        model = config_mod.DEFAULT_CONFIG["model"]
        assert model["size"] == "base"
        assert model["device"] == "cpu"
        assert model["compute_type"] == "int8"

    def test_default_download_sources_order(self):
        """下载顺序即优先级，国内网络必须 ModelScope 打头。"""
        assert config_mod.DEFAULT_CONFIG["download"]["sources"] == [
            "modelscope", "hf_mirror", "hf_official"
        ]

    def test_default_supported_formats(self):
        inp = config_mod.DEFAULT_CONFIG["input"]
        assert ".mp3" in inp["audio_extensions"]
        assert ".mp4" in inp["video_extensions"]


class TestDeepMerge:
    def test_override_scalar(self):
        merged = config_mod._deep_merge({"a": {"b": 1}}, {"a": {"b": 2}})
        assert merged == {"a": {"b": 2}}

    def test_keeps_unmentioned_siblings(self):
        merged = config_mod._deep_merge({"a": {"b": 1, "c": 2}}, {"a": {"b": 9}})
        assert merged == {"a": {"b": 9, "c": 2}}

    def test_adds_new_keys(self):
        merged = config_mod._deep_merge({"a": {"b": 1}}, {"a": {"z": 26}})
        assert merged == {"a": {"b": 1, "z": 26}}

    def test_replaces_list_whole(self):
        """列表不做元素级合并，整体替换，避免意外拼接。"""
        merged = config_mod._deep_merge({"a": [1, 2, 3]}, {"a": [9]})
        assert merged == {"a": [9]}

    def test_dict_overrides_scalar(self):
        assert config_mod._deep_merge({"a": 1}, {"a": {"b": 2}}) == {"a": {"b": 2}}

    def test_none_override_wins(self):
        assert config_mod._deep_merge({"a": 1}, {"a": None}) == {"a": None}

    def test_does_not_mutate_inputs(self):
        base, override = {"a": {"b": 1}}, {"a": {"b": 2}}
        config_mod._deep_merge(base, override)
        assert base == {"a": {"b": 1}}
        assert override == {"a": {"b": 2}}

    def test_tolerates_none_override(self):
        assert config_mod._deep_merge({"a": 1}, None) == {"a": 1}


@pytest.mark.skipif(not REAL_CONFIG.is_file(), reason="配置文件不存在")
class TestRealConfigFile:
    def test_loads_without_error(self):
        cfg = config_mod.load_config(str(REAL_CONFIG))
        assert isinstance(cfg, dict)
        assert "model" in cfg

    def test_structure_matches_defaults(self):
        """防漂移：YAML 的键结构必须与 DEFAULT_CONFIG 完全一致。

        config.py 里 DEFAULT_CONFIG 是 pyyaml 缺失时的降级路径，
        两边不同步会导致"装了 yaml 和没装 yaml 行为不一致"这种极难排查的问题。
        """
        pytest.importorskip("yaml")
        import yaml

        loaded = yaml.safe_load(REAL_CONFIG.read_text(encoding="utf-8"))
        assert _key_tree(loaded) == _key_tree(config_mod.DEFAULT_CONFIG)

    def test_scalar_values_match_defaults(self):
        """防漂移：YAML 里的默认值也要和内置默认值一致。"""
        pytest.importorskip("yaml")
        loaded = config_mod.load_config(str(REAL_CONFIG))
        assert loaded == config_mod.DEFAULT_CONFIG

    def test_config_source_points_at_file(self):
        pytest.importorskip("yaml")
        assert config_mod.config_source(str(REAL_CONFIG)) == str(REAL_CONFIG)


class TestConfigSource:
    def test_reports_missing_file(self, tmp_path):
        assert "配置文件不存在" in config_mod.config_source(str(tmp_path / "ghost.yaml"))

    def test_reports_when_pyyaml_absent(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "yaml", None)
        assert "pyyaml 未安装" in config_mod.config_source(str(REAL_CONFIG))

    def test_load_falls_back_when_pyyaml_absent(self, monkeypatch):
        """pyyaml 缺失是降级，不是崩溃 —— 这是本 Skill 的刻意设计。"""
        monkeypatch.setitem(sys.modules, "yaml", None)
        cfg = config_mod.load_config(str(REAL_CONFIG))
        assert cfg == config_mod.DEFAULT_CONFIG

    def test_malformed_yaml_falls_back(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("model: [unclosed", encoding="utf-8")
        assert config_mod.load_config(str(bad)) == config_mod.DEFAULT_CONFIG

    def test_non_mapping_yaml_falls_back(self, tmp_path):
        weird = tmp_path / "weird.yaml"
        weird.write_text("- just\n- a\n- list\n", encoding="utf-8")
        assert config_mod.load_config(str(weird)) == config_mod.DEFAULT_CONFIG

    def test_empty_yaml_falls_back(self, tmp_path):
        empty = tmp_path / "empty.yaml"
        empty.write_text("", encoding="utf-8")
        assert config_mod.load_config(str(empty)) == config_mod.DEFAULT_CONFIG

    def test_partial_yaml_merged_over_defaults(self, tmp_path):
        pytest.importorskip("yaml")
        partial = tmp_path / "partial.yaml"
        partial.write_text("model:\n  size: small\n", encoding="utf-8")
        cfg = config_mod.load_config(str(partial))
        assert cfg["model"]["size"] == "small"
        assert cfg["model"]["device"] == "cpu"  # 未提到的键保留默认
        assert cfg["transcribe"]["beam_size"] == 5
