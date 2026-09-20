"""HF endpoint 切换测试 —— v0.1 缺陷回归。

原实现先 `from huggingface_hub import snapshot_download`，再 `os.environ["HF_ENDPOINT"]=...`。
但 huggingface_hub 在 import 时就把 HF_ENDPOINT 固化成模块级常量，并被 hf_api 等子模块
from-import 复制了一份 —— 于是"hf-mirror 兜底层"实际仍然打官方站，国内必然超时。
"""
import os
import sys
import types

import pytest

from scripts import model_utils


@pytest.fixture
def fake_hf_modules(monkeypatch):
    """伪造一个"已经被 modelscope 提前导入过"的 huggingface_hub 模块树。

    三个模块各自持有一份 ENDPOINT，模拟真实的 constants + from-import 复制关系。
    """
    hub = types.ModuleType("huggingface_hub")
    constants = types.ModuleType("huggingface_hub.constants")
    hf_api = types.ModuleType("huggingface_hub.hf_api")
    hub.constants = constants
    for module in (hub, constants, hf_api):
        module.ENDPOINT = model_utils.HF_OFFICIAL_ENDPOINT
        monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    return {"hub": hub, "constants": constants, "hf_api": hf_api}


class TestForceHfEndpoint:
    def test_sets_environment_variable(self, fake_hf_modules):
        model_utils._force_hf_endpoint(model_utils.HF_MIRROR_ENDPOINT)
        assert os.environ["HF_ENDPOINT"] == model_utils.HF_MIRROR_ENDPOINT

    def test_patches_already_imported_constants(self, fake_hf_modules):
        """核心回归：已 import 的 constants.ENDPOINT 必须被覆盖。"""
        model_utils._force_hf_endpoint(model_utils.HF_MIRROR_ENDPOINT)
        assert fake_hf_modules["constants"].ENDPOINT == model_utils.HF_MIRROR_ENDPOINT

    def test_patches_copied_attribute_in_hf_api(self, fake_hf_modules):
        """hf_api 持有 from-import 复制的一份，也要一起覆盖。"""
        model_utils._force_hf_endpoint(model_utils.HF_MIRROR_ENDPOINT)
        assert fake_hf_modules["hf_api"].ENDPOINT == model_utils.HF_MIRROR_ENDPOINT

    def test_patches_top_level_module(self, fake_hf_modules):
        model_utils._force_hf_endpoint(model_utils.HF_MIRROR_ENDPOINT)
        assert fake_hf_modules["hub"].ENDPOINT == model_utils.HF_MIRROR_ENDPOINT

    def test_can_switch_back_to_official(self, fake_hf_modules):
        model_utils._force_hf_endpoint(model_utils.HF_MIRROR_ENDPOINT)
        model_utils._force_hf_endpoint(model_utils.HF_OFFICIAL_ENDPOINT)
        assert fake_hf_modules["constants"].ENDPOINT == model_utils.HF_OFFICIAL_ENDPOINT
        assert fake_hf_modules["hf_api"].ENDPOINT == model_utils.HF_OFFICIAL_ENDPOINT
        assert os.environ["HF_ENDPOINT"] == model_utils.HF_OFFICIAL_ENDPOINT

    def test_ignores_unrelated_modules(self, fake_hf_modules, monkeypatch):
        stranger = types.ModuleType("some_other_lib")
        stranger.ENDPOINT = "https://example.com"
        monkeypatch.setitem(sys.modules, "some_other_lib", stranger)
        model_utils._force_hf_endpoint(model_utils.HF_MIRROR_ENDPOINT)
        assert stranger.ENDPOINT == "https://example.com"

    def test_skips_modules_without_endpoint(self, fake_hf_modules, monkeypatch):
        bare = types.ModuleType("huggingface_hub.utils")
        monkeypatch.setitem(sys.modules, "huggingface_hub.utils", bare)
        model_utils._force_hf_endpoint(model_utils.HF_MIRROR_ENDPOINT)
        assert not hasattr(bare, "ENDPOINT")

    def test_tolerates_readonly_attribute(self, monkeypatch):
        """覆盖失败（只读属性）不能让整个下载流程崩掉。"""
        class Frozen(types.ModuleType):
            @property
            def ENDPOINT(self):
                return model_utils.HF_OFFICIAL_ENDPOINT

        frozen = Frozen("huggingface_hub.frozen")
        monkeypatch.setitem(sys.modules, "huggingface_hub.frozen", frozen)
        model_utils._force_hf_endpoint(model_utils.HF_MIRROR_ENDPOINT)
        assert frozen.ENDPOINT == model_utils.HF_OFFICIAL_ENDPOINT

    def test_works_when_nothing_imported(self, monkeypatch):
        """干净进程（hf_hub 尚未导入）走 os.environ 这条路。"""
        for name in list(sys.modules):
            if name.startswith("huggingface_hub"):
                monkeypatch.delitem(sys.modules, name, raising=False)
        model_utils._force_hf_endpoint(model_utils.HF_MIRROR_ENDPOINT)
        assert os.environ["HF_ENDPOINT"] == model_utils.HF_MIRROR_ENDPOINT


class TestEndpointConstants:
    def test_mirror_is_not_official(self):
        assert model_utils.HF_MIRROR_ENDPOINT != model_utils.HF_OFFICIAL_ENDPOINT
        assert "hf-mirror.com" in model_utils.HF_MIRROR_ENDPOINT

    def test_fallback_chain_order(self):
        """兜底顺序必须是 ModelScope -> hf-mirror -> HF 官方。"""
        chain = [model_utils._try_modelscope, model_utils._try_hf_mirror, model_utils._try_hf_official]
        assert chain[0].__name__ == "_try_modelscope"
        assert chain[1].__name__ == "_try_hf_mirror"
        assert chain[2].__name__ == "_try_hf_official"

    def test_mirror_and_official_share_one_implementation(self):
        """两个兜底层只差 endpoint，逻辑必须同源，避免修一个漏一个。"""
        import inspect
        mirror_src = inspect.getsource(model_utils._try_hf_mirror)
        official_src = inspect.getsource(model_utils._try_hf_official)
        assert "_try_hf(" in mirror_src
        assert "_try_hf(" in official_src


class TestTryHfWiring:
    def test_mirror_download_uses_mirror_endpoint(self, monkeypatch, tmp_path, fake_hf_modules):
        """_try_hf_mirror 真的把 endpoint 压成镜像站（而不是留在官方站）。"""
        monkeypatch.setattr(model_utils, "MODEL_CACHE_ROOT", tmp_path)
        observed = {}

        hub = types.ModuleType("huggingface_hub")

        def fake_snapshot_download(repo_id, local_dir=None, **kwargs):
            observed["env"] = os.environ.get("HF_ENDPOINT")
            observed["constants"] = fake_hf_modules["constants"].ENDPOINT
            observed["repo_id"] = repo_id
            observed["local_dir"] = local_dir
            raise RuntimeError("测试到此为止")

        hub.snapshot_download = fake_snapshot_download
        monkeypatch.setitem(sys.modules, "huggingface_hub", hub)

        assert model_utils._try_hf_mirror("base") is None
        assert observed["env"] == model_utils.HF_MIRROR_ENDPOINT
        assert observed["repo_id"] == "Systran/faster-whisper-base"
        assert observed["local_dir"] == str(tmp_path / "base")

    def test_official_download_uses_official_endpoint(self, monkeypatch, tmp_path, fake_hf_modules):
        monkeypatch.setattr(model_utils, "MODEL_CACHE_ROOT", tmp_path)
        observed = {}

        hub = types.ModuleType("huggingface_hub")

        def fake_snapshot_download(repo_id, local_dir=None, **kwargs):
            observed["env"] = os.environ.get("HF_ENDPOINT")
            raise RuntimeError("测试到此为止")

        hub.snapshot_download = fake_snapshot_download
        monkeypatch.setitem(sys.modules, "huggingface_hub", hub)

        assert model_utils._try_hf_official("base") is None
        assert observed["env"] == model_utils.HF_OFFICIAL_ENDPOINT

    def test_successful_mirror_download_returns_path(self, monkeypatch, tmp_path, make_model_dir):
        monkeypatch.setattr(model_utils, "MODEL_CACHE_ROOT", tmp_path)
        ready = make_model_dir(tmp_path / "base")

        hub = types.ModuleType("huggingface_hub")
        hub.snapshot_download = lambda repo_id, local_dir=None, **kw: str(ready)
        monkeypatch.setitem(sys.modules, "huggingface_hub", hub)

        assert model_utils._try_hf_mirror("base") == str(ready)

    def test_incomplete_mirror_download_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.setattr(model_utils, "MODEL_CACHE_ROOT", tmp_path)
        broken = tmp_path / "base"
        broken.mkdir(parents=True)
        (broken / "model.bin").write_text("stub", encoding="utf-8")

        hub = types.ModuleType("huggingface_hub")
        hub.snapshot_download = lambda repo_id, local_dir=None, **kw: str(broken)
        monkeypatch.setitem(sys.modules, "huggingface_hub", hub)

        assert model_utils._try_hf_mirror("base") is None

    def test_missing_huggingface_hub_degrades_gracefully(self, monkeypatch, capsys):
        """没装 huggingface_hub 时只打提示并返回 None，不抛异常。"""
        monkeypatch.setitem(sys.modules, "huggingface_hub", None)
        assert model_utils._try_hf_mirror("base") is None
        assert "huggingface_hub" in capsys.readouterr().err

    def test_missing_modelscope_degrades_gracefully(self, monkeypatch, capsys):
        monkeypatch.setitem(sys.modules, "modelscope", None)
        assert model_utils._try_modelscope("base") is None
        assert "modelscope" in capsys.readouterr().err
