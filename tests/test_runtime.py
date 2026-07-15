# pylint: disable=import-error

from renamer import runtime


def test_acoustid_key_is_optional(monkeypatch, tmp_path):
    monkeypatch.delenv("ACOUSTID_API_KEY", raising=False)
    monkeypatch.setattr(runtime, "app_data_dir", lambda: tmp_path / "app")
    monkeypatch.setattr(runtime, "resource_path", lambda name: tmp_path / name)
    monkeypatch.setattr(runtime.sys, "executable", str(tmp_path / "app.exe"))

    assert runtime.resolve_acoustid_key() is None


def test_acoustid_key_can_come_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("ACOUSTID_API_KEY", "test-key")
    monkeypatch.setattr(runtime, "app_data_dir", lambda: tmp_path / "app")

    assert runtime.resolve_acoustid_key() == "test-key"
