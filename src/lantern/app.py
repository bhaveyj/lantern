from __future__ import annotations

import argparse
import asyncio
import ctypes
import logging
import os
import sys
import threading
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import QLockFile, QObject, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QFontDatabase, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton, QSizePolicy, QSlider, QSpinBox, QStyle, QStyledItemDelegate, QSystemTrayIcon, QVBoxLayout, QWidget

from .controller import Controller, Device, Discovery, validate_host
from .library import Library, Subtitle, scan_files
from .theme import COLORS, apply_theme


def app_directory() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share")) / "LanternTV"


def file_availability(items):
    result = {}
    for media_id, path in items:
        try:
            result[media_id] = Path(path).is_file()
        except OSError:
            result[media_id] = False
    return result


def icon() -> QIcon:
    result = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        image = QPixmap(size, size)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(size / 64, size / 64)
        painter.setBrush(QColor(COLORS["blue"]))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(3, 3, 58, 58, 17, 17)
        painter.setBrush(QColor(COLORS["background"]))
        painter.drawRoundedRect(19, 15, 10, 35, 3, 3)
        painter.drawRoundedRect(19, 40, 28, 10, 3, 3)
        painter.end()
        result.addPixmap(image)
    return result


class Worker(QObject):
    event = Signal(str, object)

    def __init__(self, cache: Path):
        super().__init__()
        self.loop = asyncio.new_event_loop()
        self.controller = Controller(cache, self.event.emit)
        self.ready = threading.Event()
        self._started = asyncio.Event()
        self._start_error = None
        self._closing = threading.Event()
        self._discovery_lock = threading.Lock()
        self._import_lock = asyncio.Lock()
        self._availability_revision = 0
        self.discovery: Discovery | None = None
        self.thread = threading.Thread(target=self.run, daemon=True, name="lantern-network")
        self.thread.start()

    def run(self):
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self.controller.start())
        except Exception as error:
            self._start_error = error
            self.event.emit("error", {"message": str(error)})
        finally:
            self.ready.set()
            self._started.set()
        self.loop.run_forever()
        pending = asyncio.all_tasks(self.loop)
        for task in pending:
            task.cancel()
        if pending:
            self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        self.loop.run_until_complete(self.loop.shutdown_asyncgens())
        self.loop.run_until_complete(self.loop.shutdown_default_executor(timeout=3))
        self.loop.close()

    def submit(self, name: str, *args):
        if self._closing.is_set():
            return
        async def execute():
            try:
                await self._started.wait()
                if self._start_error:
                    raise RuntimeError(f"The local media server could not start: {self._start_error}")
                if name == "import_files":
                    async with self._import_lock:
                        items = await asyncio.to_thread(scan_files, list(args[0]))
                        if not self._closing.is_set():
                            self.event.emit("library.imported", {"items": items})
                elif name == "check_files":
                    self._availability_revision += 1
                    revision = self._availability_revision
                    await asyncio.sleep(.05)
                    if revision != self._availability_revision:
                        return
                    available = await asyncio.to_thread(file_availability, args[0])
                    if revision == self._availability_revision and not self._closing.is_set():
                        self.event.emit("library.availability", available)
                elif name == "play":
                    self.controller.queue_play(*args)
                else:
                    await getattr(self.controller, name)(*args)
            except Exception as error:
                logging.exception("Action failed: %s", name)
                self.event.emit("error", {"message": str(error)})
        return asyncio.run_coroutine_threadsafe(execute(), self.loop)

    def scan(self):
        def discover():
            try:
                with self._discovery_lock:
                    if self._closing.is_set():
                        return
                    if self.discovery:
                        self.discovery.close()
                    self.discovery = Discovery(self.event.emit)
            except Exception as error:
                self.event.emit("error", {"message": f"Discovery failed: {error}. You can also connect by IP."})
        threading.Thread(target=discover, daemon=True).start()

    def close(self):
        if self._closing.is_set():
            return
        self._closing.set()
        try:
            with self._discovery_lock:
                if self.discovery:
                    self.discovery.close()
                    self.discovery = None
            future = asyncio.run_coroutine_threadsafe(self.controller.close(), self.loop)
            try:
                future.result(15)
            except Exception:
                future.cancel()
                logging.exception("Shutdown did not finish cleanly")
        except Exception:
            logging.exception("Shutdown did not finish cleanly")
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(5)
            self.event.emit("worker.closed", {})


class PlaylistDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(200, 68)

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        focused = bool(option.state & QStyle.StateFlag.State_HasFocus)
        rect = QRectF(option.rect).adjusted(2, 2, -2, -2)
        painter.setBrush(QColor(COLORS["blue_surface"] if selected else COLORS["raised"] if hovered else COLORS["surface"]))
        painter.setPen(QPen(QColor(COLORS["blue"] if selected or focused else "#1b2638"), 1))
        painter.drawRoundedRect(rect, 9, 9)
        badge = QRectF(rect.left() + 13, rect.top() + (rect.height() - 43) / 2, 43, 43)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#17376a" if selected else "#132039"))
        painter.drawRoundedRect(badge, 8, 8)
        font = QFont(option.font)
        font.setPixelSize(10)
        font.setWeight(QFont.Weight.Bold)
        painter.setFont(font)
        painter.setPen(QColor("#a8cdff" if selected else COLORS["blue"]))
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, str(index.data(int(Qt.ItemDataRole.UserRole) + 1) or "FILE"))
        lines = str(index.data(Qt.ItemDataRole.DisplayRole) or "").split("\n", 1)
        left, width = rect.left() + 71, int(rect.width() - 125)
        font.setPixelSize(14)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(COLORS["text"]))
        title = painter.fontMetrics().elidedText(lines[0], Qt.TextElideMode.ElideMiddle, width)
        painter.drawText(QRectF(left, rect.top() + 12, width, 25), Qt.AlignmentFlag.AlignVCenter, title)
        font.setPixelSize(11)
        font.setWeight(QFont.Weight.Normal)
        painter.setFont(font)
        painter.setPen(QColor("#9fbbe5" if selected else COLORS["muted"]))
        detail = painter.fontMetrics().elidedText(lines[1] if len(lines) > 1 else "", Qt.TextElideMode.ElideMiddle, width)
        painter.drawText(QRectF(left, rect.top() + 38, width, 20), Qt.AlignmentFlag.AlignVCenter, detail)
        painter.setPen(QColor(COLORS["blue"] if selected else COLORS["disabled"]))
        painter.drawText(QRectF(rect.right() - 40, rect.top(), 25, rect.height()), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{index.row() + 1:02}")
        painter.restore()


class ElidedLabel(QLabel):
    def paintEvent(self, event):
        painter = QPainter(self)
        text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, self.contentsRect().width())
        painter.drawText(self.contentsRect(), self.alignment(), text)


class Playlist(QListWidget):
    files_dropped = Signal(list)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.setSpacing(2)
        self.setMouseTracking(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setItemDelegate(PlaylistDelegate(self))
        self.placeholder = QWidget(self.viewport())
        self.placeholder.setObjectName("emptyState")
        self.placeholder.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        empty_layout = QVBoxLayout(self.placeholder)
        empty_layout.setContentsMargins(20, 12, 20, 12)
        empty_layout.setSpacing(9)
        empty_layout.addStretch()
        empty_icon = label("+", "emptyIcon")
        empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_icon.setFixedSize(48, 48)
        empty_layout.addWidget(empty_icon, 0, Qt.AlignmentFlag.AlignHCenter)
        for text, name in [("A good night starts with a file.", "emptyTitle"), ("Drop video or audio here, or choose Add files.", "muted")]:
            item = label(text, name)
            item.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_layout.addWidget(item)
        empty_layout.addStretch()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.placeholder.setGeometry(self.viewport().rect())

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            self.files_dropped.emit([u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()])
            event.acceptProposedAction()
        else:
            super().dropEvent(event)


def label(text, name=None):
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    return widget


def clock_text(value):
    seconds = int(max(0, value))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes}:{seconds:02}"


class Window(QMainWindow):
    def __init__(self, data: Path, discover=True):
        super().__init__()
        self.setWindowTitle("Lantern · Local files, big screen")
        self.setWindowIcon(icon())
        self.resize(1120, 800)
        self.setMinimumSize(960, 720)
        self.library = Library(data / "library.sqlite3")
        self._availability = {}
        self._shutdown_complete = False
        self.worker = Worker(data / "cache")
        self.worker.event.connect(self.on_event)
        self.devices: dict[str, dict] = {}
        self.connected_kind = ""
        self.playing = False
        self.finished = False
        self.busy = False
        self.position = 0.0
        self.duration = 0.0
        self.current_id = None
        self.quitting = False
        self.rendering = False
        self.build_ui()
        self.refresh_library()
        self.sync_library()
        self.tray = QSystemTrayIcon(icon(), self)
        self.tray.setToolTip("Lantern TV")
        menu = QMenu()
        menu.addAction("Open Lantern", self.show_normal)
        menu.addAction("Stop playback", lambda: self.worker.submit("stop"))
        menu.addSeparator()
        menu.addAction("Quit", self.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.show_normal() if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        self.tray.show()
        if discover:
            QTimer.singleShot(200, self.worker.scan)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(1000)
        if self.library.recovered_records:
            self.status.setText("Some invalid library records were skipped and preserved for recovery.")

    def build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(28, 24, 28, 22)
        layout.setSpacing(16)
        top = QHBoxLayout()
        top.setSpacing(10)
        monogram = QLabel()
        monogram.setPixmap(icon().pixmap(32, 32))
        monogram.setFixedSize(32, 32)
        top.addWidget(monogram)
        top.addWidget(label("lantern", "brand"))
        top.addStretch()
        top.addWidget(label("WINDOWS", "edition"))
        layout.addLayout(top)
        intro = QVBoxLayout()
        intro.setSpacing(5)
        intro.addWidget(label("Your library. Bigger screen.", "hero"))
        intro.addWidget(label("Pick a file. Choose your TV. Press play.", "muted"))
        layout.addLayout(intro)

        connection = QWidget()
        connection.setObjectName("card")
        row = QHBoxLayout(connection)
        row.setContentsMargins(18, 15, 18, 15)
        row.addWidget(label("WATCH ON", "section"))
        self.tv = QComboBox()
        self.tv.setMinimumWidth(260)
        self.tv.addItem("Looking for TVs…", None)
        row.addWidget(self.tv, 1)
        self.connect_button = QPushButton("Connect")
        self.connect_button.setObjectName("primary")
        self.connect_button.clicked.connect(self.connect_device)
        row.addWidget(self.connect_button)
        scan = QPushButton("Refresh")
        scan.clicked.connect(self.worker.scan)
        row.addWidget(scan)
        manual = QPushButton("Connect by IP")
        manual.setObjectName("outline")
        manual.clicked.connect(self.manual_connect)
        row.addWidget(manual)
        layout.addWidget(connection)

        heading = QHBoxLayout()
        self.count = label("YOUR PLAYLIST · 0 / 200", "section")
        heading.addWidget(self.count)
        heading.addStretch()
        remove = QPushButton("Remove selected")
        remove.setObjectName("quiet")
        remove.clicked.connect(self.remove_selected)
        heading.addWidget(remove)
        add = QPushButton("+ Add files")
        add.setObjectName("outline")
        add.clicked.connect(self.choose_files)
        heading.addWidget(add)
        layout.addLayout(heading)
        self.queue = Playlist()
        self.queue.files_dropped.connect(self.add_files)
        self.queue.currentItemChanged.connect(self.selection_changed)
        self.queue.itemDoubleClicked.connect(lambda _: self.play_selected())
        self.queue.model().rowsMoved.connect(self.save_order)
        layout.addWidget(self.queue, 1)
        self.empty = self.queue.placeholder

        subs = QHBoxLayout()
        subs.addWidget(label("Subtitles", "muted"))
        self.subtitles = QComboBox()
        self.subtitles.currentIndexChanged.connect(self.subtitle_changed)
        subs.addWidget(self.subtitles, 1)
        choose_sub = QPushButton("Choose subtitle…")
        choose_sub.clicked.connect(self.choose_subtitle)
        subs.addWidget(choose_sub)
        self.fit = QPushButton("Fit / Fill")
        self.fit.setCheckable(True)
        self.fit.setEnabled(False)
        self.fit.clicked.connect(lambda checked: self.worker.submit("command", "fit", "fill" if checked else "fit"))
        subs.addWidget(self.fit)
        layout.addLayout(subs)

        controls = QWidget()
        controls.setObjectName("card")
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(20, 16, 20, 16)
        controls_layout.addWidget(label("PLAYBACK", "playerHeading"))
        self.now = ElidedLabel("Nothing playing yet")
        self.now.setObjectName("now")
        self.now.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        controls_layout.addWidget(self.now)
        timeline = QHBoxLayout()
        self.elapsed = label("0:00", "muted")
        timeline.addWidget(self.elapsed)
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, 1000)
        self.seek.sliderReleased.connect(lambda: self.worker.submit("command", "seek", self.seek.value() * self.duration / 1000))
        timeline.addWidget(self.seek, 1)
        self.total = label("0:00", "muted")
        timeline.addWidget(self.total)
        controls_layout.addLayout(timeline)
        transport = QHBoxLayout()
        for text, action, value in [("Previous", "previous", None), ("−30s", "relative", -30)]:
            button = QPushButton(text)
            button.clicked.connect(lambda _, a=action, v=value: self.transport(a, v))
            transport.addWidget(button)
        self.play_button = QPushButton("Play on TV")
        self.play_button.setObjectName("primary")
        self.play_button.clicked.connect(self.play_pause)
        transport.addWidget(self.play_button)
        for text, action, value in [("+30s", "relative", 30), ("Next", "next", None), ("Stop", "stop", None)]:
            button = QPushButton(text)
            button.clicked.connect(lambda _, a=action, v=value: self.transport(a, v))
            transport.addWidget(button)
        transport.addStretch()
        transport.addWidget(label("Volume", "muted"))
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(80)
        self.volume.setMaximumWidth(100)
        self.volume.sliderReleased.connect(lambda: self.worker.submit("command", "volume", self.volume.value() / 100))
        transport.addWidget(self.volume)
        controls_layout.addLayout(transport)
        layout.addWidget(controls)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)
        self.status = label("Ready · Local files. Home network. Bigger screen.", "status")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

    def sync_library(self):
        self.worker.submit("sync", [asdict(m) for m in self.library.items])
        self.worker.submit("check_files", [(m.id, m.path) for m in self.library.items])

    def refresh_library(self):
        selected = self.selected_id()
        self.queue.clear()
        for media in self.library.items:
            suffix = Path(media.path).suffix[1:].upper()
            item = QListWidgetItem(f"{media.title}\n{'Original file unavailable' if self._availability.get(media.id) is False else Path(media.path).name}")
            item.setData(Qt.ItemDataRole.UserRole, media.id)
            item.setData(int(Qt.ItemDataRole.UserRole) + 1, suffix)
            item.setToolTip(media.path)
            self.queue.addItem(item)
            if selected == media.id:
                self.queue.setCurrentItem(item)
        self.count.setText(f"YOUR PLAYLIST · {len(self.library.items)} / 200")
        self.empty.setVisible(not self.library.items)
        if self.queue.count() and not self.queue.currentItem():
            self.queue.setCurrentRow(0)

    def selected_id(self):
        current = self.queue.currentItem() if hasattr(self, "queue") else None
        return current.data(Qt.ItemDataRole.UserRole) if current else None

    def choose_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Add video or audio", "", "Media (*.mp4 *.m4v *.mov *.mkv *.webm *.avi *.mpeg *.mpg *.ts *.m2ts *.mp3 *.m4a *.aac *.flac *.wav *.ogg)")
        self.add_files(files)

    def add_files(self, files):
        if files:
            self.status.setText("Adding files…")
            self.worker.submit("import_files", list(files))

    def remove_selected(self):
        ids = [item.data(Qt.ItemDataRole.UserRole) for item in self.queue.selectedItems()]
        if self.current_id in ids:
            self.worker.submit("stop")
        self.library.remove(ids)
        self.refresh_library()
        self.sync_library()

    def save_order(self, *args):
        ids = [self.queue.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.queue.count())]
        self.library.reorder(ids)
        self.sync_library()

    def selection_changed(self, *args):
        self.rendering = True
        self.subtitles.clear()
        self.subtitles.addItem("Off", None)
        media_id = self.selected_id()
        if media_id:
            media = self.library.get(media_id)
            for subtitle in media.subtitles:
                self.subtitles.addItem(subtitle.label, subtitle.id)
            index = self.subtitles.findData(media.selected_subtitle)
            self.subtitles.setCurrentIndex(max(0, index))
        self.rendering = False

    def subtitle_changed(self, *args):
        if self.rendering or not self.selected_id():
            return
        media = self.library.get(self.selected_id())
        media.selected_subtitle = self.subtitles.currentData()
        self.library.save()
        self.worker.submit("subtitle", media.id, media.selected_subtitle)

    def choose_subtitle(self):
        if not self.selected_id():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Add external subtitle", "", "Subtitles (*.srt *.vtt)")
        if path:
            media = self.library.get(self.selected_id())
            subtitle = Subtitle(__import__("uuid").uuid4().hex, path, Path(path).stem)
            media.subtitles.append(subtitle)
            media.selected_subtitle = subtitle.id
            self.library.save()
            self.sync_library()
            self.selection_changed()
            self.worker.submit("subtitle", media.id, subtitle.id)

    def connect_device(self):
        device_id = self.tv.currentData()
        if device_id not in self.devices:
            self.status.setText("Open Lantern on your TV, or use Connect by IP.")
            return
        self.connect_to(self.devices[device_id])

    def connect_to(self, device):
        pin = ""
        if device["kind"] == "receiver":
            pin, ok = QInputDialog.getText(self, "Pair with your TV", "Enter the six-digit code shown in Lantern on the TV:")
            if not ok:
                return
        self.sync_library()
        self.worker.submit("connect", device, pin.strip())

    def manual_connect(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Connect by IP")
        form = QFormLayout(dialog)
        host = QLineEdit()
        host.setPlaceholderText("192.168.1.25")
        form.addRow("TV address", host)
        kind = QComboBox()
        kind.addItem("Lantern TV receiver", "receiver")
        kind.addItem("Google Cast", "cast")
        form.addRow("Connection", kind)
        port = QSpinBox()
        port.setRange(1, 65535)
        port.setValue(8765)
        kind.currentIndexChanged.connect(lambda: port.setValue(8009 if kind.currentData() == "cast" else 8765))
        form.addRow("Port", port)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                address = validate_host(host.text())
                self.connect_to(vars(Device(f"manual:{address}:{port.value()}", address, address, port.value(), kind.currentData())))
            except ValueError as error:
                self.show_error(str(error))

    def play_selected(self):
        if self.selected_id():
            self.worker.submit("play", self.selected_id())

    def play_pause(self):
        if self.current_id and self.current_id == self.selected_id() and not self.finished:
            self.worker.submit("command", "pause" if self.playing else "play")
        else:
            self.play_selected()

    def transport(self, action, value):
        if action == "stop":
            self.worker.submit("stop")
        elif action == "relative":
            self.worker.submit("command", "seek", max(0, self.position + value))
        else:
            self.worker.submit("command", action, value)

    def on_event(self, event, data):
        if event == "worker.closed":
            self.finish_quit()
            return
        if self.quitting:
            return
        if event == "library.imported":
            try:
                added = self.library.add_scanned(data["items"])
                self.status.setText(f"Added {len(added)} file{'s' if len(added) != 1 else ''}")
            except ValueError as error:
                self.show_error(str(error))
            self.refresh_library()
            self.sync_library()
        elif event == "library.availability":
            available = {m.id: data.get(m.id, self._availability.get(m.id, True)) for m in self.library.items}
            if available != self._availability:
                self._availability = available
                self.refresh_library()
        elif event == "device":
            self.devices[data["id"]] = data
            selected = self.tv.currentData()
            self.tv.clear()
            for device_id, device in self.devices.items():
                self.tv.addItem(f"{device['name']} · {'Google Cast' if device['kind'] == 'cast' else 'Lantern TV'}", device_id)
            index = self.tv.findData(selected)
            if index >= 0:
                self.tv.setCurrentIndex(index)
        elif event == "device.removed":
            self.devices.pop(data["id"], None)
            index = self.tv.findData(data["id"])
            if index >= 0:
                self.tv.removeItem(index)
        elif event == "connected":
            self.connected_kind = data["kind"]
            self.connect_button.setText("Reconnect")
            self.fit.setEnabled(self.connected_kind == "receiver")
        elif event == "disconnected":
            self.connected_kind = ""
            self.current_id = None
            self.playing = False
            self.play_button.setText("Play on TV")
            self.connect_button.setText("Connect")
            self.fit.setEnabled(False)
        elif event == "status":
            self.status.setText(data["message"])
            self.busy = data.get("busy", False)
            self.progress.setVisible(self.busy)
            self.progress.setRange(0, 0 if self.busy else 100)
        elif event == "progress":
            self.progress.setRange(0, 100)
            self.progress.setValue(int(data["percent"]))
            self.status.setText(f"Preparing audio · {data['title']} · {data['percent']:.0f}%")
        elif event == "state":
            self.current_id = data.get("id")
            self.playing = data.get("playing", False)
            self.finished = data.get("playerState") == "IDLE" and data.get("idleReason") == "FINISHED"
            self.position = data.get("position", 0)
            self.duration = data.get("duration", 0)
            self.play_button.setText("Pause" if self.playing else "Play again" if self.finished else "Resume" if self.current_id else "Play on TV")
            media = next((m for m in self.library.items if m.id == self.current_id), None)
            if media and "subtitleId" in data and media.selected_subtitle != data["subtitleId"]:
                media.selected_subtitle = data["subtitleId"]
                self.library.save()
                if self.selected_id() == media.id:
                    self.selection_changed()
            self.now.setText(media.title if media else "Nothing playing yet")
            self.now.setToolTip(media.title if media else "")
            self.update_time()
        elif event == "error":
            self.busy = False
            self.progress.hide()
            self.show_error(data["message"])
        elif event == "notice":
            self.status.setText(data["message"])
        self.sleep_hold()

    def tick(self):
        if self.playing and not self.seek.isSliderDown():
            self.position = min(self.duration, self.position + 1)
            self.update_time()

    def update_time(self):
        self.elapsed.setText(clock_text(self.position))
        self.total.setText(clock_text(self.duration))
        if not self.seek.isSliderDown():
            self.seek.setValue(int(self.position * 1000 / self.duration) if self.duration else 0)

    def sleep_hold(self):
        if os.name == "nt":
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | (0x00000001 if self.playing or self.busy else 0))

    def show_error(self, message):
        self.status.setText(message)
        QMessageBox.warning(self, "Lantern", message)

    def show_normal(self):
        self.showNormal()
        self.activateWindow()

    def closeEvent(self, event):
        if not self.quitting and self.connected_kind and QSystemTrayIcon.isSystemTrayAvailable():
            self.hide()
            self.tray.showMessage("Lantern is still running", "Open Lantern from the tray, or choose Stop playback or Quit.")
            event.ignore()
        else:
            self.quit()
            event.accept()

    def quit(self):
        if self.quitting:
            return
        self.quitting = True
        self.timer.stop()
        self.setEnabled(False)
        self.status.setText("Closing Lantern…")
        threading.Thread(target=self.worker.close, daemon=True, name="lantern-shutdown").start()

    def finish_quit(self):
        if self._shutdown_complete:
            return
        self._shutdown_complete = True
        self.library.close()
        self.tray.hide()
        if os.name == "nt":
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        QApplication.instance().quit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=app_directory())
    parser.add_argument("--no-discovery", action="store_true")
    parser.add_argument("--preview", type=Path, help="Save an offscreen UI preview and exit")
    args = parser.parse_args()
    args.data_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=args.data_dir / "lantern.log", level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    if os.name == "nt":
        # Qt's offscreen plugin does not always discover the Windows font store.
        for font_name in ("segoeui.ttf", "segoeuib.ttf", "seguisym.ttf"):
            font_file = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / font_name
            if font_file.exists():
                QFontDatabase.addApplicationFont(str(font_file))
    app.setApplicationName("Lantern TV")
    app.setStyle("Fusion")
    apply_theme(app)
    lock = QLockFile(str(args.data_dir / "lantern.lock"))
    if not lock.tryLock(100):
        QMessageBox.information(None, "Lantern", "Lantern is already running. Open it from the system tray.")
        return
    window = Window(args.data_dir, not args.no_discovery and not args.preview)
    window.show()
    if args.preview:
        def capture():
            window.grab().save(str(args.preview))
            window.quit()
        QTimer.singleShot(700, capture)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
