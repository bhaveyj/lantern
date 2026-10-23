from __future__ import annotations

import json
import logging
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


class SubtitleIndex:
    def __init__(self):
        self._files: dict[Path, list[Path]] = {}

    def files(self, folder: Path) -> list[Path]:
        if folder not in self._files:
            try:
                self._files[folder] = [path for path in sorted(folder.iterdir()) if path.is_file()]
            except OSError:
                self._files[folder] = []
        return self._files[folder]


def find_subtitles(video: Path, index: SubtitleIndex | None = None) -> list[Subtitle]:
    index = index or SubtitleIndex()
    found: dict[str, Path] = {}
    single_video = sum(path.suffix.lower() in VIDEO_EXTENSIONS for path in index.files(video.parent)) <= 1
    for folder in (video.parent, video.parent / "Subs", video.parent / "Subtitles", video.parent / "Subs" / video.stem, video.parent / "Subtitles" / video.stem):
        dedicated = folder.parent.name in {"Subs", "Subtitles"} and folder.name == video.stem
        for candidate in index.files(folder):
            if candidate.suffix.lower() in {".srt", ".vtt"}:
                stem, base = candidate.stem.casefold(), video.stem.casefold()
                matches = stem == base or (stem.startswith(base) and stem[len(base):len(base) + 1] in {".", " ", "_", "-"})
                if matches or dedicated or (single_video and folder != video.parent):
                    found[str(candidate.resolve()).casefold()] = candidate.resolve()
    return [Subtitle(uuid.uuid5(uuid.NAMESPACE_URL, str(p)).hex, str(p), p.stem) for p in found.values()]


def scan_files(paths: list[str]) -> list[Media]:
    index = SubtitleIndex()
    found = []
    known = set()
    for value in paths:
        try:
            path = Path(value).resolve()
            key = str(path).casefold()
            if key in known or path.suffix.lower() not in VIDEO_EXTENSIONS | AUDIO_EXTENSIONS or not path.is_file():
                continue
            found.append(Media(uuid.uuid4().hex, str(path), path.stem, find_subtitles(path, index)))
            known.add(key)
        except OSError:
            continue
    return found


class Library:
    def __init__(self, database: Path):
        database.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(database)
        if self.db.execute("PRAGMA user_version").fetchone()[0] > 1:
            self.db.close()
            raise ValueError("This library was created by a newer version of Lantern.")
        self.db.execute("CREATE TABLE IF NOT EXISTS library (slot INTEGER PRIMARY KEY, data TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS library_recovery (slot INTEGER NOT NULL, data TEXT NOT NULL, PRIMARY KEY (slot, data))")
        self.db.execute("PRAGMA user_version = 1")
        self.items: list[Media] = []
        self.recovered_records = 0
        self._saved_rows = dict(self.db.execute("SELECT slot, data FROM library ORDER BY slot"))
        seen_ids = set()
        with self.db:
            for slot, raw in self._saved_rows.items():
                try:
                    value = json.loads(raw)
                    if not isinstance(value, dict) or any(not isinstance(value.get(key), str) for key in ("id", "path", "title")):
                        raise ValueError("Invalid media record")
                    if not all(value[key].strip() for key in ("id", "path", "title")) or value["id"] in seen_ids:
                        raise ValueError("Invalid or duplicate media id")
                    value["subtitles"] = [Subtitle(**s) for s in value.get("subtitles", [])]
                    if any(not all(isinstance(getattr(subtitle, key), str) for key in ("id", "path", "label")) for subtitle in value["subtitles"]):
                        raise ValueError("Invalid subtitle record")
                    if value.get("selected_subtitle") not in {s.id for s in value["subtitles"]}:
                        value["selected_subtitle"] = None
                    self.items.append(Media(**value))
                    seen_ids.add(value["id"])
                except (ValueError, TypeError, KeyError):
                    self.db.execute("INSERT OR IGNORE INTO library_recovery VALUES (?, ?)", (slot, raw))
                    self.recovered_records += 1
        if self.recovered_records:
            logging.warning("Recovered library with %s invalid records preserved in library_recovery", self.recovered_records)

    def save(self) -> None:
        rows = {i: json.dumps(asdict(media)) for i, media in enumerate(self.items)}
        with self.db:
            self.db.executemany("DELETE FROM library WHERE slot = ?", [(slot,) for slot in self._saved_rows.keys() - rows.keys()])
            self.db.executemany("INSERT INTO library VALUES (?, ?) ON CONFLICT(slot) DO UPDATE SET data = excluded.data", [(slot, raw) for slot, raw in rows.items() if self._saved_rows.get(slot) != raw])
        self._saved_rows = rows

    def add(self, paths: list[str]) -> list[Media]:
        return self.add_scanned(scan_files(paths))

    def add_scanned(self, media_files: list[Media]) -> list[Media]:
        added = []
        known = {item.path.casefold() for item in self.items}
        for media in media_files:
            key = media.path.casefold()
            if key in known:
                continue
            if len(self.items) >= MAX_ITEMS:
                self.save()
                raise ValueError("The playlist can contain up to 200 files.")
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
