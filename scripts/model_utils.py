"""faster-whisper 模型下载与定位工具。

设计思路：国内网络访问 Hugging Face 经常超时或需要额外配置，而 ModelScope（魔搭）
是阿里云的国内镜像社区，下载稳定且免翻墙。所以下载优先级是：

  1. 本地缓存 —— 已经下载过就直接用，不产生任何网络请求。
  2. ModelScope 镜像 —— 国内首选，通常几秒到几十秒就能下完（base 模型约 145MB）。
  3. hf-mirror.com —— ModelScope 上如果找不到对应仓库，退到这个国内镜像站。
  4. 官方 Hugging Face —— 最后兜底，网络不通的话大概率会失败。

任何一步失败都不是致命错误，只有全部失败才应该停下来向用户求助。

约定：本模块所有面向人的进度信息一律写 **stderr**。stdout 必须保持干净，
因为上层 transcribe.py 要把转录结果以纯 JSON 打到 stdout 供机器消费。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List, Optional

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

DEFAULT_CACHE_ROOT = Path.home() / ".cache" / "faster-whisper-models"

# 允许用环境变量整体迁移缓存位置（例如挂到大盘或容器卷）。
MODEL_CACHE_ROOT = Path(os.environ.get("FASTER_WHISPER_CACHE_ROOT", str(DEFAULT_CACHE_ROOT)))

# ModelScope 的 snapshot_download(cache_dir=...) 会把文件放在这个子目录下，
# 层级是 <cache_dir>/models/<owner>--<repo>/snapshots/<revision>/。
# 与 hf-mirror 走的 local_dir=<cache_root>/<size> 完全不同，两边都要扫。
MODELSCOPE_CACHE_DIRNAME = "_ms_cache"

# 一个能被 faster-whisper 直接加载的 CTranslate2 模型目录必须有的文件。
REQUIRED_MODEL_FILES = ("model.bin", "config.json", "tokenizer.json")
# 词表文件名随模型规格不同而不同：base/small/medium 用 .txt，large-v3 用 .json。
VOCAB_FILE_CANDIDATES = ("vocabulary.txt", "vocabulary.json")
# 可选文件：faster-whisper 自带 log-mel 特征提取器，缺它也能跑。
OPTIONAL_MODEL_FILES = ("preprocessor_config.json",)

HF_MIRROR_ENDPOINT = "https://hf-mirror.com"
HF_OFFICIAL_ENDPOINT = "https://huggingface.co"
HF_MIRROR_TREE_URL = "https://hf-mirror.com/Systran/faster-whisper-{size}/tree/main"

# ModelScope 上常见的镜像命名沿用了原始的 "Systran/faster-whisper-base"，
# 但不同上传者可能用了不同的命名空间，因此列了几个候选，按顺序尝试。
MODELSCOPE_NAMESPACE_CANDIDATES = ("pengzhendong", "keepitsimple", "Systran")


def _log(message: str) -> None:
    """进度信息统一走 stderr，保证 stdout 只有 JSON。"""
    print(message, file=sys.stderr)


# ---------------------------------------------------------------------------
# 本地模型识别
# ---------------------------------------------------------------------------

def missing_model_files(path: Path) -> List[str]:
    """返回一个目录里缺失的模型文件；空列表表示这是一个完整可用的模型目录。"""
    if not path.is_dir():
        return list(REQUIRED_MODEL_FILES) + ["vocabulary.(txt|json)"]

    missing = [name for name in REQUIRED_MODEL_FILES if not (path / name).exists()]
    if not any((path / name).exists() for name in VOCAB_FILE_CANDIDATES):
        missing.append("vocabulary.(txt|json)")
    return missing


def _local_model_ready(path: Path) -> bool:
    """判断某个目录是否已经是一个完整的 faster-whisper 模型（而不是下载到一半）。"""
    return not missing_model_files(path)


def modelscope_snapshot_dirs() -> List[Path]:
    """列出 ModelScope 缓存里所有已下载的 snapshot 目录。

    这一层是 v0.1 的一个真实缺陷来源：ModelScope 下载落在
    ``_ms_cache/models/<owner>--<repo>/snapshots/<revision>/``，
    而旧版 find_local_model() 只扫 ``<cache_root>/<size>``，
    导致模型明明在本地却每次重新走下载流程，断网时直接退出。
    """
    root = MODEL_CACHE_ROOT / MODELSCOPE_CACHE_DIRNAME / "models"
    if not root.is_dir():
        return []
    return sorted(p for p in root.glob("*--faster-whisper-*/snapshots/*") if p.is_dir())


def local_model_candidates(model_size: str = "base") -> List[Path]:
    """按优先级列出所有可能已经存在模型的目录。"""
    candidates = [
        # 1. 用户显式指定的目录
        Path(os.environ["FASTER_WHISPER_MODEL_DIR"]) if os.environ.get("FASTER_WHISPER_MODEL_DIR") else None,
        # 2. hf-mirror / HF 官方的 local_dir 落点
        MODEL_CACHE_ROOT / model_size,
    ]
    # 3. ModelScope 的 snapshot 落点
    candidates.extend(modelscope_snapshot_dirs())
    return [c for c in candidates if c is not None]


def find_local_model(model_size: str = "base") -> Optional[str]:
    """在常见位置找已经下载好的模型，找到就直接返回路径，避免重复下载。"""
    for candidate in local_model_candidates(model_size):
        if _local_model_ready(candidate):
            return str(candidate)
    return None


# ---------------------------------------------------------------------------
# Hugging Face endpoint 处理
# ---------------------------------------------------------------------------

def _force_hf_endpoint(url: str) -> None:
    """把 HF 站点地址同时写进环境变量和已导入的 huggingface_hub 模块。

    只设 os.environ 是不够的：huggingface_hub 在 import 时就把 HF_ENDPOINT
    固化成模块级常量 ``constants.ENDPOINT``，并被 ``hf_api`` 等子模块
    from-import 复制了一份。而 modelscope 往往会先把 huggingface_hub 带进
    当前进程 —— 等轮到 hf-mirror 兜底时，常量早已指向官方站，镜像层形同虚设。

    所以这里对 sys.modules 中所有 huggingface_hub 子模块的 ENDPOINT 做覆盖。
    对尚未导入的情况，os.environ 仍然生效，两条路径都覆盖到。
    """
    os.environ["HF_ENDPOINT"] = url
    for name, module in list(sys.modules.items()):
        if name != "huggingface_hub" and not name.startswith("huggingface_hub."):
            continue
        if getattr(module, "ENDPOINT", None) is None:
            continue
        try:
            module.ENDPOINT = url
        except Exception:  # 只读模块属性，覆盖失败不影响主流程
            pass


# ---------------------------------------------------------------------------
# 三个下载来源
# ---------------------------------------------------------------------------

def _try_modelscope(model_size: str) -> Optional[str]:
    try:
        from modelscope import snapshot_download
    except ImportError:
        _log("  未安装 modelscope，跳过（可运行 `pip install modelscope` 后重试）")
        return None

    cache_dir = str(MODEL_CACHE_ROOT / MODELSCOPE_CACHE_DIRNAME)
    for namespace in MODELSCOPE_NAMESPACE_CANDIDATES:
        repo_id = f"{namespace}/faster-whisper-{model_size}"
        try:
            _log(f"  尝试从 ModelScope 下载: {repo_id} ...")
            local_dir = snapshot_download(repo_id, cache_dir=cache_dir)
            ready = Path(local_dir)
            if _local_model_ready(ready):
                return str(ready)
            missing = missing_model_files(ready)
            _log(f"  {repo_id} 下载完成但缺少 {missing}，可能不是有效的 faster-whisper 仓库，换下一个")
        except Exception as exc:
            _log(f"  {repo_id} 下载失败: {exc}")
    return None


def _try_hf(endpoint: str, label: str, model_size: str) -> Optional[str]:
    """hf-mirror 与 HF 官方共用一套下载逻辑，只有 endpoint 不同。"""
    _force_hf_endpoint(endpoint)
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        _log("  未安装 huggingface_hub，跳过（可运行 `pip install huggingface_hub` 后重试）")
        return None
    # import 之后 endpoint 可能已被固化，再压一次确保生效。
    _force_hf_endpoint(endpoint)

    target = MODEL_CACHE_ROOT / model_size
    repo_id = f"Systran/faster-whisper-{model_size}"
    try:
        _log(f"  尝试从 {label} 下载: {repo_id} ...")
        local_dir = snapshot_download(repo_id, local_dir=str(target))
        ready = Path(local_dir)
        if _local_model_ready(ready):
            return str(ready)
        _log(f"  {label} 下载完成但缺少 {missing_model_files(ready)}")
    except Exception as exc:
        _log(f"  {label} 下载失败: {exc}")
    return None


def _try_hf_mirror(model_size: str) -> Optional[str]:
    return _try_hf(HF_MIRROR_ENDPOINT, "hf-mirror.com", model_size)


def _try_hf_official(model_size: str) -> Optional[str]:
    return _try_hf(HF_OFFICIAL_ENDPOINT, "Hugging Face 官方", model_size)


# ---------------------------------------------------------------------------
# 手动下载指引
# ---------------------------------------------------------------------------

def manual_download_hint(model_size: str) -> str:
    """四条自动通道全失败时给用户的手动下载指引。

    文件清单与 REQUIRED_MODEL_FILES / VOCAB_FILE_CANDIDATES 保持同源，
    避免文档与实际仓库内容对不上（v0.1 曾把 vocabulary.json 和
    preprocessor_config.json 列为必需，但 ModelScope 上的 base 仓库
    实际提供的是 vocabulary.txt，且没有 preprocessor_config.json）。
    """
    target_dir = MODEL_CACHE_ROOT / model_size
    required = " / ".join(REQUIRED_MODEL_FILES)
    optional = " / ".join(OPTIONAL_MODEL_FILES)
    return (
        "自动下载失败。可能是网络问题，请尝试以下方式之一：\n"
        f"  1. 手动访问 {HF_MIRROR_TREE_URL.format(size=model_size)}\n"
        f"     必需文件：{required}\n"
        f"     词表文件：{' 或 '.join(VOCAB_FILE_CANDIDATES)}（二者有其一即可，随模型规格而不同）\n"
        f"     可选文件：{optional}（faster-whisper 自带特征提取器，缺它也能跑）\n"
        f"     全部放到目录: {target_dir}\n"
        "  2. 或访问 https://modelscope.cn 搜索 "
        f"faster-whisper-{model_size} 手动下载，解压后用 --model-path 指向该目录\n"
        f"  3. 也可以用环境变量指定已有模型目录: export FASTER_WHISPER_MODEL_DIR=/path/to/model\n"
        "  4. 确认当前网络能访问上述站点后，重新运行本 Skill\n"
        "\n"
        "  运行 `python3 scripts/env_check.py --self-check` 可以确认环境是否已就绪。"
    )


# ---------------------------------------------------------------------------
# 对外主入口
# ---------------------------------------------------------------------------

def ensure_model(model_size: str = "base", model_path: Optional[str] = None) -> str:
    """确保本地有可用的 faster-whisper 模型，返回其本地目录路径。

    Args:
        model_size: 模型规格，默认 base（约 145MB）。
        model_path: 显式指定的本地模型目录；给了就只用它，不做任何下载。

    找不到就按 ModelScope -> hf-mirror -> Hugging Face 官方的顺序自动尝试下载，
    全部失败才提示用户手动处理并退出。
    """
    if model_path:
        explicit = Path(model_path)
        if _local_model_ready(explicit):
            _log(f"使用显式指定的模型目录: {explicit}")
            return str(explicit)
        missing = missing_model_files(explicit)
        _log(f"显式指定的模型目录不完整: {explicit}\n  缺少: {missing}")
        sys.exit(1)

    local = find_local_model(model_size)
    if local:
        _log(f"发现本地已缓存的模型: {local}")
        return local

    _log(f"本地未找到 faster-whisper-{model_size} 模型，开始自动下载（优先走 ModelScope 国内镜像）...")
    MODEL_CACHE_ROOT.mkdir(parents=True, exist_ok=True)

    for attempt in (_try_modelscope, _try_hf_mirror, _try_hf_official):
        result = attempt(model_size)
        if result:
            _log(f"模型下载完成: {result}")
            return result

    _log(manual_download_hint(model_size))
    sys.exit(1)


if __name__ == "__main__":
    size = sys.argv[1] if len(sys.argv) > 1 else "base"
    # 这里刻意走 stdout：本文件被直接执行时的产物就是路径本身，不是 JSON。
    print(ensure_model(size))
