from pathlib import Path
import sqlite3

import pytest

from lantern.library import Library, MAX_ITEMS, scan_files


def test_references_subtitles_order_and_persistence(tmp_path):
    movie = tmp_path / "My Movie.mkv"
    movie.write_bytes(b"original")
    (tmp_path / "My Movie.en.srt").write_text("captions")
    (tmp_path / "Another Movie.srt").write_text("unrelated")
    subfolder = tmp_path / "Subs"
    subfolder.mkdir()
    (subfolder / "French.vtt").write_text("WEBVTT")
    second = tmp_path / "Music.flac"
    second.touch()
    database = tmp_path / "library.db"
    library = Library(database)
    library.add([str(movie), str(movie), str(second)])
    assert len(library.items) == 2
    assert {Path(s.path).name for s in library.items[0].subtitles} == {"My Movie.en.srt", "French.vtt"}
    ids = [m.id for m in reversed(library.items)]
    library.reorder(ids)
    library.close()
    restored = Library(database)
    assert [m.id for m in restored.items] == ids
    assert movie.read_bytes() == b"original"
    movie.unlink()
    assert not restored.items[1].exists
    restored.close()


def test_library_cap_and_invalid_order(tmp_path):
    library = Library(tmp_path / "library.db")
    paths = []
    for i in range(MAX_ITEMS + 1):
        path = tmp_path / f"{i}.mp4"
        path.touch()
        paths.append(str(path))
    with pytest.raises(ValueError, match="200"):
        library.add(paths)
    assert len(library.items) == MAX_ITEMS
    with pytest.raises(ValueError, match="order"):
        library.reorder([])
    library.close()


def test_subtitles_match_movie_boundaries_and_dedicated_folders(tmp_path):
    for name in ("Movie", "Movie2"):
        (tmp_path / f"{name}.mkv").touch()
    shared = tmp_path / "Subs"
    shared.mkdir()
    for name in ("Movie.en.srt", "Movie2.en.srt", "Unrelated.en.srt", "French.vtt"):
        (shared / name).touch()
    dedicated = shared / "Movie"
    dedicated.mkdir()
    (dedicated / "Spanish.srt").touch()
    media = scan_files([str(tmp_path / "Movie.mkv")])[0]
    assert {Path(s.path).name for s in media.subtitles} == {"Movie.en.srt", "Spanish.srt"}


def test_batch_import_scans_each_folder_once(tmp_path, monkeypatch):
    files = []
    for index in range(3):
        source = tmp_path / f"Movie{index}.mkv"
        source.touch()
        files.append(str(source))
    original = Path.iterdir
    reads = []
    def iterdir(path):
        reads.append(path)
        return original(path)
    monkeypatch.setattr(Path, "iterdir", iterdir)
    assert len(scan_files(files)) == 3
    assert reads.count(tmp_path) == 1


def test_library_updates_only_changed_rows(tmp_path):
    for index in range(3): (tmp_path / f"{index}.mp4").touch()
    library = Library(tmp_path / "library.db")
    library.add([str(tmp_path / f"{index}.mp4") for index in range(3)])
    before = library.db.total_changes
    library.save()
    assert library.db.total_changes == before
    library.items[1].title = "Changed title"
    library.save()
    assert library.db.total_changes == before + 1
    library.close()


def test_invalid_library_record_is_preserved_without_losing_valid_items(tmp_path):
    source = tmp_path / "movie.mp4"
    source.touch()
    path = tmp_path / "library.db"
    library = Library(path)
    library.add([str(source)])
    library.close()
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO library VALUES (1, ?)", ("invalid JSON",))
    restored = Library(path)
    assert len(restored.items) == 1
    assert restored.recovered_records == 1
    assert restored.db.execute("SELECT data FROM library_recovery WHERE slot = 1").fetchone()[0] == "invalid JSON"
    restored.save()
    restored.close()
    reopened = Library(path)
    assert len(reopened.items) == 1
    assert reopened.recovered_records == 0
    reopened.close()
