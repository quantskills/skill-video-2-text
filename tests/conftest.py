"""Make scripts/ importable from tests + 共享 fixture。"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import config as config_mod  # noqa: E402

# 一个能被 faster-whisper 加载的 base 模型目录应有的文件（与 model_utils 常量同源）
COMPLETE_MODEL_FILES = ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt")


@pytest.fixture
def cfg():
    """内置默认配置，测试不依赖磁盘上的 YAML。"""
    return config_mod.load_config(str(REPO_ROOT / "config" / "does-not-exist.yaml"))


@pytest.fixture
def make_model_dir():
    """工厂 fixture：在指定目录造一个模型目录，可指定要写哪些文件。"""
    def _make(directory: Path, files=COMPLETE_MODEL_FILES) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        for name in files:
            (directory / name).write_text("stub", encoding="utf-8")
        return directory
    return _make


@pytest.fixture
def sample_result():
    """一份符合输出契约的转录结果。"""
    return {
        "ok": True,
        "source": {
            "path": "/tmp/demo.mp3",
            "name": "demo.mp3",
            "ext": ".mp3",
            "kind": "audio",
            "size_bytes": 1024,
        },
        "model_size": "base",
        "language": "zh",
        "language_probability": 0.99,
        "duration": 7.8,
        "segment_count": 2,
        "segments": [
            {"start": 0.0, "end": 3.2, "text": "呃 这个 项目 的话 我们 上周"},
            {"start": 3.2, "end": 7.8, "text": "已经 把 那个 接口 对齐 了"},
        ],
        "outputs": {},
    }
