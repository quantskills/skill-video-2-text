#!/usr/bin/env python3
"""环境自检 —— 一次说清"现在能不能转录、不能的话缺什么"。

对应 SKILL.md「整体流程」的第 1~2 步（环境自检 / 解码能力确认），
把这些原本靠 Agent 临场敲命令完成的检查固化成一条可复现的命令：

    python3 scripts/env_check.py --self-check
    python3 scripts/env_check.py --self-check --json      # 机器消费

退出码：0 = 可以转录；1 = 有阻塞项；2 = 只有非阻塞告警（仍可转录）。

ffmpeg 的判定是**软性**的：faster-whisper 底层用 PyAV 解码，PyAV 自带静态
编译的 ffmpeg 解码库，多数情况下系统没装 ffmpeg 也能正常工作。所以缺 ffmpeg
只记 warning，不算阻塞项 —— 只有真的解码失败（transcribe 返回 decode_failed）
才需要回头装它。
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import config as config_mod
from scripts import model_utils

MIN_PYTHON = (3, 10)

# 硬性依赖：缺了就完全没法转录
HARD_DEPENDENCIES = ("faster_whisper", "ctranslate2")
# 软性依赖：缺失只影响自动下载或某些容器的解码
SOFT_DEPENDENCIES = ("av", "modelscope", "huggingface_hub", "yaml")


def _module_version(name: str) -> Optional[str]:
    try:
        module = __import__(name)
    except ImportError:
        return None
    return str(getattr(module, "__version__", "unknown"))


def check_python() -> Dict[str, Any]:
    current = sys.version_info[:2]
    ok = current >= MIN_PYTHON
    return {
        "name": "python",
        "status": "ok" if ok else "blocker",
        "found": platform.python_version(),
        "expected": f">={MIN_PYTHON[0]}.{MIN_PYTHON[1]}",
        "hint": "" if ok else (
            f"当前 Python {platform.python_version()} 低于要求的 "
            f"{MIN_PYTHON[0]}.{MIN_PYTHON[1]}+。macOS: `brew install python@3.12`；"
            "然后用 python3.12 -m venv .venv 建虚拟环境。"
        ),
    }


def check_dependencies() -> List[Dict[str, Any]]:
    results = []
    for name in HARD_DEPENDENCIES:
        version = _module_version(name)
        results.append({
            "name": name,
            "status": "ok" if version else "blocker",
            "found": version or "未安装",
            "hint": "" if version else (
                "运行: pip install -r requirements.txt"
                "（国内网络加 -i https://mirrors.aliyun.com/pypi/simple/）"
            ),
        })
    for name in SOFT_DEPENDENCIES:
        version = _module_version(name)
        if version:
            status, hint = "ok", ""
        elif name in ("modelscope", "huggingface_hub"):
            status = "warning"
            hint = (
                f"{name} 未安装：本地已缓存模型时不影响转录，"
                "但首次需要下载模型时会少一条通道。pip install " + name
            )
        else:
            status = "warning"
            hint = f"{name} 未安装，部分容器格式可能无法解码。pip install {name}"
        results.append({
            "name": name,
            "status": status,
            "found": version or "未安装",
            "hint": hint,
        })
    return results


def check_ffmpeg() -> Dict[str, Any]:
    path = shutil.which("ffmpeg")
    if path:
        return {"name": "ffmpeg", "status": "ok", "found": path, "hint": ""}
    return {
        "name": "ffmpeg",
        "status": "warning",
        "found": "未安装",
        "hint": (
            "系统未检测到 ffmpeg。多数情况下 PyAV 自带的解码库足够用，"
            "可以先直接尝试转录；只有报出 decode_failed 时才需要安装："
            "macOS `brew install ffmpeg` / Windows `winget install ffmpeg` / "
            "Debian·Ubuntu `sudo apt install ffmpeg`。"
        ),
    }


def check_model(model_size: str) -> Dict[str, Any]:
    found = model_utils.find_local_model(model_size)
    if found:
        return {
            "name": f"faster-whisper-{model_size}",
            "status": "ok",
            "found": found,
            "hint": "",
        }

    candidates = model_utils.local_model_candidates(model_size)
    incomplete = {
        str(path): model_utils.missing_model_files(path)
        for path in candidates
        if path.is_dir()
    }
    hint = (
        f"本地未找到完整的 faster-whisper-{model_size} 模型。"
        "首次运行 transcribe.py 会按 ModelScope -> hf-mirror -> HF官方 自动下载（base 约 145MB）。"
        f"\n  已扫描位置: {[str(c) for c in candidates] or '(无)'}"
    )
    if incomplete:
        detail = "; ".join(f"{path} 缺 {files}" for path, files in incomplete.items())
        hint += f"\n  发现不完整目录: {detail}"
    return {
        "name": f"faster-whisper-{model_size}",
        "status": "blocker",
        "found": "未找到",
        "hint": hint,
    }


def check_config(config_path: Optional[str]) -> Dict[str, Any]:
    cfg = config_mod.load_config(config_path)
    return {
        "name": "config",
        "status": "ok",
        "found": config_mod.config_source(config_path),
        "model_size": cfg["model"]["size"],
        "device": cfg["model"]["device"],
        "compute_type": cfg["model"]["compute_type"],
        "vad_filter": cfg["transcribe"]["vad_filter"],
        "cache_root": str(model_utils.MODEL_CACHE_ROOT),
        "hint": "",
    }


def self_check(config_path: Optional[str] = None, model_size: Optional[str] = None) -> Dict[str, Any]:
    """执行全部自检项，返回汇总报告。"""
    cfg = config_mod.load_config(config_path)
    size = model_size or cfg["model"]["size"]

    checks: List[Dict[str, Any]] = [
        {"group": "runtime", "items": [check_python()]},
        {"group": "dependencies", "items": check_dependencies()},
        {"group": "decoder", "items": [check_ffmpeg()]},
        {"group": "model", "items": [check_model(size)]},
        {"group": "config", "items": [check_config(config_path)]},
    ]

    blockers, warnings = [], []
    for group in checks:
        for item in group["items"]:
            if item["status"] == "blocker":
                blockers.append(item)
            elif item["status"] == "warning":
                warnings.append(item)

    if blockers:
        status, exit_code = "blocked", 1
    elif warnings:
        status, exit_code = "degraded", 2
    else:
        status, exit_code = "ready", 0

    return {
        "ok": status != "blocked",
        "status": status,
        "exit_code": exit_code,
        "platform": {"system": platform.system(), "release": platform.release(),
                     "machine": platform.machine()},
        "summary": {
            "blockers": [item["name"] for item in blockers],
            "warnings": [item["name"] for item in warnings],
        },
        "checks": checks,
    }


def render_text(report: Dict[str, Any]) -> str:
    lines = [
        f"video-to-text 环境自检 — {report['status'].upper()}",
        f"平台: {report['platform']['system']} {report['platform']['release']} "
        f"({report['platform']['machine']})",
        "",
    ]
    for group in report["checks"]:
        lines.append(f"[{group['group']}]")
        for item in group["items"]:
            mark = {"ok": "✅", "warning": "⚠️ ", "blocker": "❌"}[item["status"]]
            lines.append(f"  {mark} {item['name']}: {item['found']}")
            for extra in ("expected", "model_size", "device", "compute_type", "vad_filter", "cache_root"):
                if extra in item:
                    lines.append(f"       {extra}: {item[extra]}")
            if item.get("hint"):
                for hint_line in item["hint"].splitlines():
                    lines.append(f"       → {hint_line.strip()}")
        lines.append("")

    if report["summary"]["blockers"]:
        lines.append(f"阻塞项: {', '.join(report['summary']['blockers'])}")
    if report["summary"]["warnings"]:
        lines.append(f"告警项: {', '.join(report['summary']['warnings'])}")
    if report["status"] == "ready":
        lines.append("结论: 环境就绪，可以直接转录。")
    elif report["status"] == "degraded":
        lines.append("结论: 可以转录，但存在告警项，遇到解码失败时回头处理。")
    else:
        lines.append("结论: 存在阻塞项，先按上面的 → 提示处理再转录。")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="env_check.py", description="video-to-text 环境自检")
    p.add_argument("--self-check", action="store_true", help="执行自检（默认行为）")
    p.add_argument("--json", action="store_true", help="以 JSON 输出到 stdout")
    p.add_argument("--config", default=None, help="配置文件路径")
    p.add_argument("--model-size", default=None, help="要检查的模型规格，默认取 config")
    args = p.parse_args(argv)

    report = self_check(args.config, args.model_size)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        # 文本模式走 stderr，保持 stdout 纯净；--json 时 stdout 是唯一输出。
        print(render_text(report), file=sys.stderr)
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
