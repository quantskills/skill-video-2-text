"""配置加载：YAML 优先，缺失时降级到内置默认值。

刻意不硬依赖 pyyaml —— 本 Skill 的核心价值是"离线把语音变成文字"，
不应该因为一个可选依赖没装就整体跑不起来。pyyaml 在 requirements.txt 里，
正常安装后走 YAML 分支；没装时用 DEFAULT_CONFIG，行为完全一致（因为
DEFAULT_CONFIG 与 config/transcribe_config.yaml 内容同源）。
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "transcribe_config.yaml"

# 与 config/transcribe_config.yaml 同源；改一边记得改另一边（tests/test_config.py 会做一致性校验）。
DEFAULT_CONFIG: Dict[str, Any] = {
    "model": {
        "size": "base",
        "device": "cpu",
        "compute_type": "int8",
        "path": None,
    },
    "download": {
        "sources": ["modelscope", "hf_mirror", "hf_official"],
        "hf_mirror_endpoint": "https://hf-mirror.com",
        "hf_official_endpoint": "https://huggingface.co",
        "cache_root": None,
    },
    "transcribe": {
        "beam_size": 5,
        "best_of": 5,
        "vad_filter": True,
        "vad_min_silence_ms": 2000,
        "temperature": 0.0,
        "condition_on_previous_text": False,
        "language": None,
        "initial_prompt": None,
    },
    "input": {
        "audio_extensions": [".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".wma", ".opus"],
        "video_extensions": [".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv", ".wmv", ".m4v"],
        "max_size_mb": 4096,
    },
    "output": {
        "dir": "output",
        "write_json": True,
        "write_plain_text": True,
        "json_indent": 2,
    },
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """递归合并，override 里的值覆盖 base；两边都不是 dict 时直接取 override。"""
    merged = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def load_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """加载配置。文件不存在、pyyaml 未安装或解析失败都降级到 DEFAULT_CONFIG。"""
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    if not path.is_file():
        return copy.deepcopy(DEFAULT_CONFIG)
    try:
        import yaml
    except ImportError:
        return copy.deepcopy(DEFAULT_CONFIG)
    try:
        with path.open("r", encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle) or {}
    except Exception:
        return copy.deepcopy(DEFAULT_CONFIG)
    if not isinstance(loaded, dict):
        return copy.deepcopy(DEFAULT_CONFIG)
    return _deep_merge(DEFAULT_CONFIG, loaded)


def config_source(config_path: Optional[str] = None) -> str:
    """返回本次配置的实际来源，供 env_check 自检展示。"""
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    if not path.is_file():
        return "builtin-defaults (配置文件不存在)"
    try:
        import yaml  # noqa: F401
    except ImportError:
        return "builtin-defaults (pyyaml 未安装)"
    return str(path)
