"""Whole-source CLI options and resumable outcomes, without video encoding."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


@pytest.fixture
def cli():
    spec = importlib.util.spec_from_file_location("clips_full_cli_under_test", ROOT / "scripts" / "clips.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def result(*, complete=False, failures=None):
    return {
        "source": {"id": "source-1"}, "clips": [], "failures": failures or [],
        "words_from": "none", "resumed": True,
        "sequence": {
            "total_parts": 12, "completed_parts": 12 if complete else 3,
            "remaining_parts": 0 if complete else 9,
            "next_start": 720 if complete else 180, "complete": complete,
        },
    }


def test_default_full_command_resumes_all_remaining_parts(cli, monkeypatch, capsys):
    calls = []

    def full(target, **kwargs):
        calls.append((target, kwargs))
        return result()

    monkeypatch.setattr(cli.clips, "full", full, raising=False)
    assert cli.main(["full", "video.mp4"]) == 0
    assert calls[0][0] == "video.mp4"
    assert calls[0][1]["clip_seconds"] == 60
    assert calls[0][1]["batch_size"] == 0 and calls[0][1]["resume"] is True
    assert "count" not in calls[0][1]
    assert "9 part(s) left" in capsys.readouterr().out


def test_full_json_preserves_exact_parameters_and_keeps_progress_logs_on_stderr(cli, monkeypatch, capsys):
    calls = []
    output = result(complete=True)

    def full(target, **kwargs):
        calls.append((target, kwargs))
        print("Rendering part 12/12")
        return output

    monkeypatch.setattr(cli.clips, "full", full, raising=False)
    assert cli.main([
        "--json", "full", "https://example.com/video", "--clip-seconds", "90",
        "--batch-size", "25", "--no-resume", "--rights", "mi canal",
        "--language", "es", "--no-subtitles", "--layout", "crop", "--whisper",
        "--refresh", "--max-minutes", "240", "--title-overlay", "never",
    ]) == 0
    target, args = calls[0]
    assert target == "https://example.com/video"
    assert args["clip_seconds"] == 90 and args["batch_size"] == 25
    assert args["resume"] is False and args["subtitles"] is False
    assert args["rights_note"] == "mi canal" and args["language"] == "es"
    assert args["captions"] == "whisper" and args["layout"] == "crop"
    assert args["refresh"] is True and args["max_minutes"] == 240
    assert args["title_overlay"] == "never" and args["on_status"] is None
    captured = capsys.readouterr()
    assert json.loads(captured.out) == output
    assert "Rendering part 12/12" in captured.err


def test_full_command_reports_partial_failure_without_losing_completed_parts(cli, monkeypatch, capsys):
    output = result(failures=["part 4: encoder failed"])
    output["clips"] = [{"title": "Parte 3", "start": 120, "end": 180, "path": "part3.mp4"}]
    monkeypatch.setattr(cli.clips, "full", lambda *args, **kwargs: output, raising=False)
    assert cli.main(["full", "video.mp4", "--batch-size", "3"]) == 1
    text = capsys.readouterr().out
    assert "1 new clip(s)" in text and "part3.mp4" in text
    assert "part 4: encoder failed" in text and "Run full again to continue" in text


def test_complete_resume_is_a_successful_no_op(cli, monkeypatch, capsys):
    monkeypatch.setattr(cli.clips, "full", lambda *args, **kwargs: result(complete=True), raising=False)
    assert cli.main(["full", "video.mp4"]) == 0
    assert "The whole video is covered" in capsys.readouterr().out


def test_full_error_has_a_single_machine_readable_result(cli, monkeypatch, capsys):
    def full(*args, **kwargs):
        print("Resolving video")
        raise cli.clips.ClipError("Source is already being clipped")

    monkeypatch.setattr(cli.clips, "full", full, raising=False)
    assert cli.main(["--json", "full", "video.mp4"]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"error": "Source is already being clipped"}
    assert "Resolving video" in captured.err
