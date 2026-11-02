"""Filename-derived display text. Never changes library records or source files."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PureWindowsPath


_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_RELEASE = re.compile(
    r"(?<![\w])(?:2160p|1080[pi]|720p|480p|4320p|WEB[ ._-]?DL|WEBRip|"
    r"Blu[ ._-]?Ray|BDRip|BRRip|HDTV|[xh][ ._-]?26[45]|HEVC|AV1)(?![\w])",
    re.IGNORECASE,
)
_EPISODE = re.compile(r"(?<!\w)(?:S\d{1,2}[ ._-]?E\d{1,3}|\d{1,2}x\d{2})(?!\d)", re.IGNORECASE)


@dataclass(frozen=True)
class MediaDisplay:
    title: str
    filename: str
    format: str
    context: str
    details: tuple[str, ...]

    @property
    def summary(self) -> str:
        return " · ".join(self.details) or self.filename


def describe_media(path: str, title: str | None = None) -> MediaDisplay:
    # PureWindowsPath also understands forward slashes, including in Linux tests.
    source = PureWindowsPath(path)
    stem, filename = source.stem, source.name
    display_title = title or stem
    release = _RELEASE.search(stem)
    context, details = [], []
    if release:
        for pattern, value in (
            (r"(?<!\w)(4320p|2160p|1080[pi]|720p|480p)(?!\w)", None),
            (r"(?<!\w)(?:HDR10\+|HDR10|HDR|SDR)(?!\w)", None),
            (r"(?<!\w)Atmos(?!\w)", "Atmos"),
            (r"(?<!\w)DDP[ .]?\d(?:[ .]\d)?(?!\w)", None),
        ):
            match = re.search(pattern, stem[release.start():], re.IGNORECASE)
            if match:
                details.append(value or match.group().replace(" ", "."))
        # Only replace a filename-derived title with a high-confidence prefix.
        # A year by itself is not enough: "2001.A.Space.Odyssey" is a title.
        if not title or title == stem:
            years = [m for m in _YEAR.finditer(stem, 0, release.start())]
            year = years[-1] if years else None
            boundary = year.start() if year else release.start()
            candidate = stem[:boundary].strip(" ._-[()]")
            candidate = re.sub(r"[._]+", " ", candidate)
            candidate = re.sub(r"\s+", " ", candidate).strip()
            if candidate and (year or _EPISODE.search(candidate)):
                display_title = candidate
                if year:
                    context.append(year.group())
        if re.search(r"(?<!\w)IMAX(?!\w)", stem, re.IGNORECASE):
            context.append("IMAX")
    return MediaDisplay(display_title, filename, source.suffix[1:].upper() or "FILE", " · ".join(context), tuple(details))
