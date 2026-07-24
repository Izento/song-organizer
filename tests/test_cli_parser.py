# pylint: disable=import-error

import pytest

from cli.parser import parse_args


def test_no_command_defaults_to_gui_dispatch():
    assert parse_args([]).command is None


def test_rename_options_are_scoped_to_rename_command():
    args = parse_args(
        [
            "rename",
            "--folder",
            "D:\\Music",
            "--strategy",
            "regular",
            "--fingerprint",
            "--apply",
        ]
    )

    assert args.command == "rename"
    assert args.folder == "D:\\Music"
    assert args.strategy == "regular"
    assert args.fingerprint is True
    assert args.apply is True


@pytest.mark.parametrize(
    "legacy_option",
    [
        "--audit",
        "--sync-tags",
        "--dedup-regular",
        "--dedup-ocremix",
        "--undo",
        "--acoustid-key",
    ],
)
def test_legacy_action_flags_are_rejected(legacy_option):
    with pytest.raises(SystemExit):
        parse_args([legacy_option])
