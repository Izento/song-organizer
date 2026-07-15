"""Optional Chromaprint fingerprint adapter."""

from __future__ import annotations

import os
import subprocess

from .runtime import resolve_fpcalc


def fingerprint_file(path: str, timeout: int = 60) -> tuple[str | None, str | None]:
    fingerprint, _, error = fingerprint_file_details(path, timeout=timeout)
    return fingerprint, error


def fingerprint_file_details(
    path: str,
    timeout: int = 60,
) -> tuple[str | None, float | None, str | None]:
    command = resolve_fpcalc()
    if not command:
        return None, None, "fpcalc unavailable"
    try:
        process_options = {}
        if os.name == "nt":
            process_options["creationflags"] = subprocess.CREATE_NO_WINDOW
        process = subprocess.run(
            [command, path],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            **process_options,
        )
    except subprocess.TimeoutExpired:
        return None, None, f"fpcalc timed out after {timeout} seconds"
    except OSError as exc:
        return None, None, str(exc)
    if process.returncode:
        return (
            None,
            None,
            process.stderr.strip() or f"fpcalc exited with {process.returncode}",
        )
    values = {}
    for line in process.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    fingerprint = values.get("FINGERPRINT")
    if not fingerprint:
        return None, None, "No fingerprint returned"
    try:
        duration = float(values["DURATION"])
    except (KeyError, ValueError):
        duration = None
    return fingerprint, duration, None


__all__ = ["fingerprint_file", "fingerprint_file_details"]
