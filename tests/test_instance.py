import asyncio
import os
import subprocess
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QSystemTrayIcon

from lantern.app import Window
from lantern.instance import REPLY, SingleInstance


async def until(app, predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        await asyncio.sleep(.01)
    pytest.fail("Window activation did not complete")


@pytest.mark.parametrize("state", ["tray", "minimized", "maximized-hidden", "starting"])
async def test_second_launch_restores_original_window_and_keeps_playback_state(qt_app, monkeypatch, tmp_path, state):
    data = tmp_path / "Lantern data"
    data.mkdir()
    instance = SingleInstance(data)
    assert instance.acquire()
    if state != "starting":
        instance.listen()
    window = Window(data, discover=False)
    instance.activated.connect(window.show_normal)
    process = None
    try:
        await until(qt_app, lambda: window.worker.ready.is_set())
        window.show()
        window.timer.stop()
        worker = window.worker
        window.connected_kind = "receiver"
        window.playing = True
        window.current_id = "keep-current-playback"
        window.position = 37
        if state == "tray":
            monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", lambda: True)
            monkeypatch.setattr(window.tray, "showMessage", lambda *args: None)
            window.close()
            assert not window.isVisible()
        elif state == "minimized":
            window.showMinimized()
            assert window.isMinimized()
        else:
            if state == "maximized-hidden":
                window.showMaximized()
            window.hide()
        if state == "starting":
            QTimer.singleShot(300, instance.listen)
        process = subprocess.Popen(
            [sys.executable, "-m", "lantern.app", "--no-discovery", "--data-dir", str(data)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=dict(os.environ, QT_QPA_PLATFORM="offscreen"),
        )
        await until(qt_app, lambda: process.poll() is not None)
        _, errors = process.communicate()
        assert process.returncode == 0, errors.decode(errors="replace")
        assert window.isVisible() and not window.isMinimized()
        if state == "maximized-hidden":
            assert window.isMaximized()
        assert window.worker is worker and worker.thread.is_alive()
        assert window.connected_kind == "receiver" and window.playing
        assert window.current_id == "keep-current-playback" and window.position == 37
        assert instance.lock.isLocked()
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(5)
        window.quit()
        await until(qt_app, lambda: window._shutdown_complete)
        instance.activated.disconnect(window.show_normal)
        window.close()
        window.deleteLater()
        instance.close()
        instance.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("message", [b"quit\n", b"x" * 1024], ids=["unknown-command", "oversized-message"])
async def test_local_endpoint_only_opens_the_window_and_never_interprets_data(qt_app, tmp_path, message):
    instance = SingleInstance(tmp_path)
    activated = []
    instance.activated.connect(lambda: activated.append(True))
    socket = QLocalSocket()
    try:
        assert instance.acquire()
        instance.listen()
        assert instance.server.socketOptions() == QLocalServer.SocketOption.UserAccessOption
        socket.connectToServer(instance.name)
        assert socket.waitForConnected(500)
        socket.write(message)
        socket.flush()
        await until(qt_app, lambda: socket.state() == QLocalSocket.LocalSocketState.UnconnectedState)
        assert activated == [True]
        assert instance.lock.isLocked()
        assert bytes(socket.readAll()) == REPLY
    finally:
        socket.abort()
        instance.close()


async def test_local_connection_does_not_wait_for_client_data(qt_app, tmp_path):
    instance = SingleInstance(tmp_path)
    socket = QLocalSocket()
    try:
        assert instance.acquire()
        instance.listen()
        socket.connectToServer(instance.name)
        assert socket.waitForConnected(500)
        await until(qt_app, lambda: socket.state() == QLocalSocket.LocalSocketState.UnconnectedState)
        assert bytes(socket.readAll()) == REPLY
    finally:
        socket.abort()
        instance.close()


def test_data_directories_are_isolated_and_live_lock_does_not_expire(qt_app, tmp_path):
    first = SingleInstance(tmp_path)
    alias = SingleInstance(tmp_path / ".")
    other_data = tmp_path / "other"
    other_data.mkdir()
    other = SingleInstance(other_data)
    try:
        assert first.name == alias.name and first.name != other.name
        assert first.acquire() and other.acquire()
        yesterday = time.time() - 86400
        os.utime(tmp_path / "lantern.lock", (yesterday, yesterday))
        assert not alias.acquire()
        assert not alias.activate_existing(timeout_ms=100)
        assert first.lock.isLocked()
    finally:
        first.close()
        alias.close()
        other.close()


def test_crashed_process_lock_and_listener_are_recovered(qt_app, tmp_path):
    script = (
        "import os,sys; from pathlib import Path; from PySide6.QtCore import QCoreApplication; "
        "from lantern.instance import SingleInstance; app=QCoreApplication([]); "
        "instance=SingleInstance(Path(sys.argv[1])); assert instance.acquire(); instance.listen(); os._exit(0)"
    )
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)], capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert (tmp_path / "lantern.lock").exists()
    instance = SingleInstance(tmp_path)
    try:
        assert instance.acquire()
        instance.listen()
        assert instance.server.isListening()
    finally:
        instance.close()
