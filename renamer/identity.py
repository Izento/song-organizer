"""Source-neutral track identity primitives."""

from __future__ import annotations

from dataclasses import dataclass
import re

from .regular_parser import RegularName, normalize_text


_VERSION_BLOCK_RE = re.compile(r"[\(\[]\s*[^\)\]]+?\s*[\)\]]")


@dataclass(frozen=True)
class TrackIdentity:
    artist: str
    title: str
    contributors: tuple[str, ...] = ()
    qualifiers: tuple[str, ...] = ()
    version: str = ""
    source: str = "filename"

    @property
    def normalized_artist(self) -> str:
        return normalize_text(self.artist)

    @property
    def normalized_title(self) -> str:
        return normalize_text(self.title)

    @property
    def normalized_core_title(self) -> str:
        return normalize_text(_VERSION_BLOCK_RE.sub(" ", self.title))

    @property
    def key(self) -> tuple:
        return (
            self.normalized_artist,
            self.normalized_title,
            tuple(sorted(normalize_text(value) for value in self.contributors)),
            tuple(sorted(normalize_text(value) for value in self.qualifiers)),
            normalize_text(self.version),
        )

    @property
    def core_key(self) -> tuple[str, str]:
        return self.normalized_artist, self.normalized_core_title

    @classmethod
    def from_regular(cls, name: RegularName) -> "TrackIdentity":
        return cls(
            artist=name.artist,
            title=name.title,
            contributors=name.features,
            qualifiers=name.qualifiers,
            version=", ".join(name.qualifiers),
        )


__all__ = ["TrackIdentity"]
