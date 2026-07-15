import os
import subprocess
from types import SimpleNamespace

from renamer import fingerprint


def test_fingerprint_subprocess_has_no_windows_console(monkeypatch):
    captured = {}
    monkeypatch.setattr(fingerprint, "resolve_fpcalc", lambda: "fpcalc.exe")

    def fake_run(command, **options):
        captured["command"] = command
        captured["options"] = options
        return SimpleNamespace(
            returncode=0,
            stdout="DURATION=12.7\nFINGERPRINT=abc123\n",
            stderr="",
        )

    monkeypatch.setattr(fingerprint.subprocess, "run", fake_run)

    result, error = fingerprint.fingerprint_file("song.mp3")

    assert result == "abc123"
    assert error is None
    computed_fingerprint, duration, details_error = (
        fingerprint.fingerprint_file_details("song.mp3")
    )
    assert computed_fingerprint == "abc123"
    assert duration == 12.7
    assert details_error is None
    if os.name == "nt":
        assert (
            captured["options"]["creationflags"]
            == subprocess.CREATE_NO_WINDOW
        )
