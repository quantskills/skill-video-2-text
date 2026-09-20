"""输入校验（validate_input / classify_extension）测试。"""
import os
from pathlib import Path

import pytest

from scripts.transcribe import InputValidationError, classify_extension, validate_input


@pytest.fixture
def mp3(tmp_path):
    path = tmp_path / "demo.mp3"
    path.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 2048)
    return path


class TestClassifyExtension:
    def test_audio_extensions(self, cfg):
        for ext in (".mp3", ".wav", ".m4a", ".flac", ".ogg"):
            assert classify_extension(ext, cfg) == "audio"

    def test_video_extensions(self, cfg):
        for ext in (".mp4", ".mov", ".mkv", ".avi", ".webm"):
            assert classify_extension(ext, cfg) == "video"

    def test_case_insensitive(self, cfg):
        assert classify_extension(".MP3", cfg) == "audio"
        assert classify_extension(".MP4", cfg) == "video"

    def test_unsupported(self, cfg):
        for ext in (".txt", ".pdf", ".parquet", ".py", ""):
            assert classify_extension(ext, cfg) is None


class TestValidateInputPass:
    def test_returns_normalized_dict(self, mp3, cfg):
        info = validate_input(mp3, cfg)
        assert info["kind"] == "audio"
        assert info["ext"] == ".mp3"
        assert info["name"] == "demo.mp3"
        assert info["size_bytes"] > 0

    def test_path_is_absolute(self, mp3, cfg):
        assert validate_input(mp3, cfg)["path"].startswith("/")

    def test_accepts_string_path(self, mp3, cfg):
        assert validate_input(str(mp3), cfg)["name"] == "demo.mp3"

    def test_accepts_video(self, tmp_path, cfg):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"\x00" * 4096)
        assert validate_input(video, cfg)["kind"] == "video"

    def test_cfg_defaults_when_omitted(self, mp3):
        assert validate_input(mp3)["kind"] == "audio"


class TestValidateInputRejects:
    def test_not_found(self, tmp_path, cfg):
        with pytest.raises(InputValidationError) as exc:
            validate_input(tmp_path / "nope.mp3", cfg)
        assert exc.value.code == "input_not_found"

    def test_directory_is_not_a_file(self, tmp_path, cfg):
        with pytest.raises(InputValidationError) as exc:
            validate_input(tmp_path, cfg)
        assert exc.value.code == "input_not_a_file"

    def test_unsupported_format_lists_alternatives(self, tmp_path, cfg):
        bad = tmp_path / "notes.txt"
        bad.write_text("hello", encoding="utf-8")
        with pytest.raises(InputValidationError) as exc:
            validate_input(bad, cfg)
        assert exc.value.code == "unsupported_format"
        assert ".mp3" in exc.value.hint

    def test_no_extension(self, tmp_path, cfg):
        bad = tmp_path / "README"
        bad.write_text("x", encoding="utf-8")
        with pytest.raises(InputValidationError) as exc:
            validate_input(bad, cfg)
        assert exc.value.code == "unsupported_format"

    def test_empty_file(self, tmp_path, cfg):
        empty = tmp_path / "empty.wav"
        empty.write_bytes(b"")
        with pytest.raises(InputValidationError) as exc:
            validate_input(empty, cfg)
        assert exc.value.code == "input_empty"

    def test_too_large(self, mp3, cfg):
        tiny_limit = dict(cfg)
        tiny_limit["input"] = dict(cfg["input"], max_size_mb=0)
        with pytest.raises(InputValidationError) as exc:
            validate_input(mp3, tiny_limit)
        assert exc.value.code == "input_too_large"

    @pytest.mark.skipif(
        hasattr(os, "geteuid") and os.geteuid() == 0,
        reason="root 无视文件权限位，无法构造不可读文件",
    )
    def test_unreadable_file(self, tmp_path, cfg):
        """权限位挡住读取时要给出 input_unreadable，而不是让 OSError 冒出去。"""
        locked = tmp_path / "locked.mp3"
        locked.write_bytes(b"\x00" * 1024)
        os.chmod(locked, 0o000)
        try:
            with pytest.raises(InputValidationError) as exc:
                validate_input(locked, cfg)
            assert exc.value.code == "input_unreadable"
            assert "权限" in exc.value.hint
        finally:
            os.chmod(locked, 0o644)

    def test_unreadable_hint_is_actionable(self, tmp_path, cfg, monkeypatch):
        """不依赖真实权限位也能验证这条分支：直接让 open 抛 OSError。"""
        target = tmp_path / "busy.mp3"
        target.write_bytes(b"\x00" * 1024)

        real_open = Path.open

        def _deny(self, *args, **kwargs):
            if self == target:
                raise OSError(16, "Resource busy")
            return real_open(self, *args, **kwargs)

        monkeypatch.setattr(Path, "open", _deny)
        with pytest.raises(InputValidationError) as exc:
            validate_input(target, cfg)
        assert exc.value.code == "input_unreadable"
        assert "Resource busy" in exc.value.message

    def test_error_carries_hint(self, tmp_path, cfg):
        with pytest.raises(InputValidationError) as exc:
            validate_input(tmp_path / "nope.mp3", cfg)
        assert exc.value.hint
        assert str(exc.value) == exc.value.message


class TestValidateInputIsPure:
    def test_does_not_create_files(self, mp3, cfg, tmp_path):
        before = sorted(p.name for p in tmp_path.iterdir())
        validate_input(mp3, cfg)
        assert sorted(p.name for p in tmp_path.iterdir()) == before

    def test_does_not_print(self, mp3, cfg, capsys):
        validate_input(mp3, cfg)
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""
