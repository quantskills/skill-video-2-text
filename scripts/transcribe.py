#!/usr/bin/env python3
"""用 faster-whisper 转录音频/视频，输出带时间戳的原始分段文本（JSON）。

这一步只做「语音变文字」，不做纠错/标点/摘要 —— 那些交给上层的大模型后处理，
因为语言模型比语音模型更擅长理解语义、修正错别字和组织行文。

用法：
    python3 scripts/transcribe.py <输入文件> [--language zh] [--model-size base]
    python3 scripts/transcribe.py <输入文件> --config config/transcribe_config.yaml

输出契约（stdout 永远是且仅是**一个** JSON 对象）：
    成功：{"ok": true, "language": "zh", "language_probability": 0.99,
           "duration": 123.4, "segments": [{"start": 0.0, "end": 3.2, "text": "..."}],
           "outputs": {...}}
    失败：{"ok": false, "error": {"code": "...", "message": "...", "hint": "..."}}

    进度、模型下载日志、依赖告警一律走 stderr，绝不污染 stdout。
    退出码 0 = 成功，非 0 = 失败（与 error.code 对应）。

视频文件会被 faster-whisper 底层的 PyAV 自动解码出音轨，不需要手动用 ffmpeg 先转换。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import config as config_mod
from scripts import model_utils


class InputValidationError(ValueError):
    """输入校验失败。code 用于机器消费方分支处理，message 给人看。"""

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint


# ---------------------------------------------------------------------------
# 判断层：纯函数，输入状态返回结论
# ---------------------------------------------------------------------------

def classify_extension(ext: str, cfg: Dict[str, Any]) -> Optional[str]:
    """返回 'audio' / 'video' / None（不支持）。"""
    ext = ext.lower()
    if ext in [e.lower() for e in cfg["input"]["audio_extensions"]]:
        return "audio"
    if ext in [e.lower() for e in cfg["input"]["video_extensions"]]:
        return "video"
    return None


def validate_input(input_path: str | Path, cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """校验输入文件，通过则返回归一化后的描述 dict，不通过抛 InputValidationError。

    校验项：路径存在 -> 是普通文件 -> 扩展名受支持 -> 可读 -> 非空 -> 未超体积上限。
    """
    cfg = cfg or config_mod.load_config()
    path = Path(input_path).expanduser()

    if not path.exists():
        raise InputValidationError(
            "input_not_found",
            f"文件不存在: {path}",
            "请确认路径拼写；含空格或中文的路径需要加引号。",
        )
    if not path.is_file():
        raise InputValidationError(
            "input_not_a_file",
            f"路径不是普通文件: {path}",
            "本 Skill 只处理单个音视频文件，不接受目录或设备节点。",
        )

    ext = path.suffix.lower()
    kind = classify_extension(ext, cfg)
    if kind is None:
        supported = sorted(
            set(e.lower() for e in cfg["input"]["audio_extensions"])
            | set(e.lower() for e in cfg["input"]["video_extensions"])
        )
        raise InputValidationError(
            "unsupported_format",
            f"不支持的文件格式: {ext or '(无扩展名)'}",
            f"支持的扩展名: {', '.join(supported)}。"
            "其他格式请先用 ffmpeg 转成 wav/mp3/mp4 再试。",
        )

    try:
        with path.open("rb"):
            pass
    except OSError as exc:
        raise InputValidationError(
            "input_unreadable",
            f"文件无法读取: {path}（{exc}）",
            "检查文件权限，或确认它没有被其他进程独占。",
        )

    size_bytes = path.stat().st_size
    if size_bytes == 0:
        raise InputValidationError(
            "input_empty",
            f"文件内容为空: {path}",
            "0 字节文件无法解码，请重新导出或下载。",
        )

    max_bytes = int(cfg["input"]["max_size_mb"]) * 1024 * 1024
    if size_bytes > max_bytes:
        raise InputValidationError(
            "input_too_large",
            f"文件体积 {size_bytes / 1048576:.1f}MB 超过上限 {cfg['input']['max_size_mb']}MB",
            "可调大 config/transcribe_config.yaml 里 input.max_size_mb，或先切分文件。",
        )

    return {
        "path": str(path.resolve()),
        "name": path.name,
        "ext": ext,
        "kind": kind,
        "size_bytes": size_bytes,
    }


# ---------------------------------------------------------------------------
# 决策层：核心转录
# ---------------------------------------------------------------------------

def _load_whisper_model(cfg: Dict[str, Any], model_size: str, model_path: Optional[str]):
    """加载 WhisperModel；缺依赖或模型不可用时抛 InputValidationError（带明确 code）。"""
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise InputValidationError(
            "dependency_missing",
            f"未安装 faster-whisper: {exc}",
            "请运行: pip install -r requirements.txt"
            "（国内网络建议加 -i https://mirrors.aliyun.com/pypi/simple/）",
        )

    resolved = model_utils.ensure_model(model_size=model_size, model_path=model_path or cfg["model"]["path"])

    device = cfg["model"]["device"]
    if device == "auto":
        device = _detect_device()

    try:
        return WhisperModel(
            resolved,
            device=device,
            compute_type=cfg["model"]["compute_type"],
            local_files_only=True,
        )
    except Exception as exc:
        raise InputValidationError(
            "model_load_failed",
            f"模型加载失败（{resolved}, device={device}, compute_type={cfg['model']['compute_type']}）: {exc}",
            "int8 在部分平台不受支持，可把 config 里 model.compute_type 改成 float32 重试。",
        )


def _detect_device() -> str:
    """auto 设备探测：有 CUDA 用 cuda，否则 cpu。刻意不引入 torch 依赖。"""
    try:
        import ctranslate2

        if hasattr(ctranslate2, "get_cuda_device_count") and ctranslate2.get_cuda_device_count() > 0:
            return "cuda"
    except Exception:
        pass
    return "cpu"


def _build_transcribe_kwargs(cfg: Dict[str, Any], language: Optional[str],
                             initial_prompt: Optional[str]) -> Dict[str, Any]:
    tcfg = cfg["transcribe"]
    kwargs: Dict[str, Any] = {
        "beam_size": int(tcfg["beam_size"]),
        "best_of": int(tcfg["best_of"]),
        "temperature": float(tcfg["temperature"]),
        "condition_on_previous_text": bool(tcfg["condition_on_previous_text"]),
        "vad_filter": bool(tcfg["vad_filter"]),
    }
    if kwargs["vad_filter"]:
        kwargs["vad_parameters"] = {"min_silence_duration_ms": int(tcfg["vad_min_silence_ms"])}

    effective_language = language or tcfg.get("language")
    if effective_language:
        kwargs["language"] = effective_language
    prompt = initial_prompt or tcfg.get("initial_prompt")
    if prompt:
        kwargs["initial_prompt"] = prompt
    return kwargs


def transcribe(input_path: str | Path,
               language: Optional[str] = None,
               model_size: Optional[str] = None,
               model_path: Optional[str] = None,
               initial_prompt: Optional[str] = None,
               cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """转录单个文件，返回结果 dict（不落盘）。

    这是给外部 agent 直接 import 调用的核心函数，不做任何 print。
    """
    cfg = cfg or config_mod.load_config()
    info = validate_input(input_path, cfg)
    size = model_size or cfg["model"]["size"]

    model = _load_whisper_model(cfg, size, model_path)
    kwargs = _build_transcribe_kwargs(cfg, language, initial_prompt)

    try:
        segments_iter, meta = model.transcribe(info["path"], **kwargs)
        segments: List[Dict[str, Any]] = []
        for seg in segments_iter:
            text = (seg.text or "").strip()
            if not text:
                continue
            segments.append({
                "start": round(float(seg.start), 2),
                "end": round(float(seg.end), 2),
                "text": text,
            })
    except Exception as exc:
        message = str(exc)
        code = "transcribe_failed"
        hint = "请把完整 stderr 反馈给维护者。"
        lowered = message.lower()
        if any(token in lowered for token in ("invalid data", "unknown format", "decoder", "av_", "errno", "moov")):
            code = "decode_failed"
            hint = (
                "这通常是解码器问题。faster-whisper 依赖的 PyAV 自带静态 ffmpeg 解码库，"
                "但精简版 Python 环境可能不完整。请安装系统 ffmpeg 后重试："
                "macOS `brew install ffmpeg` / Windows `winget install ffmpeg` / "
                "Debian·Ubuntu `sudo apt install ffmpeg`。"
            )
        raise InputValidationError(code, f"转录失败: {message}", hint)

    return {
        "ok": True,
        "source": info,
        "model_size": size,
        "language": meta.language,
        "language_probability": round(float(meta.language_probability), 3),
        "duration": round(float(meta.duration), 2),
        "segment_count": len(segments),
        "segments": segments,
    }


# ---------------------------------------------------------------------------
# 输出层
# ---------------------------------------------------------------------------

def _format_timestamp(seconds: float) -> str:
    total = int(round(seconds))
    return f"{total // 60:02d}:{total % 60:02d}"


def render_plain_text(result: Dict[str, Any]) -> str:
    """把分段拼成纯文本稿（不含时间戳），便于直接检索和引用。"""
    return "\n".join(seg["text"] for seg in result["segments"])


def render_timeline(result: Dict[str, Any]) -> str:
    """渲染成 [mm:ss] 时间轴稿，便于回原音频对照。"""
    lines = []
    for seg in result["segments"]:
        lines.append(f"[{_format_timestamp(seg['start'])} - {_format_timestamp(seg['end'])}] {seg['text']}")
    return "\n".join(lines)


def write_outputs(result: Dict[str, Any], output_dir: str | Path, cfg: Dict[str, Any]) -> Dict[str, str]:
    """按配置落盘原始 JSON / 纯文本 / 时间轴稿，返回实际写出的文件路径。"""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = Path(result["source"]["name"]).stem
    written: Dict[str, str] = {}

    if cfg["output"]["write_json"]:
        json_path = out / f"{stem}.transcript.json"
        json_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=int(cfg["output"]["json_indent"])),
            encoding="utf-8",
        )
        written["json"] = str(json_path)

    if cfg["output"]["write_plain_text"]:
        txt_path = out / f"{stem}.transcript.txt"
        txt_path.write_text(render_plain_text(result) + "\n", encoding="utf-8")
        written["plain_text"] = str(txt_path)

    timeline_path = out / f"{stem}.timeline.txt"
    timeline_path.write_text(render_timeline(result) + "\n", encoding="utf-8")
    written["timeline"] = str(timeline_path)

    return written


# ---------------------------------------------------------------------------
# 标准 agent 入口
# ---------------------------------------------------------------------------

def run(input_path: str,
        language: Optional[str] = None,
        model_size: Optional[str] = None,
        model_path: Optional[str] = None,
        initial_prompt: Optional[str] = None,
        output_dir: Optional[str] = None,
        config: Optional[str] = None,
        write_files: bool = True,
        **kwargs: Any) -> Dict[str, Any]:
    """Standard entry point for external agents.

    Args:
        input_path:    音频或视频文件路径。
        language:      语言代码（zh/en/...）；None 表示自动检测，也尊重 config 里的默认值。
        model_size:    模型规格，默认取 config 的 model.size（base）。
        model_path:    显式指定本地模型目录；给了就不做任何下载。
        initial_prompt: 领域提示词，改善专有名词识别。
        output_dir:    产物目录，默认 config 的 output.dir（相对仓库根）。
        config:        配置文件路径，默认 config/transcribe_config.yaml。
        write_files:   是否落盘 JSON / 纯文本 / 时间轴稿。
        **kwargs:      预留，忽略未知键以便向前兼容。

    Returns:
        dict，含 ok / language / language_probability / duration / segments / outputs。

    Raises:
        InputValidationError: 输入校验、依赖缺失、模型不可用、解码失败。
    """
    cfg = config_mod.load_config(config)
    result = transcribe(
        input_path,
        language=language,
        model_size=model_size,
        model_path=model_path,
        initial_prompt=initial_prompt,
        cfg=cfg,
    )
    if write_files:
        target = output_dir or cfg["output"]["dir"]
        if not Path(target).is_absolute():
            target = str(REPO_ROOT / target)
        result["outputs"] = write_outputs(result, target, cfg)
    else:
        result["outputs"] = {}
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="transcribe.py",
        description="faster-whisper 音视频转录（stdout 输出纯 JSON）",
    )
    p.add_argument("input", nargs="?", help="输入的音频或视频文件路径")
    p.add_argument("--language", default=None, help="语言代码，如 zh/en；不传则按 config 或自动检测")
    p.add_argument("--model-size", default=None, help="模型规格，默认 base")
    p.add_argument("--model-path", default=None, help="本地模型目录；不传则自动查找/下载")
    p.add_argument("--initial-prompt", default=None, help="领域提示词，改善专有名词识别")
    p.add_argument("--config", default=None, help="配置文件路径，默认 config/transcribe_config.yaml")
    p.add_argument("--output-dir", default=None, help="产物目录，默认取 config 的 output.dir")
    p.add_argument("--no-write", action="store_true", help="只打 JSON，不落盘任何文件")
    return p.parse_args(argv)


def _emit(payload: Dict[str, Any], exit_code: int) -> int:
    """把唯一一个 JSON 对象写到 stdout。"""
    print(json.dumps(payload, ensure_ascii=False))
    return exit_code


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)

    if not args.input:
        return _emit({
            "ok": False,
            "error": {
                "code": "missing_argument",
                "message": "缺少输入文件路径",
                "hint": "用法: python3 scripts/transcribe.py <音频或视频文件> [--language zh]",
            },
        }, 2)

    try:
        result = run(
            args.input,
            language=args.language,
            model_size=args.model_size,
            model_path=args.model_path,
            initial_prompt=args.initial_prompt,
            output_dir=args.output_dir,
            config=args.config,
            write_files=not args.no_write,
        )
    except InputValidationError as exc:
        return _emit({
            "ok": False,
            "error": {"code": exc.code, "message": exc.message, "hint": exc.hint},
        }, 1)
    except KeyboardInterrupt:
        return _emit({
            "ok": False,
            "error": {"code": "interrupted", "message": "用户中断", "hint": ""},
        }, 130)

    return _emit(result, 0)


if __name__ == "__main__":
    sys.exit(main())
