# pylint: disable=import-error,protected-access

from datetime import datetime

from gui.app import _format_local_timestamp


def test_history_timestamps_are_converted_to_local_time():
    timestamp = "2026-07-13T07:05:10.468747+00:00"

    expected = datetime.fromisoformat(timestamp).astimezone().strftime(
        "%Y-%m-%d %I:%M:%S %p %Z"
    )

    assert _format_local_timestamp(timestamp) == expected


def test_history_timestamp_falls_back_when_invalid():
    assert _format_local_timestamp("not-a-timestamp") == "not-a-timestamp"
