#!/bin/bash
# 重新生成中文端到端测试样本（约 21 秒，16kHz 单声道 WAV）。
#
# 为什么用脚本而不是把音频提交进仓库：
#   - 音频是二进制大文件（约 660KB），不该进 git
#   - macOS 自带 `say` + `afconvert`，任何人都能一键复现同一个样本
#   - 基准原文与音频由同一次调用产生，永远不会对不上
#
# 用法：
#   ./tests/fixtures/make_sample_zh.sh
#
# 依赖：macOS（say / afconvert 均为系统自带）。Linux/Windows 请自备等价的
#       16kHz 单声道 WAV，并把原文写进 sample_zh.txt。

set -euo pipefail

cd "$(dirname "$0")"

VOICE="Tingting"          # zh_CN 标准普通话女声，macOS 自带
RATE_HZ=16000             # Whisper 的原生采样率，避免额外重采样
WAV="sample_zh.wav"
TXT="sample_zh.txt"
TMP_AIFF="$(mktemp -t sample_zh).aiff"

# 基准原文。刻意混入三类"转录难点"，让端到端测试真的能暴露问题：
#   1. 中文数字与量词   —— 第三季度、本周三、千分之一、千分之一点五
#   2. 金融专有名词     —— 沪深三百、成分股、权重、回测引擎、滑点
#   3. 易混同音/近音词  —— 对齐/对起、偏差/篇差、修复/休复、滑点/华点
# 92 字 @ Tingting 默认语速 ≈ 21 秒。
TEXT="各位早上好，今天对齐一下第三季度的进度。数据接口的字段已经核对完成，其中沪深三百的成分股权重存在两处偏差，工程组会在本周三之前修复。回测引擎的滑点参数需要从千分之一调整到千分之一点五。"

echo "语音: $VOICE | 采样率: ${RATE_HZ}Hz | 字数: ${#TEXT}"

say -v "$VOICE" -o "$TMP_AIFF" "$TEXT"
afconvert -f WAVE -d "LEI16@${RATE_HZ}" -c 1 "$TMP_AIFF" "$WAV"
rm -f "$TMP_AIFF"

printf '%s\n' "$TEXT" > "$TXT"

# 打印实测时长，便于确认样本仍在预期区间
DURATION=$(afinfo "$WAV" 2>/dev/null | grep -o 'estimated duration: [0-9.]*' | awk '{print $3}')
SIZE=$(stat -f%z "$WAV" 2>/dev/null || stat -c%s "$WAV")
echo "✅ 生成 $WAV (${SIZE} 字节, ${DURATION:-?} 秒)"
echo "✅ 生成 $TXT (基准原文)"
echo
echo "下一步（需先 pip install -r requirements.txt）："
echo "  python3 ../../scripts/transcribe.py $PWD/$WAV --language zh"
echo "  python3 ../../scripts/validate.py ../../output/sample_zh.transcript.json --strict"
