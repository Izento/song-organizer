"""Shared media inspection helpers.

All callers use the same canonical tag names regardless of the underlying
container.  Inspection is intentionally separate from writing so analysis can
report unsupported and malformed files instead of treating them as untagged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class MediaRead:
    path: str
    status: str
    container: str = ""
    tags: dict[str, str] = field(default_factory=dict)
    duration: float | None = None
    bitrate: int | None = None
    error: str = ""

    @property
    def usable(self) -> bool:
        return self.status in {"ok", "empty"}


def _text_value(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "text"):
        return _text_value(value.text)
    if hasattr(value, "value") and not isinstance(value, (str, bytes)):
        return _text_value(value.value)
    if isinstance(value, (list, tuple)):
        return ", ".join(
            item for item in (_text_value(entry) for entry in value) if item
        ).strip()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip()
    return str(value).strip()


def _first_tag(tags: Any, *keys: str) -> str:
    if not tags:
        return ""
    for key in keys:
        try:
            value = tags.get(key)
        except (AttributeError, TypeError):
            continue
        text = _text_value(value)
        if text:
            return text
    return ""


def _canonical_tags(audio: Any) -> dict[str, str]:
    tags = getattr(audio, "tags", None)
    if not tags:
        return {}

    # ID3 and Vorbis/Opus comments.
    result = {
        "artist": _first_tag(tags, "TPE1", "artist"),
        "title": _first_tag(tags, "TIT2", "title"),
        "album": _first_tag(tags, "TALB", "album"),
        "album_artist": _first_tag(tags, "TPE2", "albumartist", "album_artist"),
        "grouping": _first_tag(tags, "TIT1", "grouping", "contentgroup"),
        "subtitle": _first_tag(tags, "TIT3", "subtitle"),
    }

    # MP4 atoms.
    result["artist"] = result["artist"] or _first_tag(tags, "\xa9ART")
    result["title"] = result["title"] or _first_tag(tags, "\xa9nam")
    result["album"] = result["album"] or _first_tag(tags, "\xa9alb")
    result["album_artist"] = result["album_artist"] or _first_tag(tags, "aART")
    result["grouping"] = result["grouping"] or _first_tag(tags, "\xa9grp")
    result["subtitle"] = result["subtitle"] or _first_tag(
        tags, "----:com.apple.iTunes:SUBTITLE"
    )

    # ASF/WMA attributes.
    result["artist"] = result["artist"] or _first_tag(tags, "Author")
    result["title"] = result["title"] or _first_tag(tags, "Title")
    result["album"] = result["album"] or _first_tag(tags, "WM/AlbumTitle")
    result["album_artist"] = result["album_artist"] or _first_tag(
        tags, "WM/AlbumArtist"
    )
    result["grouping"] = result["grouping"] or _first_tag(
        tags, "WM/ContentGroupDescription"
    )
    result["subtitle"] = result["subtitle"] or _first_tag(tags, "WM/SubTitle")

    return {key: value for key, value in result.items() if value}


def read_media(path: str) -> MediaRead:
    """Read container, technical information, and canonical display tags."""
    try:
        import mutagen
    except ImportError as exc:
        return MediaRead(path=path, status="error", error=str(exc))

    try:
        audio = mutagen.File(path)
    except PermissionError as exc:
        return MediaRead(path=path, status="permission_denied", error=str(exc))
    except OSError as exc:
        return MediaRead(path=path, status="unreadable", error=str(exc))
    except Exception as exc:  # Mutagen has format-specific parse exceptions.
        return MediaRead(path=path, status="malformed", error=str(exc))

    if audio is None:
        return MediaRead(path=path, status="unsupported", error="Unsupported media")

    info = getattr(audio, "info", None)
    duration = getattr(info, "length", None)
    bitrate = getattr(info, "bitrate", None)
    tags = _canonical_tags(audio)
    return MediaRead(
        path=path,
        status="ok" if tags else "empty",
        container=type(audio).__name__,
        tags=tags,
        duration=float(duration) if duration is not None else None,
        bitrate=int(bitrate) if bitrate is not None else None,
    )


def canonical_to_id3(tags: dict[str, str]) -> dict[str, str]:
    """Translate canonical fields to the legacy ID3-style names."""
    mapping = {
        "artist": "TPE1",
        "title": "TIT2",
        "album": "TALB",
        "album_artist": "TPE2",
        "grouping": "TIT1",
        "subtitle": "TIT3",
    }
    return {
        mapping[key]: value
        for key, value in tags.items()
        if key in mapping and value
    }
__all__ = [
    "MediaRead",
    "canonical_to_id3",
    "read_media",
]
