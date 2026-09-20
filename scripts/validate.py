#!/usr/bin/env python3
"""校验转录产物是否符合输出契约。

对应 SKILL.md「输出契约」一节的机器可执行版本 —— 大模型后处理之前，
先用它确认第 4 步的原始 JSON 是完整可信的，避免拿残缺数据去写摘要。

    python3 scripts/validate.py output/xxx.transcript.json
    python3 scripts/validate.py output/xxx.transcript.json --strict

退出码：0 = PASS，1 = FAIL。stdout 输出 JSON 报告。
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

REQUIRED_TOP_LEVEL_KEYS = [
    "ok", "source", "model_size", "language", "language_probability",
    "duration", "segment_count", "segments",
]
REQUIRED_SEGMENT_KEYS = ["start", "end", "text"]
REQUIRED_SOURCE_KEYS = ["path", "name", "ext", "kind", "size_bytes"]

# BCP-47 主子标签，宽松匹配 zh / en / zh-CN 等
LANGUAGE_PATTERN = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$")

# 最后一个 segment 的 end 允许略微超出 duration（VAD 与时长统计口径有微小差异）
DURATION_TOLERANCE_SEC = 1.0


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_transcript(payload: Dict[str, Any], strict: bool = False) -> Dict[str, Any]:
    """校验一个转录结果 dict，返回 {status, errors, warnings, stats}。"""
    errors: List[str] = []
    warnings: List[str] = []

    if not isinstance(payload, dict):
        return {"status": "FAIL", "errors": ["根节点必须是 JSON 对象"], "warnings": [], "stats": {}}

    missing = [k for k in REQUIRED_TOP_LEVEL_KEYS if k not in payload]
    if missing:
        errors.append(f"缺少必需字段: {missing}")

    if payload.get("ok") is not True:
        errors.append(f"ok 不为 true（实际: {payload.get('ok')!r}）—— 这是一份失败结果，不是转录产物")

    # --- source ---
    source = payload.get("source")
    if not isinstance(source, dict):
        errors.append("source 必须是对象")
    else:
        src_missing = [k for k in REQUIRED_SOURCE_KEYS if k not in source]
        if src_missing:
            errors.append(f"source 缺少字段: {src_missing}")
        if source.get("kind") not in ("audio", "video"):
            errors.append(f"source.kind 必须是 audio 或 video（实际: {source.get('kind')!r}）")
        if not _is_finite_number(source.get("size_bytes")) or source.get("size_bytes", 0) <= 0:
            errors.append("source.size_bytes 必须是正数")

    # --- language / probability / duration ---
    language = payload.get("language")
    if not isinstance(language, str) or not LANGUAGE_PATTERN.match(language):
        errors.append(f"language 不是合法语言代码: {language!r}")

    prob = payload.get("language_probability")
    if not _is_finite_number(prob):
        errors.append(f"language_probability 必须是有限数字: {prob!r}")
    elif not 0.0 <= float(prob) <= 1.0:
        errors.append(f"language_probability 超出 [0,1]: {prob}")
    elif strict and float(prob) < 0.5:
        warnings.append(f"language_probability 偏低（{prob}），自动检测结果可能不可靠，建议显式传 --language")

    duration = payload.get("duration")
    if not _is_finite_number(duration):
        errors.append(f"duration 必须是有限数字: {duration!r}")
    elif duration < 0:
        errors.append(f"duration 不能为负: {duration}")
    elif duration == 0:
        warnings.append("duration 为 0，可能是空音频或解码只拿到了头部")

    # --- segments ---
    segments = payload.get("segments")
    if not isinstance(segments, list):
        errors.append(f"segments 必须是数组: {type(segments).__name__}")
        segments = []

    declared = payload.get("segment_count")
    if declared is not None and declared != len(segments):
        errors.append(f"segment_count({declared}) 与 segments 实际长度({len(segments)}) 不一致")

    if not segments:
        errors.append("segments 为空 —— 没有识别到任何语音")

    previous_start = 0.0
    for index, seg in enumerate(segments):
        path = f"segments[{index}]"
        if not isinstance(seg, dict):
            errors.append(f"{path} 必须是对象")
            continue
        seg_missing = [k for k in REQUIRED_SEGMENT_KEYS if k not in seg]
        if seg_missing:
            errors.append(f"{path} 缺少字段: {seg_missing}")
            continue

        start, end, text = seg["start"], seg["end"], seg["text"]
        if not _is_finite_number(start) or not _is_finite_number(end):
            errors.append(f"{path} 的 start/end 必须是有限数字")
            continue
        if float(end) < float(start):
            errors.append(f"{path} end({end}) 小于 start({start})")
        if float(start) < previous_start - 1e-6:
            errors.append(f"{path} start({start}) 早于上一段的 start({previous_start})，时间戳非单调")
        previous_start = float(start)

        if not isinstance(text, str):
            errors.append(f"{path}.text 必须是字符串")
        elif not text.strip():
            errors.append(f"{path}.text 为空")
        elif strict and text != text.strip():
            warnings.append(f"{path}.text 首尾含空白，未按契约 strip")

    # 只统计确实合法的 segment —— 校验器遇到畸形输入必须报 FAIL，而不是自己崩掉。
    ends = [
        float(seg["end"]) for seg in segments
        if isinstance(seg, dict) and _is_finite_number(seg.get("end"))
    ]
    if ends and _is_finite_number(duration):
        last_end = max(ends)
        if last_end > float(duration) + DURATION_TOLERANCE_SEC:
            errors.append(
                f"最后一个 segment 的 end({last_end}) 超过 duration({duration}) "
                f"达 {last_end - float(duration):.2f}s（容差 {DURATION_TOLERANCE_SEC}s）"
            )

    valid_segments = sum(
        1 for seg in segments
        if isinstance(seg, dict)
        and _is_finite_number(seg.get("start"))
        and _is_finite_number(seg.get("end"))
        and float(seg["end"]) >= float(seg["start"])
        and isinstance(seg.get("text"), str)
        and seg["text"].strip()
    )

    stats = {
        "segment_count": len(segments),
        "valid_segment_count": valid_segments,
        "total_text_chars": sum(
            len(seg["text"]) for seg in segments
            if isinstance(seg, dict) and isinstance(seg.get("text"), str)
        ),
        "duration": duration,
        "language": language,
        "language_probability": prob,
    }
    return {
        "status": "FAIL" if errors else "PASS",
        "errors": errors,
        "warnings": warnings,
        "stats": stats,
    }


def validate_file(path: Path, strict: bool = False) -> Dict[str, Any]:
    if not path.exists():
        return {"status": "FAIL", "errors": [f"文件不存在: {path}"], "warnings": [], "stats": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"status": "FAIL", "errors": [f"无法解析 JSON: {exc}"], "warnings": [], "stats": {}}
    result = validate_transcript(payload, strict=strict)
    result["file"] = str(path)
    return result


def main(argv: List[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="validate.py", description="校验转录产物输出契约")
    p.add_argument("path", help="*.transcript.json 路径")
    p.add_argument("--strict", action="store_true", help="启用更严格的告警项")
    args = p.parse_args(argv)

    report = validate_file(Path(args.path), strict=args.strict)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
