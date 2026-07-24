# pylint: disable=import-error,protected-access

from argparse import Namespace

from cli import app
from cli.commands import undo


class _Output:
    def __init__(self):
        self.messages = []

    def print(self, message=""):
        self.messages.append(message)

    def confirm(self, _prompt):
        return True


def test_no_arguments_launch_gui(monkeypatch):
    monkeypatch.setattr(app, "_configure_utf8_console", lambda: None)
    monkeypatch.setattr(app, "_load_local_environment", lambda: None)
    monkeypatch.setattr(app, "ensure_app_dirs", lambda: None)
    monkeypatch.setattr(app, "_run_gui", lambda: 7)

    assert app.main([], output=_Output()) == 7


def test_dispatch_returns_command_status(monkeypatch):
    monkeypatch.setattr(undo, "run", lambda _args, _output: 3)

    assert app._dispatch("undo", Namespace(), _Output()) == 3
