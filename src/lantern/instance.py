"""One UI per data directory, with same-user local window activation."""
from __future__ import annotations

import ctypes
import hashlib
import os
from pathlib import Path
import time

from PySide6.QtCore import QLockFile, QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

REPLY = b"ok\n"
MAX_CLIENTS = 4


class SingleInstance(QObject):
    activated = Signal()

    def __init__(self, directory: Path):
        super().__init__()
        identity = os.path.normcase(str(directory.resolve()))
        self.name = "lantern-window-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
        self.lock = QLockFile(str(directory / "lantern.lock"))
        self.lock.setStaleLockTime(0)
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.setMaxPendingConnections(MAX_CLIENTS)
        self.server.setListenBacklogSize(MAX_CLIENTS)
        self.server.newConnection.connect(self._accept)

    def acquire(self) -> bool:
        return self.lock.tryLock(100)

    def listen(self):
        if not self.lock.isLocked():
            raise RuntimeError("The application lock is required before listening.")
        if not self.server.listen(self.name):
            # Only the lock owner may clean up a socket left by a crashed process.
            QLocalServer.removeServer(self.name)
            if not self.server.listen(self.name):
                raise RuntimeError("Lantern could not create its local window connection. Please try opening it again.")

    def activate_existing(self, timeout_ms: int = 4000) -> bool:
        deadline = time.monotonic() + timeout_ms / 1000
        if os.name == "nt":
            # Transfer foreground permission before the server sees the connection.
            pid, _, _ = self.lock.getLockInfo()
            if pid > 0:
                ctypes.windll.user32.AllowSetForegroundWindow(ctypes.c_uint32(pid))
        while time.monotonic() < deadline:
            socket = QLocalSocket()
            try:
                socket.connectToServer(self.name)
                if socket.waitForConnected(min(100, max(1, int((deadline - time.monotonic()) * 1000)))):
                    socket.setReadBufferSize(len(REPLY) + 1)
                    response = bytearray()
                    while time.monotonic() < deadline:
                        response.extend(bytes(socket.readAll()))
                        if response == REPLY:
                            return True
                        if not REPLY.startswith(response):
                            break
                        remaining = max(1, int((deadline - time.monotonic()) * 1000))
                        if not socket.waitForReadyRead(remaining):
                            # A peer may deliver its final bytes with the close event.
                            response.extend(bytes(socket.readAll()))
                            return response == REPLY
            finally:
                socket.abort()
            # The first instance may hold the lock before its listener is ready.
            time.sleep(.025)
        return False

    def _accept(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            # Connecting is the entire request. Never read or interpret peer data,
            # and never wait for a client to send anything or acknowledge us.
            socket.setReadBufferSize(1)
            socket.disconnected.connect(socket.deleteLater)
            self.activated.emit()
            socket.write(REPLY)
            socket.disconnectFromServer()

    def close(self):
        self.server.close()
        for socket in self.server.findChildren(QLocalSocket):
            socket.abort()
        self.lock.unlock()
