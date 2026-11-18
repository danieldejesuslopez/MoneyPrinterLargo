"""CLI upload outcomes are reported without running Firefox in these tests."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from clips import upload as clips_upload  # noqa: E402


@pytest.fixture
def cli():
    spec = importlib.util.spec_from_file_location("clips_upload_cli_under_test", ROOT / "scripts" / "clips.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upload_cli_passes_the_reviewed_metadata_and_prints_confirmed_result(cli, monkeypatch, capsys):
    calls = []
    result = {"id": "clip-1", "status": "published", "youtube_upload": {
        "status": "uploaded", "url": "https://www.youtube.com/shorts/abc123",
    }}

    def upload(clip_id, channel_id, title=None, description=None, on_status=None):
        calls.append((clip_id, channel_id, title, description))
        print("Firefox conectado")
        return result

    monkeypatch.setattr(clips_upload, "upload_clip", upload)
    code = cli.main([
        "--json", "upload", "clip-1", "--channel-id", "channel-1",
        "--title", 'Mi "título"', "--description", "Línea uno\nLínea dos #Shorts",
    ])
    assert code == 0
    assert calls == [("clip-1", "channel-1", 'Mi "título"', "Línea uno\nLínea dos #Shorts")]
    captured = capsys.readouterr()
    assert json.loads(captured.out) == result
    assert "Firefox conectado" in captured.err


@pytest.mark.parametrize("upload_status, expected_code", [("uploaded", 0), ("pending_confirmation", 0), ("failed", 1)])
def test_upload_cli_distinguishes_confirmed_pending_and_failed_results(cli, monkeypatch, capsys, upload_status, expected_code):
    monkeypatch.setattr(clips_upload, "upload_clip", lambda *args, **kwargs: {
        "id": "clip-1", "title": "Título", "status": "draft",
        "youtube_upload": {"status": upload_status, "url": "", "error": "falló"},
    })
    assert cli.main(["--json", "upload", "clip-1", "--channel-id", "channel-1"]) == expected_code
    assert json.loads(capsys.readouterr().out)["youtube_upload"]["status"] == upload_status


def test_upload_cli_reports_a_validation_error_without_uploading(cli, monkeypatch, capsys):
    def invalid(*args, **kwargs):
        raise clips_upload.UploadError("Perfil de Firefox no encontrado")

    monkeypatch.setattr(clips_upload, "upload_clip", invalid)
    assert cli.main(["--json", "upload", "clip-1", "--channel-id", "channel-1"]) == 1
    assert "Firefox" in json.loads(capsys.readouterr().out)["error"]
