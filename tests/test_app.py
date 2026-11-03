import asyncio
import os
import threading

import aiohttp
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from lantern.app import PlaylistDelegate, Window, Worker
from lantern.library import Library, Subtitle, scan_files
from lantern.subtitle_dialog import SubtitleDialog

LONG_NAME = "Spider-Man.Brand.New.Day.2026.IMAX.2160p.CORE.WEB-DL.DDP5.1.Atmos.HDR.H.265-NotSoNewDayForSony.mkv"


async def test_connect_asks_for_code_only_when_worker_requests_it(qt_app, monkeypatch, tmp_path):
    window = Window(tmp_path / "data", discover=False)
    calls, prompts = [], []
    submit = window.worker.submit
    try:
        await qt_wait(qt_app, lambda: window.worker.ready.is_set())
        monkeypatch.setattr(window.worker, "submit", lambda *args: calls.append(args))
        monkeypatch.setattr("lantern.app.QInputDialog.getText", lambda *args: (prompts.append(args) or ("123 456", True)))
        device = {"id": "tv", "name": "TV", "host": "192.168.1.10", "port": 8765, "kind": "receiver"}
        window.connect_to(device)
        assert not prompts
        assert ("connect", device, "") in calls
        window.on_event("pairing.code_required", {"device": device, "again": False})
        assert len(prompts) == 1
        assert calls[-1] == ("connect", device, "123456")
        assert "identity" not in prompts[0][2].lower()
    finally:
        monkeypatch.setattr(window.worker, "submit", submit)
        window.quit()
        await qt_wait(qt_app, lambda: window._shutdown_complete)


async def test_readable_names_preserve_files_records_selection_and_full_details(qt_app, tmp_path):
    first = tmp_path / LONG_NAME
    first.write_bytes(b"original media content")
    second = tmp_path / "Second.2026.1080p.WEB-DL.mkv"
    second.touch()
    window = Window(tmp_path / "data", discover=False)
    try:
        media = window.library.add([str(first), str(second)])
        window.refresh_library()
        assert window.queue.item(0).text().startswith("Spider-Man Brand New Day\n")
        assert not window.file_details_dialog.isVisible()
        window.show_file_details()
        assert window.file_details_dialog.filename.toPlainText() == LONG_NAME
        assert "2160p" in window.queue.item(0).text()
        window.queue.item(1).setSelected(True)
        before = {item.data(256) for item in window.queue.selectedItems()}
        window.original_names.setChecked(True)
        assert LONG_NAME in window.queue.item(0).text()
        assert {item.data(256) for item in window.queue.selectedItems()} == before
        assert window.selected_id() == media[0].id
        assert media[0].title == first.stem
        assert media[0].path == str(first.resolve())
        assert first.read_bytes() == b"original media content"
        assert window.file_details_dialog.filename.toPlainText() == LONG_NAME
    finally:
        window.quit()
        await qt_wait(qt_app, lambda: window._shutdown_complete)


async def test_dock_play_targets_selected_file_without_changing_active_playback(qt_app, monkeypatch, tmp_path):
    source = tmp_path / LONG_NAME
    source.touch()
    second = tmp_path / "Second.2026.1080p.WEB-DL.mkv"
    second.touch()
    window = Window(tmp_path / "data", discover=False)
    submit = window.worker.submit
    calls = []
    try:
        await qt_wait(qt_app, lambda: window.worker.ready.is_set())
        first_media, second_media = window.library.add([str(source), str(second)])
        window.refresh_library()
        monkeypatch.setattr(window.worker, "submit", lambda *args: calls.append(args))
        window.on_event("state", {"id": first_media.id, "playing": True, "position": 37, "duration": 100})
        window.queue.setCurrentRow(1)
        window.play_button.click()
        assert calls == [("play", second_media.id)]
        assert window.current_id == first_media.id
        assert window.position == 37
        assert window.now.text() == "Spider-Man Brand New Day"
        window.show_file_details()
        assert window.file_details_dialog.title.text() == "Second"
        assert window.current_id == first_media.id
        window.queue.setCurrentRow(0)
        window.play_button.click()
        assert calls[-1] == ("command", "pause")
    finally:
        monkeypatch.setattr(window.worker, "submit", submit)
        window.quit()
        await qt_wait(qt_app, lambda: window._shutdown_complete)


async def test_row_info_preserves_multiselection_and_active_playback(qt_app, monkeypatch, tmp_path):
    sources = [tmp_path / LONG_NAME, tmp_path / "Second.2026.1080p.WEB-DL.mkv"]
    for source in sources:
        source.touch()
    window = Window(tmp_path / "data", discover=False)
    submit, calls = window.worker.submit, []
    try:
        await qt_wait(qt_app, lambda: window.worker.ready.is_set())
        media = window.library.add([str(path) for path in sources])
        window.refresh_library()
        window.show()
        qt_app.processEvents()
        window.queue.item(1).setSelected(True)
        selected = {item.data(256) for item in window.queue.selectedItems()}
        window.on_event("state", {"id": media[0].id, "playing": True, "position": 37, "duration": 100})
        monkeypatch.setattr(window.worker, "submit", lambda *args: calls.append(args))
        info = PlaylistDelegate.info_rect(window.queue.visualItemRect(window.queue.item(1)))
        QTest.mouseClick(window.queue.viewport(), Qt.MouseButton.LeftButton, pos=info.center())
        assert window.file_details_dialog.isVisible()
        assert window.file_details_dialog.filename.toPlainText() == sources[1].name
        assert {item.data(256) for item in window.queue.selectedItems()} == selected
        assert window.selected_id() == media[0].id
        assert window.current_id == media[0].id and window.playing and window.position == 37
        assert not calls
    finally:
        monkeypatch.setattr(window.worker, "submit", submit)
        window.quit()
        await qt_wait(qt_app, lambda: window._shutdown_complete)


async def test_subtitle_popup_targets_selected_file_and_keeps_job_state(qt_app, monkeypatch, tmp_path):
    sources = [tmp_path / "First.mp4", tmp_path / "Second.mp4"]
    for source in sources:
        source.touch()
    window = Window(tmp_path / "data", discover=False)
    submit, calls = window.worker.submit, []
    try:
        await qt_wait(qt_app, lambda: window.worker.ready.is_set())
        first, second = window.library.add([str(path) for path in sources])
        second.subtitles.append(Subtitle("second-subtitle", str(tmp_path / "second.srt"), "English"))
        window.refresh_library()
        monkeypatch.setattr(window.worker, "submit", lambda *args: calls.append(args))
        window.on_event("state", {"id": first.id, "playing": True, "position": 37, "duration": 100})
        window.queue.setCurrentRow(1)
        window.captions_button.click()
        assert window.subtitle_panel.isVisible()
        assert window.subtitle_scope.text() == "For: Second"
        assert not calls
        window.subtitles.setCurrentIndex(1)
        assert calls == [("subtitle", second.id, "second-subtitle")]
        assert window.current_id == first.id and window.position == 37
        window.on_event("subtitles.started", {})
        window.queue.setCurrentRow(0)
        assert not window.create_subtitles.isEnabled()
        window.on_event("subtitles.finished", {})
        assert window.create_subtitles.isEnabled()
    finally:
        monkeypatch.setattr(window.worker, "submit", submit)
        window.quit()
        await qt_wait(qt_app, lambda: window._shutdown_complete)


async def test_connection_header_does_not_claim_cast_is_encrypted(qt_app, tmp_path):
    window = Window(tmp_path / "data", discover=False)
    try:
        window.on_event("connected", {"kind": "cast", "name": "Living room"})
        assert window.connection_panel.isHidden()
        assert "securely" not in window.device_button.toolTip()
        assert not window.fit.isEnabled()
        window.on_event("connected", {"kind": "receiver", "name": "Living room"})
        assert "securely" in window.device_button.toolTip()
        assert window.fit.isEnabled()
        window.device_button.click()
        assert not window.connection_panel.isHidden()
    finally:
        window.quit()
        await qt_wait(qt_app, lambda: window._shutdown_complete)


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
        assert dialog.cloud_privacy.isChecked()
        dialog.cloud_budget.setValue(.5)
        assert not dialog.create.isEnabled()
        dialog.cloud_budget.setValue(1)
        assert dialog.create.isEnabled()
        dialog.reject()
        assert not dialog.api_key.text()
    finally:
        window.quit()
        await qt_wait(qt_app, lambda: window._shutdown_complete)


async def test_locked_subtitle_cleanup_does_not_disable_playback_worker(qt_app, monkeypatch, tmp_path):
    async def locked_cleanup(jobs):
        raise PermissionError("locked old subtitle directory")
    monkeypatch.setattr("lantern.app.SubtitleJobs.cleanup_stale", locked_cleanup)
    worker = Worker(tmp_path / "cache")
    try:
        result = worker.submit("sync", [])
        await asyncio.wait_for(asyncio.wrap_future(result), 3)
        assert worker._start_error is None
    finally:
        await asyncio.to_thread(worker.close)


async def test_created_hindi_subtitles_are_served_as_webvtt_through_real_worker(qt_app, tmp_path):
    source = tmp_path / "movie.mp4"
    source.touch()
    subtitle = tmp_path / "generated.srt"
    subtitle.write_text("1\n00:00:00,100 --> 00:00:02,000\nनमस्ते दुनिया।\n", encoding="utf-8")
    window = Window(tmp_path / "data", discover=False)
    try:
        media = window.library.add([str(source)])[0]
        window.refresh_library()
        window.on_event("subtitles.ready", {"media_id": media.id, "source": media.path, "path": str(subtitle), "label": "Hindi · AI generated"})
        await asyncio.wait_for(asyncio.wrap_future(window.worker.submit("sync", [__import__("dataclasses").asdict(media)])), 3)
        url = window.worker.controller.server.subtitle_url("127.0.0.1", media.selected_subtitle)
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                assert response.status == 200
                text = await response.text()
                assert text.startswith("WEBVTT")
                assert "00:00:00.100 --> 00:00:02.000" in text
                assert "नमस्ते दुनिया।" in text
        assert source.read_bytes() == b""
    finally:
        window.quit()
        await qt_wait(qt_app, lambda: window._shutdown_complete)
