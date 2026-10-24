import asyncio
import os
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from lantern.app import Window, Worker
from lantern.library import Library, scan_files
from lantern.subtitle_dialog import SubtitleDialog


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


async def qt_wait(app, predicate):
    for _ in range(300):
        app.processEvents()
        if predicate(): return
        await asyncio.sleep(.01)
    pytest.fail("Qt operation did not complete")


async def test_worker_submission_does_not_wait_on_ui_thread(qt_app, monkeypatch, tmp_path):
    release = threading.Event()
    async def start(controller):
        while not release.is_set(): await asyncio.sleep(.01)
    monkeypatch.setattr("lantern.app.Controller.start", start)
    worker = Worker(tmp_path / "cache")
    try:
        future = worker.submit("sync", [])
        assert future is not None
        assert not worker.ready.is_set()
        release.set()
        await asyncio.wait_for(asyncio.wrap_future(future), 2)
    finally:
        release.set()
        await asyncio.to_thread(worker.close)


async def test_import_keeps_qt_responsive_and_shutdown_finishes(qt_app, monkeypatch, tmp_path):
    started = threading.Event()
    release = threading.Event()
    def scan(paths):
        started.set()
        release.wait(2)
        return scan_files(paths)
    monkeypatch.setattr("lantern.app.scan_files", scan)
    source = tmp_path / "movie.mp4"
    source.touch()
    window = Window(tmp_path / "data", discover=False)
    try:
        window.add_files([str(source)])
        assert await asyncio.to_thread(started.wait, 1)
        ticked = []
        QTimer.singleShot(0, lambda: ticked.append(True))
        qt_app.processEvents()
        assert ticked
        assert not window.library.items
        release.set()
        await qt_wait(qt_app, lambda: len(window.library.items) == 1)
        assert window.library.items[0].path == str(source.resolve())
    finally:
        release.set()
        window.quit()
        await qt_wait(qt_app, lambda: window._shutdown_complete)
        assert not window.worker.thread.is_alive()


async def test_missing_file_status_is_checked_in_background(qt_app, tmp_path):
    data = tmp_path / "data"
    source = tmp_path / "movie.mp4"
    source.touch()
    library = Library(data / "library.sqlite3")
    library.add([str(source)])
    library.close()
    source.unlink()
    window = Window(data, discover=False)
    try:
        await qt_wait(qt_app, lambda: bool(window._availability))
        assert "Original file unavailable" in window.queue.item(0).text()
    finally:
        window.quit()
        await qt_wait(qt_app, lambda: window._shutdown_complete)


async def test_created_subtitle_attaches_once_and_ignores_removed_sources(qt_app, tmp_path):
    source = tmp_path / "movie.mp4"
    source.touch()
    subtitle = tmp_path / "created.srt"
    subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\nनमस्ते\n", encoding="utf-8")
    window = Window(tmp_path / "data", discover=False)
    try:
        media = window.library.add([str(source)])[0]
        window.refresh_library()
        data = {"media_id": media.id, "source": media.path, "path": str(subtitle), "label": "Hindi · Embedded"}
        window.on_event("subtitles.ready", data)
        window.on_event("subtitles.ready", data)
        assert len(media.subtitles) == 1
        assert media.selected_subtitle == media.subtitles[0].id
        assert media.subtitles[0].path == str(subtitle)
        window.library.remove([media.id])
        window.refresh_library()
        window.on_event("subtitles.ready", data)
        assert not window.library.items
    finally:
        window.quit()
        await qt_wait(qt_app, lambda: window._shutdown_complete)


async def test_subtitle_dialog_defaults_to_local_eco_and_cloud_requires_key(qt_app, tmp_path):
    source = tmp_path / "movie.mp4"
    source.touch()
    window = Window(tmp_path / "data", discover=False)
    try:
        media = window.library.add([str(source)])[0]
        dialog = SubtitleDialog(window, media)
        dialog.inspected({"inspection": {"duration": 7200, "subtitles": [], "audio": [{"index": 1, "label": "English"}]}})
        assert dialog.mode.currentData() == "local"
        assert dialog.cpu.currentData() == 2
        assert dialog.model.currentData() == "small"
        assert dialog.create.isEnabled()
        dialog.mode.setCurrentIndex(dialog.mode.findData("cloud"))
        assert not dialog.create.isEnabled()
        dialog.api_key.setText("test-only-key")
        assert dialog.create.isEnabled()
        assert "US$0.62" in dialog.explanation.text()
        dialog.reject()
        assert not dialog.api_key.text()
    finally:
        window.quit()
        await qt_wait(qt_app, lambda: window._shutdown_complete)
