"""输出契约校验器（scripts/validate.py）测试。"""
import copy
import json

import pytest

from scripts.validate import DURATION_TOLERANCE_SEC, validate_file, validate_transcript


class TestHappyPath:
    def test_sample_passes(self, sample_result):
        assert validate_transcript(sample_result)["status"] == "PASS"

    def test_no_errors_no_warnings(self, sample_result):
        report = validate_transcript(sample_result)
        assert report["errors"] == []
        assert report["warnings"] == []

    def test_stats_computed(self, sample_result):
        stats = validate_transcript(sample_result)["stats"]
        assert stats["segment_count"] == 2
        assert stats["duration"] == 7.8
        assert stats["language"] == "zh"
        assert stats["total_text_chars"] > 0

    def test_single_segment_ok(self, sample_result):
        result = copy.deepcopy(sample_result)
        result["segments"] = result["segments"][:1]
        result["segment_count"] = 1
        assert validate_transcript(result)["status"] == "PASS"

    def test_english_language_ok(self, sample_result):
        result = copy.deepcopy(sample_result)
        result["language"] = "en"
        assert validate_transcript(result)["status"] == "PASS"

    def test_region_subtag_ok(self, sample_result):
        result = copy.deepcopy(sample_result)
        result["language"] = "zh-CN"
        assert validate_transcript(result)["status"] == "PASS"


class TestTopLevelContract:
    def test_non_dict_root(self):
        assert validate_transcript([])["status"] == "FAIL"

    def test_missing_keys_reported(self, sample_result):
        result = copy.deepcopy(sample_result)
        del result["duration"]
        report = validate_transcript(result)
        assert report["status"] == "FAIL"
        assert any("duration" in e for e in report["errors"])

    def test_ok_false_is_a_failure_not_a_transcript(self, sample_result):
        result = copy.deepcopy(sample_result)
        result["ok"] = False
        report = validate_transcript(result)
        assert report["status"] == "FAIL"
        assert any("失败结果" in e for e in report["errors"])

    def test_segment_count_mismatch(self, sample_result):
        result = copy.deepcopy(sample_result)
        result["segment_count"] = 99
        assert validate_transcript(result)["status"] == "FAIL"

    def test_extra_keys_are_tolerated(self, sample_result):
        result = copy.deepcopy(sample_result)
        result["outputs"] = {"json": "/tmp/x.json"}
        assert validate_transcript(result)["status"] == "PASS"


class TestSourceContract:
    def test_source_must_be_object(self, sample_result):
        result = copy.deepcopy(sample_result)
        result["source"] = "/tmp/demo.mp3"
        assert validate_transcript(result)["status"] == "FAIL"

    def test_kind_must_be_audio_or_video(self, sample_result):
        result = copy.deepcopy(sample_result)
        result["source"]["kind"] = "text"
        assert validate_transcript(result)["status"] == "FAIL"

    def test_video_kind_accepted(self, sample_result):
        result = copy.deepcopy(sample_result)
        result["source"].update({"kind": "video", "ext": ".mp4", "name": "a.mp4"})
        assert validate_transcript(result)["status"] == "PASS"

    def test_zero_size_rejected(self, sample_result):
        result = copy.deepcopy(sample_result)
        result["source"]["size_bytes"] = 0
        assert validate_transcript(result)["status"] == "FAIL"


class TestLanguageAndProbability:
    @pytest.mark.parametrize("bad", ["", "z", "中文", None, 123, "zh_CN"])
    def test_invalid_language(self, sample_result, bad):
        result = copy.deepcopy(sample_result)
        result["language"] = bad
        assert validate_transcript(result)["status"] == "FAIL"

    @pytest.mark.parametrize("bad", [-0.1, 1.5, None, "0.9"])
    def test_probability_out_of_range(self, sample_result, bad):
        result = copy.deepcopy(sample_result)
        result["language_probability"] = bad
        assert validate_transcript(result)["status"] == "FAIL"

    def test_probability_boundaries_ok(self, sample_result):
        for value in (0.0, 1.0):
            result = copy.deepcopy(sample_result)
            result["language_probability"] = value
            assert validate_transcript(result)["status"] == "PASS"

    def test_low_probability_warns_only_in_strict(self, sample_result):
        result = copy.deepcopy(sample_result)
        result["language_probability"] = 0.3
        assert validate_transcript(result)["status"] == "PASS"
        assert validate_transcript(result)["warnings"] == []
        strict = validate_transcript(result, strict=True)
        assert strict["status"] == "PASS"
        assert any("偏低" in w for w in strict["warnings"])


class TestDuration:
    def test_negative_duration(self, sample_result):
        result = copy.deepcopy(sample_result)
        result["duration"] = -1.0
        assert validate_transcript(result)["status"] == "FAIL"

    def test_zero_duration_warns(self, sample_result):
        result = copy.deepcopy(sample_result)
        result["duration"] = 0.0
        result["segments"] = [{"start": 0.0, "end": 0.0, "text": "x"}]
        result["segment_count"] = 1
        report = validate_transcript(result)
        assert any("duration 为 0" in w for w in report["warnings"])

    def test_non_numeric_duration(self, sample_result):
        result = copy.deepcopy(sample_result)
        result["duration"] = "7.8"
        assert validate_transcript(result)["status"] == "FAIL"

    def test_last_segment_within_tolerance(self, sample_result):
        """end 略超 duration 属正常（VAD 与时长口径差异），容差内应放行。"""
        result = copy.deepcopy(sample_result)
        result["segments"][1]["end"] = 7.8 + DURATION_TOLERANCE_SEC - 0.1
        assert validate_transcript(result)["status"] == "PASS"

    def test_last_segment_beyond_tolerance(self, sample_result):
        result = copy.deepcopy(sample_result)
        result["segments"][1]["end"] = 7.8 + DURATION_TOLERANCE_SEC + 5.0
        report = validate_transcript(result)
        assert report["status"] == "FAIL"
        assert any("超过 duration" in e for e in report["errors"])


class TestSegments:
    def test_empty_segments(self, sample_result):
        result = copy.deepcopy(sample_result)
        result["segments"] = []
        result["segment_count"] = 0
        report = validate_transcript(result)
        assert report["status"] == "FAIL"
        assert any("没有识别到任何语音" in e for e in report["errors"])

    def test_segments_not_a_list(self, sample_result):
        result = copy.deepcopy(sample_result)
        result["segments"] = {"0": "x"}
        assert validate_transcript(result)["status"] == "FAIL"

    def test_segment_not_an_object(self, sample_result):
        result = copy.deepcopy(sample_result)
        result["segments"] = ["just a string"]
        result["segment_count"] = 1
        assert validate_transcript(result)["status"] == "FAIL"

    def test_missing_segment_keys(self, sample_result):
        result = copy.deepcopy(sample_result)
        result["segments"] = [{"start": 0.0, "end": 1.0}]
        result["segment_count"] = 1
        assert validate_transcript(result)["status"] == "FAIL"

    def test_end_before_start(self, sample_result):
        result = copy.deepcopy(sample_result)
        result["segments"] = [{"start": 5.0, "end": 1.0, "text": "x"}]
        result["segment_count"] = 1
        result["duration"] = 10.0
        assert validate_transcript(result)["status"] == "FAIL"

    def test_non_monotonic_timestamps(self, sample_result):
        result = copy.deepcopy(sample_result)
        result["segments"] = [
            {"start": 5.0, "end": 6.0, "text": "后"},
            {"start": 1.0, "end": 2.0, "text": "先"},
        ]
        report = validate_transcript(result)
        assert report["status"] == "FAIL"
        assert any("非单调" in e for e in report["errors"])

    def test_empty_text_rejected(self, sample_result):
        result = copy.deepcopy(sample_result)
        result["segments"][0]["text"] = "   "
        assert validate_transcript(result)["status"] == "FAIL"

    def test_non_string_text_rejected(self, sample_result):
        result = copy.deepcopy(sample_result)
        result["segments"][0]["text"] = 123
        assert validate_transcript(result)["status"] == "FAIL"

    def test_non_numeric_timestamp_rejected(self, sample_result):
        result = copy.deepcopy(sample_result)
        result["segments"][0]["start"] = "0.0"
        assert validate_transcript(result)["status"] == "FAIL"

    def test_nan_timestamp_rejected(self, sample_result):
        result = copy.deepcopy(sample_result)
        result["segments"][0]["start"] = float("nan")
        assert validate_transcript(result)["status"] == "FAIL"

    def test_boolean_is_not_a_number(self, sample_result):
        """True == 1 在 Python 里成立，必须显式排除 bool。"""
        result = copy.deepcopy(sample_result)
        result["segments"][0]["start"] = True
        assert validate_transcript(result)["status"] == "FAIL"

    def test_untrimmed_text_warns_only_in_strict(self, sample_result):
        result = copy.deepcopy(sample_result)
        result["segments"][0]["text"] = "  带空白  "
        assert validate_transcript(result)["warnings"] == []
        strict = validate_transcript(result, strict=True)
        assert strict["status"] == "PASS"
        assert any("首尾含空白" in w for w in strict["warnings"])


class TestValidatorNeverCrashes:
    """回归：校验器遇到它本该拒绝的畸形输入时，必须报 FAIL，而不是自己抛异常。

    v0.1 曾有两处：所有 segment 的 end 都非法时 max() 收到空序列，
    text 是 int 时 len() 报 TypeError —— 校验器崩在被校验的数据上。
    """

    @pytest.mark.parametrize("payload", [
        {"segments": ["not a dict"], "duration": 1.0},
        {"segments": [{"start": "a", "end": "b", "text": "x"}], "duration": 1.0},
        {"segments": [{"start": 0, "end": 1, "text": 123}], "duration": 1.0},
        {"segments": [{"start": 0, "end": None, "text": "x"}], "duration": 1.0},
        {"segments": [{}], "duration": 1.0},
        {"segments": [{"start": float("nan"), "end": float("nan"), "text": "x"}]},
        {"segments": [{"start": 0, "end": 1, "text": ["list"]}], "duration": 1.0},
        {"segments": None, "duration": 1.0},
        {"segments": [{"start": 0, "end": 1, "text": "x"}], "duration": None},
        {"segments": [{"start": 0, "end": 1, "text": "x"}], "duration": "7.8"},
        {"ok": True, "segments": [{"start": 0, "end": 1, "text": "x"}], "source": None},
        {"ok": True, "segments": [{"start": 0, "end": 1, "text": "x"}], "source": {"kind": 1}},
        {"ok": True, "segments": [{"start": True, "end": True, "text": "x"}]},
        {},
        {"ok": True},
    ])
    def test_returns_fail_instead_of_raising(self, payload):
        report = validate_transcript(payload)
        assert report["status"] == "FAIL"
        assert isinstance(report["errors"], list) and report["errors"]
        assert isinstance(report["stats"], dict)

    @pytest.mark.parametrize("payload", [
        {"segments": [{"start": "a", "end": "b", "text": "x"}], "duration": 1.0},
        {"segments": [{"start": 0, "end": 1, "text": 123}], "duration": 1.0},
    ])
    def test_strict_mode_also_survives(self, payload):
        assert validate_transcript(payload, strict=True)["status"] == "FAIL"

    def test_stats_count_only_valid_segments(self):
        """valid_segment_count 要求 start/end 均为有限数、end>=start、text 为非空字符串。"""
        payload = {
            "ok": True,
            "segments": [
                {"start": 0.0, "end": 1.0, "text": "abc"},   # 合法
                {"start": "bad", "end": 2.0, "text": "de"},  # start 非数字
                {"start": 3.0, "end": 4.0, "text": 999},     # text 非字符串
            ],
            "duration": 4.0,
        }
        stats = validate_transcript(payload)["stats"]
        assert stats["segment_count"] == 3
        assert stats["valid_segment_count"] == 1
        assert stats["total_text_chars"] == 5  # "abc" + "de"，999 不是字符串不计

    def test_valid_count_equals_segment_count_on_clean_input(self, sample_result):
        stats = validate_transcript(sample_result)["stats"]
        assert stats["valid_segment_count"] == stats["segment_count"] == 2


class TestValidateFile:
    def test_missing_file(self, tmp_path):
        report = validate_file(tmp_path / "ghost.json")
        assert report["status"] == "FAIL"
        assert any("文件不存在" in e for e in report["errors"])

    def test_malformed_json(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        report = validate_file(bad)
        assert report["status"] == "FAIL"
        assert any("无法解析 JSON" in e for e in report["errors"])

    def test_valid_file(self, tmp_path, sample_result):
        good = tmp_path / "demo.transcript.json"
        good.write_text(json.dumps(sample_result, ensure_ascii=False), encoding="utf-8")
        report = validate_file(good)
        assert report["status"] == "PASS"
        assert report["file"] == str(good)

    def test_unicode_roundtrip(self, tmp_path, sample_result):
        path = tmp_path / "中文.transcript.json"
        path.write_text(json.dumps(sample_result, ensure_ascii=False), encoding="utf-8")
        assert validate_file(path)["status"] == "PASS"

    def test_strict_flag_propagates(self, tmp_path, sample_result):
        sample_result["language_probability"] = 0.2
        path = tmp_path / "low.json"
        path.write_text(json.dumps(sample_result, ensure_ascii=False), encoding="utf-8")
        assert validate_file(path)["warnings"] == []
        assert validate_file(path, strict=True)["warnings"]
