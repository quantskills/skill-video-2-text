"""stdio 契约测试 —— v0.1 缺陷回归。

契约：**stdout 永远是且仅是一个 JSON 对象**，所有进度/诊断/下载日志走 stderr。

原实现里 model_utils.ensure_model() 把"发现本地已缓存的模型"这类进度 print 到 stdout，
而 transcribe.py 也往 stdout 打结果 JSON，导致机器消费方 json.loads(stdout) 必然失败
—— 与 SKILL.md 承诺的"转录结果以 JSON 打印到标准输出"自相矛盾。
"""
import json
import sys

import pytest

from scripts import env_check, model_utils, transcribe


@pytest.fixture
def real_mp3(tmp_path):
    path = tmp_path / "demo.mp3"
    path.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 4096)
    return path


def _single_json(raw: str) -> dict:
    """断言 stdout 恰好是一个 JSON 对象（不多不少）。"""
    assert raw.strip(), "stdout 不应为空"
    return json.loads(raw)


class TestCliStdoutIsPureJson:
    def test_missing_file(self, tmp_path, capsys):
        code = transcribe.main([str(tmp_path / "nope.mp3")])
        captured = capsys.readouterr()
        payload = _single_json(captured.out)
        assert code == 1
        assert payload["ok"] is False
        assert payload["error"]["code"] == "input_not_found"
        assert payload["error"]["hint"]

    def test_no_argument(self, capsys):
        code = transcribe.main([])
        payload = _single_json(capsys.readouterr().out)
        assert code == 2
        assert payload["error"]["code"] == "missing_argument"
        assert "transcribe.py" in payload["error"]["hint"]

    def test_unsupported_format(self, tmp_path, capsys):
        bad = tmp_path / "notes.txt"
        bad.write_text("hello", encoding="utf-8")
        code = transcribe.main([str(bad)])
        payload = _single_json(capsys.readouterr().out)
        assert code == 1
        assert payload["error"]["code"] == "unsupported_format"

    def test_empty_file(self, tmp_path, capsys):
        empty = tmp_path / "empty.wav"
        empty.write_bytes(b"")
        code = transcribe.main([str(empty)])
        payload = _single_json(capsys.readouterr().out)
        assert payload["error"]["code"] == "input_empty"
        assert code == 1

    def test_error_json_has_all_three_fields(self, tmp_path, capsys):
        transcribe.main([str(tmp_path / "nope.mp3")])
        error = _single_json(capsys.readouterr().out)["error"]
        assert set(error) == {"code", "message", "hint"}

    def test_stdout_has_no_trailing_progress_lines(self, tmp_path, capsys):
        """即使内部有多条日志，stdout 也只能有一行 JSON。"""
        transcribe.main([str(tmp_path / "nope.mp3")])
        lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
        assert len(lines) == 1


class TestDependencyMissingSurfaced:
    """没装 faster-whisper 时，错误必须以结构化 JSON 返回，而不是裸 traceback。

    把 sys.modules["faster_whisper"] 置为 None 可强制 import 失败，
    因此无论本机是否真的装了 faster-whisper，这组测试都成立。
    """

    def test_code_is_dependency_missing(self, real_mp3, monkeypatch, capsys):
        monkeypatch.setitem(sys.modules, "faster_whisper", None)
        code = transcribe.main([str(real_mp3)])
        payload = _single_json(capsys.readouterr().out)
        assert code == 1
        assert payload["error"]["code"] == "dependency_missing"
        assert "requirements.txt" in payload["error"]["hint"]

    def test_run_raises_typed_error(self, real_mp3, monkeypatch):
        monkeypatch.setitem(sys.modules, "faster_whisper", None)
        with pytest.raises(transcribe.InputValidationError) as exc:
            transcribe.run(str(real_mp3), write_files=False)
        assert exc.value.code == "dependency_missing"


class TestProgressGoesToStderr:
    def test_log_helper_writes_stderr(self, capsys):
        model_utils._log("进度信息")
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "进度信息" in captured.err

    def test_env_check_text_mode_keeps_stdout_clean(self, capsys):
        env_check.main(["--self-check"])
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "环境自检" in captured.err

    def test_env_check_json_mode_emits_valid_json(self, capsys):
        env_check.main(["--self-check", "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert "status" in payload
        assert "checks" in payload

    def test_validate_input_is_silent(self, real_mp3, capsys):
        transcribe.validate_input(real_mp3)
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""


class TestInterrupted:
    def test_keyboard_interrupt_returns_130(self, real_mp3, monkeypatch, capsys):
        """Ctrl-C 也要给出结构化 JSON + 约定退出码 130，而不是抛 KeyboardInterrupt 栈。"""
        def _boom(*args, **kwargs):
            raise KeyboardInterrupt

        monkeypatch.setattr(transcribe, "run", _boom)
        code = transcribe.main([str(real_mp3)])
        payload = _single_json(capsys.readouterr().out)
        assert code == 130
        assert payload["ok"] is False
        assert payload["error"]["code"] == "interrupted"

    def test_interrupt_still_keeps_stdout_pure(self, real_mp3, monkeypatch, capsys):
        def _boom(*args, **kwargs):
            raise KeyboardInterrupt

        monkeypatch.setattr(transcribe, "run", _boom)
        transcribe.main([str(real_mp3)])
        lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
        assert len(lines) == 1


class TestExitCodes:
    def test_success_is_zero(self, real_mp3, monkeypatch, capsys, sample_result):
        """成功路径：退出码 0，且 stdout 就是 run() 的返回值。"""
        monkeypatch.setattr(transcribe, "run", lambda *a, **k: sample_result)
        code = transcribe.main([str(real_mp3)])
        payload = _single_json(capsys.readouterr().out)
        assert code == 0
        assert payload["ok"] is True
        assert payload["segment_count"] == 2

    def test_failure_is_one(self, tmp_path, capsys):
        assert transcribe.main([str(tmp_path / "nope.mp3")]) == 1

    def test_argument_error_is_two(self, capsys):
        """参数缺失(2) 要与运行失败(1) 区分开，方便调用方分支处理。"""
        assert transcribe.main([]) == 2

    def test_env_check_blocked_is_one(self, monkeypatch):
        monkeypatch.setattr(env_check, "MIN_PYTHON", (99, 0))
        assert env_check.main(["--self-check", "--json"]) == 1

    def test_env_check_ready_is_zero(self, monkeypatch, tmp_path):
        """把所有阻塞项都桩掉，自检应返回 0。"""
        monkeypatch.setattr(env_check, "check_python",
                            lambda: {"name": "python", "status": "ok", "found": "3.12", "hint": ""})
        monkeypatch.setattr(env_check, "check_dependencies", lambda: [])
        monkeypatch.setattr(env_check, "check_ffmpeg",
                            lambda: {"name": "ffmpeg", "status": "ok", "found": "/usr/bin/ffmpeg", "hint": ""})
        monkeypatch.setattr(env_check, "check_model",
                            lambda size: {"name": "model", "status": "ok", "found": str(tmp_path), "hint": ""})
        assert env_check.main(["--self-check", "--json"]) == 0

    def test_env_check_warning_only_is_two(self, monkeypatch, tmp_path):
        monkeypatch.setattr(env_check, "check_python",
                            lambda: {"name": "python", "status": "ok", "found": "3.12", "hint": ""})
        monkeypatch.setattr(env_check, "check_dependencies", lambda: [])
        monkeypatch.setattr(env_check, "check_ffmpeg",
                            lambda: {"name": "ffmpeg", "status": "warning", "found": "未安装", "hint": "x"})
        monkeypatch.setattr(env_check, "check_model",
                            lambda size: {"name": "model", "status": "ok", "found": str(tmp_path), "hint": ""})
        assert env_check.main(["--self-check", "--json"]) == 2
