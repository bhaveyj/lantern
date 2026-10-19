from pathlib import Path

import pytest

from lantern.library import Library, MAX_ITEMS


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
