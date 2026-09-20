"""输出层测试 —— 时间戳格式、纯文本/时间轴渲染、落盘。"""
import json
import os
from pathlib import Path

import pytest

from scripts import transcribe


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


class TestFormatTimestamp:
    @pytest.mark.parametrize("seconds,expected", [
        (0.0, "00:00"),
        (7.8, "00:08"),
        (59.4, "00:59"),
        (60.0, "01:00"),
        (225.0, "03:45"),
        (3599.0, "59:59"),
        (3600.0, "60:00"),
        (612.3, "10:12"),
    ])
    def test_mm_ss_format(self, seconds, expected):
        assert transcribe._format_timestamp(seconds) == expected

    def test_rounds_to_nearest_second(self):
        assert transcribe._format_timestamp(3.4) == "00:03"
        assert transcribe._format_timestamp(3.6) == "00:04"

    def test_zero_padded(self):
        assert transcribe._format_timestamp(5.0) == "00:05"


class TestRenderPlainText:
    def test_joins_segments_with_newline(self, sample_result):
        text = transcribe.render_plain_text(sample_result)
        assert text == "呃 这个 项目 的话 我们 上周\n已经 把 那个 接口 对齐 了"

    def test_no_timestamps_in_plain_text(self, sample_result):
        text = transcribe.render_plain_text(sample_result)
        assert "[" not in text and "]" not in text

    def test_empty_segments(self, sample_result):
        sample_result["segments"] = []
        assert transcribe.render_plain_text(sample_result) == ""


class TestRenderTimeline:
    def test_prefixes_each_segment(self, sample_result):
        lines = transcribe.render_timeline(sample_result).splitlines()
        assert lines[0] == "[00:00 - 00:03] 呃 这个 项目 的话 我们 上周"
        assert lines[1] == "[00:03 - 00:08] 已经 把 那个 接口 对齐 了"

    def test_line_count_matches_segments(self, sample_result):
        assert len(transcribe.render_timeline(sample_result).splitlines()) == 2

    def test_empty_segments(self, sample_result):
        sample_result["segments"] = []
        assert transcribe.render_timeline(sample_result) == ""


class TestWriteOutputs:
    def test_writes_three_files(self, sample_result, tmp_path, cfg):
        written = transcribe.write_outputs(sample_result, tmp_path, cfg)
        assert set(written) == {"json", "plain_text", "timeline"}
        for path in written.values():
            assert os.path.isfile(path), f"未真正落盘: {path}"

    def test_json_is_parseable_and_lossless(self, sample_result, tmp_path, cfg):
        written = transcribe.write_outputs(sample_result, tmp_path, cfg)
        reloaded = json.loads(_read(written["json"]))
        assert reloaded["segments"] == sample_result["segments"]
        assert reloaded["language"] == "zh"

    def test_filenames_derive_from_source(self, sample_result, tmp_path, cfg):
        written = transcribe.write_outputs(sample_result, tmp_path, cfg)
        assert written["json"].endswith("demo.transcript.json")
        assert written["plain_text"].endswith("demo.transcript.txt")
        assert written["timeline"].endswith("demo.timeline.txt")

    def test_creates_missing_directory(self, sample_result, tmp_path, cfg):
        nested = tmp_path / "a" / "b" / "c"
        written = transcribe.write_outputs(sample_result, nested, cfg)
        assert nested.is_dir()
        assert json.loads(_read(written["json"]))["ok"] is True

    def test_unicode_preserved_not_escaped(self, sample_result, tmp_path, cfg):
        written = transcribe.write_outputs(sample_result, tmp_path, cfg)
        raw = _read(written["json"])
        assert "呃 这个 项目" in raw
        assert "\\u" not in raw

    def test_write_json_disabled(self, sample_result, tmp_path, cfg):
        cfg["output"]["write_json"] = False
        written = transcribe.write_outputs(sample_result, tmp_path, cfg)
        assert "json" not in written
        assert "plain_text" in written

    def test_write_plain_text_disabled(self, sample_result, tmp_path, cfg):
        cfg["output"]["write_plain_text"] = False
        written = transcribe.write_outputs(sample_result, tmp_path, cfg)
        assert "plain_text" not in written
        assert "json" in written

    def test_timeline_always_written(self, sample_result, tmp_path, cfg):
        """时间轴稿是回原音频对照的唯一凭据，不受开关影响。"""
        cfg["output"]["write_json"] = False
        cfg["output"]["write_plain_text"] = False
        assert "timeline" in transcribe.write_outputs(sample_result, tmp_path, cfg)

    def test_json_indent_respected(self, sample_result, tmp_path, cfg):
        cfg["output"]["json_indent"] = 2
        written = transcribe.write_outputs(sample_result, tmp_path, cfg)
        raw = _read(written["json"])
        assert '\n  "ok"' in raw

    def test_overwrites_existing_file(self, sample_result, tmp_path, cfg):
        transcribe.write_outputs(sample_result, tmp_path, cfg)
        sample_result["segments"][0]["text"] = "改过了"
        written = transcribe.write_outputs(sample_result, tmp_path, cfg)
        assert "改过了" in _read(written["plain_text"])
