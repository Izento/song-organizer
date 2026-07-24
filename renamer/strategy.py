"""Pure strategy inference from a representative folder sample."""

from __future__ import annotations

import re
from dataclasses import dataclass


_TRACK_NUMBER_RE = re.compile(r"^\d{1,3}(\s|[-.]|$)")
_COUNT_KEYS = (
    "ocremix_tagged",
    "ocremix_old",
    "tag_based",
    "filename_norm",
    "musicbrainz",
)


@dataclass(frozen=True)
class StrategySample:
    filename: str
    extraction_strategy: str


@dataclass(frozen=True)
class StrategyRecommendation:
    strategy: str | None
    note: str
    counts: dict[str, int]
    sample_size: int


def infer_strategy(samples: list[StrategySample]) -> StrategyRecommendation:
    counts = dict.fromkeys(_COUNT_KEYS, 0)
    for sample in samples:
        strategy = sample.extraction_strategy
        if strategy == "ocremix_tagged":
            counts["ocremix_tagged"] += 1
        elif strategy in {"ocremix_old_tags", "ocremix_filename"}:
            counts["ocremix_old"] += 1
        elif strategy in {"tag_based", "filename_norm"}:
            counts[strategy] += 1
        if _TRACK_NUMBER_RE.match(sample.filename):
            counts["musicbrainz"] += 1

    size = len(samples)
    if counts["musicbrainz"] > size * 0.5:
        strategy = "musicbrainz"
        note = "Files appear track-number-only; MusicBrainz lookup is needed."
    elif counts["ocremix_tagged"] > size * 0.3:
        strategy = None
        note = "Fully tagged OC ReMix files are handled automatically."
    elif counts["ocremix_old"] > size * 0.3:
        strategy = None
        note = "Legacy OC ReMix files are handled automatically."
    elif counts["tag_based"] > size * 0.4:
        strategy = None
        note = "Reliable embedded tags are available."
    else:
        strategy = "filename_norm"
        note = "Filename parsing is the strongest available evidence."
    return StrategyRecommendation(strategy, note, counts, size)


__all__ = ["StrategyRecommendation", "StrategySample", "infer_strategy"]
