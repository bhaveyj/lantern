from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

VIDEO_EXTENSIONS = {".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".mpeg", ".mpg", ".ts", ".m2ts"}
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg"}
MAX_ITEMS = 200


@dataclass
class Subtitle:
    id: str
    path: str
    label: str


@dataclass
class Media:
    id: str
    path: str
    title: str
    subtitles: list[Subtitle] = field(default_factory=list)
    selected_subtitle: str | None = None

    @property
    def exists(self) -> bool:
        return Path(self.path).is_file()


def find_subtitles(video: Path) -> list[Subtitle]:
    found: dict[str, Path] = {}
    for folder in (video.parent, video.parent / "Subs", video.parent / "Subtitles"):
        if not folder.is_dir():
            continue
        for candidate in sorted(folder.iterdir()):
            if candidate.is_file() and candidate.suffix.lower() in {".srt", ".vtt"}:
                # A subtitle must belong to this video, or live in its dedicated folder.
                if folder != video.parent or candidate.stem.lower().startswith(video.stem.lower()):
                    found[str(candidate.resolve()).casefold()] = candidate.resolve()
    return [Subtitle(uuid.uuid5(uuid.NAMESPACE_URL, str(p)).hex, str(p), p.stem) for p in found.values()]


class Library:
    def __init__(self, database: Path):
        database.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(database)
        self.db.execute("CREATE TABLE IF NOT EXISTS library (slot INTEGER PRIMARY KEY, data TEXT NOT NULL)")
        self.items: list[Media] = []
        for (raw,) in self.db.execute("SELECT data FROM library ORDER BY slot"):
            value = json.loads(raw)
            value["subtitles"] = [Subtitle(**s) for s in value.get("subtitles", [])]
            self.items.append(Media(**value))

    def save(self) -> None:
        with self.db:
            self.db.execute("DELETE FROM library")
            self.db.executemany("INSERT INTO library VALUES (?, ?)", [(i, json.dumps(asdict(m))) for i, m in enumerate(self.items)])

    def add(self, paths: list[str]) -> list[Media]:
        added = []
        known = {str(Path(item.path).resolve()).casefold() for item in self.items}
        for value in paths:
            path = Path(value).resolve()
            if not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS | AUDIO_EXTENSIONS:
                continue
            key = str(path).casefold()
            if key in known:
                continue
            if len(self.items) >= MAX_ITEMS:
                self.save()
                raise ValueError("The playlist can contain up to 200 files.")
            media = Media(uuid.uuid4().hex, str(path), path.stem, find_subtitles(path))
            self.items.append(media)
            known.add(key)
            added.append(media)
        self.save()
        return added

    def get(self, media_id: str) -> Media:
        return next(m for m in self.items if m.id == media_id)

    def reorder(self, ids: list[str]) -> None:
        if len(ids) != len(self.items) or set(ids) != {m.id for m in self.items}:
            raise ValueError("Playlist order does not match the library.")
        by_id = {m.id: m for m in self.items}
        self.items = [by_id[i] for i in ids]
        self.save()

    def remove(self, ids: list[str]) -> None:
        self.items = [m for m in self.items if m.id not in ids]
        self.save()

    def close(self) -> None:
        self.db.close()
