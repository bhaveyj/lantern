from __future__ import annotations

import argparse
import asyncio
import ctypes
import logging
import os
import sys
import threading
import uuid
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import QItemSelectionModel, QObject, QPoint, QPointF, QRect, QRectF, QSignalBlocker, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QFontDatabase, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton, QSizePolicy, QSlider, QSpinBox, QStyle, QStyledItemDelegate, QSystemTrayIcon, QVBoxLayout, QWidget

from .controller import Controller, Device, Discovery, validate_host
from .library import Library, Subtitle, scan_files
from .subtitle_dialog import SubtitleDialog
from .subtitle_jobs import SubtitleJobs, inspect_media
from .credentials import CredentialStore
from .desktop_ui import FileDetailsDialog, PopupPanel, control_icon, draw_lines
from .instance import SingleInstance
from .presentation import describe_media
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
        self.subtitle_jobs = SubtitleJobs(cache.parent / "subtitles", self.event.emit)
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
            try:
                self.loop.run_until_complete(self.subtitle_jobs.cleanup_stale())
            except OSError:
                logging.warning("Old subtitle temporary files could not be cleaned; playback is still available.")
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
                elif name == "subtitle_inspect":
                    media_id, source = args
                    try:
                        inspection = await inspect_media(Path(source))
                        self.event.emit("subtitles.inspected", {"media_id": media_id, "source": source, "inspection": asdict(inspection)})
                    except Exception as error:
                        self.event.emit("subtitles.inspection_failed", {"media_id": media_id, "source": source, "message": str(error)})
                elif name == "subtitle_extract":
                    self.subtitle_jobs.start_extraction(*args)
                elif name == "subtitle_local":
                    self.subtitle_jobs.start_local(*args)
                elif name == "subtitle_cloud":
                    self.subtitle_jobs.start_cloud(*args)
                elif name == "subtitle_cancel":
                    await self.subtitle_jobs.cancel()
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
            async def shutdown():
                await self.subtitle_jobs.close()
                await self.controller.close()
            future = asyncio.run_coroutine_threadsafe(shutdown(), self.loop)
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
        return QSize(320, 80)

    @staticmethod
    def info_rect(rect):
        return QRect(rect.right() - 42, rect.center().y() - 17, 34, 34)

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        focused = bool(option.state & QStyle.StateFlag.State_HasFocus)
        rect = QRectF(option.rect).adjusted(0, 1, 0, -1)
        painter.setPen(Qt.PenStyle.NoPen)
        if selected or hovered:
            painter.setBrush(QColor("#111f35" if selected else "#101720"))
            painter.drawRoundedRect(rect, 5, 5)
        else:
            painter.setPen(QPen(QColor("#1a2330"), 1))
            painter.drawLine(QPointF(rect.left(), rect.bottom()), QPointF(rect.right(), rect.bottom()))
        if selected:
            painter.setPen(QPen(QColor(COLORS["blue"]), 2))
            painter.drawLine(QPointF(rect.left() + 1, rect.top() + 17), QPointF(rect.left() + 1, rect.bottom() - 17))
        elif focused:
            painter.setPen(QPen(QColor("#435a79"), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(.5, .5, -.5, -.5), 5, 5)
        control_icon("file", COLORS["blue"] if selected else COLORS["muted"]).paint(painter, QRect(int(rect.left()) + 17, int(rect.top()) + 22, 18, 18))
        info = self.info_rect(option.rect)
        control_icon("info", "#a7b8d1" if selected else COLORS["muted"]).paint(painter, info.adjusted(9, 9, -9, -9))
        font = QFont(option.font)
        font.setPixelSize(11)
        font.setWeight(QFont.Weight.Normal)
        painter.setFont(font)
        painter.setPen(QColor(COLORS["muted"]))
        painter.drawText(QRectF(info.left() - 59, rect.top(), 48, rect.height()), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, str(index.data(int(Qt.ItemDataRole.UserRole) + 1) or "FILE"))
        lines = str(index.data(Qt.ItemDataRole.DisplayRole) or "").split("\n", 1)
        detail = lines[1] if len(lines) > 1 else ""
        left, width = rect.left() + 52, max(1, int(rect.width() - 171))
        font.setPixelSize(14)
        painter.setFont(font)
        single_line = painter.fontMetrics().horizontalAdvance(lines[0]) <= width
        draw_lines(painter, lines[0], QRectF(left, rect.top() + (13 if detail else 24 if single_line else 20), width, 37), font, COLORS["text"], 2)
        font.setPixelSize(12)
        painter.setFont(font)
        painter.setPen(QColor("#d6ad79" if detail == "Original file unavailable" else COLORS["muted"]))
        detail = painter.fontMetrics().elidedText(detail, Qt.TextElideMode.ElideMiddle, width)
        painter.drawText(QRectF(left, rect.top() + (37 if single_line else 54), width, 19), Qt.AlignmentFlag.AlignVCenter, detail)
        painter.restore()


class ElidedLabel(QLabel):
    def paintEvent(self, event):
        painter = QPainter(self)
        text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, self.contentsRect().width())
        painter.drawText(self.contentsRect(), self.alignment(), text)


class Playlist(QListWidget):
    files_dropped = Signal(list)
    details_requested = Signal(str)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.setSpacing(0)
        self._info_pressed = None
        self.setMouseTracking(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setItemDelegate(PlaylistDelegate(self))
        self.setAccessibleName("Playlist")
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
        for text, name in [("Your playlist is empty", "emptyTitle"), ("Drop video or audio here, or choose Add files.", "muted")]:
            item = label(text, name)
            item.setWordWrap(True)
            item.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_layout.addWidget(item)
        empty_layout.addStretch()

    def info_item(self, position):
        item = self.itemAt(position)
        if item and PlaylistDelegate.info_rect(self.visualItemRect(item)).contains(position):
            return item
        return None

    def mousePressEvent(self, event):
        item = self.info_item(event.position().toPoint())
        self._info_pressed = item.data(Qt.ItemDataRole.UserRole) if item and event.button() == Qt.MouseButton.LeftButton else None
        if self._info_pressed is not None:
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self._info_pressed is not None:
            media_id, self._info_pressed = self._info_pressed, None
            item = self.info_item(event.position().toPoint())
            if item and item.data(Qt.ItemDataRole.UserRole) == media_id:
                self.details_requested.emit(media_id)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if self.info_item(event.position().toPoint()):
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

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
    widget.setTextFormat(Qt.TextFormat.PlainText)
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
        self.setWindowTitle("Lantern · Video on your TV")
        self.setWindowIcon(icon())
        self.resize(1120, 730)
        self.setMinimumSize(900, 600)
        self.library = Library(data / "library.sqlite3")
        self.cloud_credentials = CredentialStore(data / "deepgram.key")
        self._availability = {}
        self._shutdown_complete = False
        self.subtitle_dialog = None
        self.subtitle_busy = False
        self.worker = Worker(data / "cache")
        self.worker.event.connect(self.on_event)
        self.devices: dict[str, dict] = {}
        self.connected_kind = ""
        self.connected_name = ""
        self.playing = False
        self.finished = False
        self.busy = False
        self.position = 0.0
        self.duration = 0.0
        self.current_id = None
        self.web_source = {}
        self.live = False
        self.seekable = True
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
        self.tray.activated.connect(lambda reason: self.show_normal() if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick) else None)
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
        root.setObjectName("quietQueue")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(30, 26, 30, 22)
        layout.setSpacing(0)

        top = QHBoxLayout()
        top.setSpacing(10)
        monogram = QLabel()
        monogram.setPixmap(icon().pixmap(27, 27))
        monogram.setFixedSize(27, 27)
        top.addWidget(monogram)
        top.addWidget(label("lantern", "brand"))
        top.addStretch()
        self.device_button = QPushButton("Choose your TV  ▾")
        self.device_button.setObjectName("deviceButton")
        self.device_button.setIcon(control_icon("tv"))
        self.device_button.setCheckable(True)
        self.device_button.setAccessibleName("TV connection")
        self.device_button.setToolTip("Choose a TV or manage your connection")
        top.addWidget(self.device_button)
        top.addSpacing(12)
        self.add_button = QPushButton("Add files")
        self.add_button.setObjectName("headerAction")
        self.add_button.setIcon(control_icon("plus"))
        self.add_button.clicked.connect(self.choose_files)
        self.link_button = QPushButton("Paste video link")
        self.link_button.setObjectName("headerAction")
        self.link_button.clicked.connect(self.paste_video_link)
        top.addWidget(self.link_button)
        top.addWidget(self.add_button)
        layout.addLayout(top)
        layout.addSpacing(35)

        self.connection_panel = PopupPanel(self)
        connection = QVBoxLayout(self.connection_panel)
        connection.setContentsMargins(18, 16, 18, 16)
        connection.setSpacing(12)
        connection.addWidget(label("Watch on", "panelHeading"))
        receivers = QHBoxLayout()
        self.tv = QComboBox()
        self.tv.setMinimumWidth(240)
        self.tv.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.tv.setAccessibleName("Available TVs")
        self.tv.addItem("Looking for TVs…", None)
        receivers.addWidget(self.tv, 1)
        self.connect_button = QPushButton("Connect")
        self.connect_button.setObjectName("primary")
        self.connect_button.clicked.connect(self.connect_device)
        receivers.addWidget(self.connect_button)
        connection.addLayout(receivers)
        connection_tools = QHBoxLayout()
        refresh = QPushButton("Refresh")
        refresh.setObjectName("smallQuiet")
        refresh.clicked.connect(self.worker.scan)
        connection_tools.addWidget(refresh)
        manual = QPushButton("Connect by IP…")
        manual.setObjectName("smallQuiet")
        manual.clicked.connect(self.manual_connect)
        connection_tools.addWidget(manual)
        connection_tools.addStretch()
        options = self.icon_button("more", "Connection settings")
        connection_options = QMenu(options)
        connection_options.addAction("Paired TVs…", lambda: self.worker.submit("paired_devices"))
        connection_options.addAction("Advanced connection details…", lambda: self.worker.submit("connection_details"))
        connection_options.addSeparator()
        connection_options.addAction("Reset saved pairings…", self.reset_pairings)
        options.setMenu(connection_options)
        connection_tools.addWidget(options)
        connection.addLayout(connection_tools)
        self.device_button.toggled.connect(self.toggle_connection_panel)
        self.connection_panel.closed.connect(lambda: self.clear_popup_button(self.device_button))

        heading = QHBoxLayout()
        heading.setSpacing(11)
        heading.addWidget(label("Playlist", "queueHeading"))
        self.count = label("0", "section")
        heading.addWidget(self.count)
        heading.addStretch()
        self.playlist_options = self.icon_button("more", "Playlist options")
        self.playlist_menu = QMenu(self.playlist_options)
        self.details_action = self.playlist_menu.addAction("File details…")
        self.details_action.setShortcut("Ctrl+I")
        self.details_action.triggered.connect(lambda: self.show_file_details())
        self.addAction(self.details_action)
        self.original_names = self.playlist_menu.addAction("Show original filenames")
        self.original_names.setCheckable(True)
        self.original_names.toggled.connect(self.refresh_library)
        self.playlist_menu.addSeparator()
        self.remove_action = self.playlist_menu.addAction("Remove selected")
        self.remove_action.triggered.connect(self.remove_selected)
        self.playlist_options.setMenu(self.playlist_menu)
        heading.addWidget(self.playlist_options)
        layout.addLayout(heading)
        layout.addSpacing(15)

        self.queue = Playlist()
        self.queue.setObjectName("playlist")
        self.queue.files_dropped.connect(self.add_files)
        self.queue.details_requested.connect(self.show_file_details)
        self.queue.currentItemChanged.connect(self.selection_changed)
        self.queue.itemSelectionChanged.connect(self.update_selection_summary)
        self.queue.itemDoubleClicked.connect(lambda _: self.play_selected())
        self.queue.model().rowsMoved.connect(self.save_order)
        self.queue.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.queue.customContextMenuRequested.connect(self.show_playlist_menu)
        remove_shortcut = QAction(self.queue)
        remove_shortcut.setShortcut("Delete")
        remove_shortcut.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        remove_shortcut.triggered.connect(self.remove_selected)
        self.queue.addAction(remove_shortcut)
        layout.addWidget(self.queue, 1)
        self.empty = self.queue.placeholder
        self.file_details_dialog = FileDetailsDialog(self)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 14, 0, 17)
        self.status = label("Local files. Your TV.", "status")
        self.status.setWordWrap(True)
        footer.addWidget(self.status, 1)
        self.selection_status = label("", "status")
        footer.addWidget(self.selection_status)
        layout.addLayout(footer)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.hide()
        layout.addWidget(self.progress)

        self.subtitle_activity = QWidget()
        subtitle_row = QHBoxLayout(self.subtitle_activity)
        subtitle_row.setContentsMargins(0, 0, 0, 12)
        self.subtitle_status = label("", "muted")
        self.subtitle_status.setWordWrap(True)
        subtitle_row.addWidget(self.subtitle_status, 1)
        self.subtitle_progress = QProgressBar()
        self.subtitle_progress.setMaximumWidth(140)
        self.subtitle_progress.setTextVisible(False)
        subtitle_row.addWidget(self.subtitle_progress)
        self.cancel_subtitles = QPushButton("Cancel")
        self.cancel_subtitles.setObjectName("smallQuiet")
        self.cancel_subtitles.clicked.connect(lambda: self.worker.submit("subtitle_cancel"))
        subtitle_row.addWidget(self.cancel_subtitles)
        self.subtitle_activity.hide()
        layout.addWidget(self.subtitle_activity)

        controls = QWidget()
        controls.setObjectName("playbackDock")
        dock = QHBoxLayout(controls)
        dock.setContentsMargins(0, 17, 0, 0)
        dock.setSpacing(26)
        now_column = QVBoxLayout()
        now_column.setSpacing(4)
        self.now = ElidedLabel("Nothing selected")
        self.now.setObjectName("now")
        self.now.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        now_column.addWidget(self.now)
        self.playback_destination = ElidedLabel("Choose a TV to start watching")
        self.playback_destination.setObjectName("status")
        self.playback_destination.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        now_column.addWidget(self.playback_destination)
        now_widget = QWidget()
        now_widget.setObjectName("nowDetails")
        now_widget.setLayout(now_column)
        now_column.setContentsMargins(0, 0, 0, 0)
        now_widget.setMinimumWidth(170)
        now_widget.setMaximumWidth(290)
        dock.addWidget(now_widget, 1)

        center = QVBoxLayout()
        center.setSpacing(7)
        transport = QHBoxLayout()
        transport.setSpacing(17)
        transport.addStretch()
        previous = self.icon_button("previous", "Previous file")
        previous.clicked.connect(lambda: self.transport("previous", None))
        transport.addWidget(previous)
        self.play_button = QPushButton()
        self.play_button.setObjectName("playCircle")
        self.play_button.setFixedSize(38, 38)
        self.play_button.setIconSize(QSize(17, 17))
        self.set_play_button("Play on TV", False)
        self.play_button.clicked.connect(self.play_pause)
        transport.addWidget(self.play_button)
        next_file = self.icon_button("next", "Next file")
        next_file.clicked.connect(lambda: self.transport("next", None))
        transport.addWidget(next_file)
        transport.addStretch()
        center.addLayout(transport)
        timeline = QHBoxLayout()
        timeline.setSpacing(10)
        self.elapsed = label("0:00", "time")
        timeline.addWidget(self.elapsed)
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setAccessibleName("Playback position")
        self.seek.setRange(0, 1000)
        self.seek.setEnabled(False)
        self.seek.sliderReleased.connect(lambda: self.worker.submit("command", "seek", self.seek.value() * self.duration / 1000))
        timeline.addWidget(self.seek, 1)
        self.total = label("0:00", "time")
        timeline.addWidget(self.total)
        self.go_live = QPushButton("Go live")
        self.go_live.setObjectName("smallQuiet")
        self.go_live.clicked.connect(lambda: self.worker.submit("command", "live"))
        self.go_live.hide()
        timeline.addWidget(self.go_live)
        center.addLayout(timeline)
        dock.addLayout(center, 2)

        tools = QHBoxLayout()
        tools.setSpacing(2)
        self.captions_button = self.icon_button("captions", "Subtitles for selected file")
        self.captions_button.setCheckable(True)
        tools.addWidget(self.captions_button)
        self.volume_button = self.icon_button("volume", "Volume")
        self.volume_button.setCheckable(True)
        tools.addWidget(self.volume_button)
        more = self.icon_button("more", "More playback controls")
        self.playback_menu = QMenu(more)
        self.playback_menu.addAction("Skip back 30 seconds", lambda: self.transport("relative", -30))
        self.playback_menu.addAction("Skip forward 30 seconds", lambda: self.transport("relative", 30))
        self.playback_menu.addSeparator()
        self.fit = self.playback_menu.addAction("Fill TV screen")
        self.fit.setCheckable(True)
        self.fit.setEnabled(False)
        self.fit.setToolTip("Picture fit is available with the Lantern TV receiver")
        self.fit.triggered.connect(lambda checked: self.worker.submit("command", "fit", "fill" if checked else "fit"))
        self.playback_menu.addSeparator()
        self.stop_action = self.playback_menu.addAction("Stop playback", lambda: self.transport("stop", None))
        more.setMenu(self.playback_menu)
        tools.addWidget(more)
        dock.addLayout(tools, 1)
        tools.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(controls)

        self.subtitle_panel = PopupPanel(self)
        subs = QVBoxLayout(self.subtitle_panel)
        subs.setContentsMargins(18, 16, 18, 16)
        subs.setSpacing(11)
        subs.addWidget(label("Subtitles", "panelHeading"))
        self.subtitle_scope = ElidedLabel("")
        self.subtitle_scope.setObjectName("status")
        self.subtitle_scope.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        subs.addWidget(self.subtitle_scope)
        self.subtitles = QComboBox()
        self.subtitles.setAccessibleName("Subtitle track for selected file")
        self.subtitles.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.subtitles.setMinimumContentsLength(12)
        self.subtitles.currentIndexChanged.connect(self.subtitle_changed)
        subs.addWidget(self.subtitles)
        sub_actions = QHBoxLayout()
        self.choose_subtitles = QPushButton("Choose file…")
        self.choose_subtitles.setObjectName("smallQuiet")
        self.choose_subtitles.setAccessibleName("Choose subtitle file")
        self.choose_subtitles.clicked.connect(self.choose_subtitle)
        sub_actions.addWidget(self.choose_subtitles)
        self.create_subtitles = QPushButton("Create subtitles…")
        self.create_subtitles.setObjectName("smallQuiet")
        self.create_subtitles.clicked.connect(self.open_subtitle_dialog)
        sub_actions.addWidget(self.create_subtitles)
        subs.addLayout(sub_actions)
        self.captions_button.toggled.connect(lambda visible: self.toggle_popup(self.subtitle_panel, self.captions_button, visible, 350, above=True))
        self.subtitle_panel.closed.connect(lambda: self.clear_popup_button(self.captions_button))

        self.volume_panel = PopupPanel(self)
        volume_layout = QVBoxLayout(self.volume_panel)
        volume_layout.setContentsMargins(18, 16, 18, 16)
        volume_heading = QHBoxLayout()
        volume_heading.addWidget(label("Volume", "panelHeading"))
        volume_heading.addStretch()
        self.volume_value = label("80%", "status")
        volume_heading.addWidget(self.volume_value)
        volume_layout.addLayout(volume_heading)
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setAccessibleName("Volume")
        self.volume.setRange(0, 100)
        self.volume.setValue(80)
        self.volume.valueChanged.connect(lambda value: self.volume_value.setText(f"{value}%"))
        self.volume.sliderReleased.connect(lambda: self.worker.submit("command", "volume", self.volume.value() / 100))
        volume_layout.addWidget(self.volume)
        self.volume_button.toggled.connect(lambda visible: self.toggle_popup(self.volume_panel, self.volume_button, visible, 240, above=True))
        self.volume_panel.closed.connect(lambda: self.clear_popup_button(self.volume_button))
        self.tv.currentIndexChanged.connect(self.update_connection_button)

    def icon_button(self, name, description):
        button = QPushButton()
        button.setObjectName("transport")
        button.setIcon(control_icon(name))
        button.setIconSize(QSize(17, 17))
        button.setFixedSize(34, 34)
        button.setAccessibleName(description)
        button.setToolTip(description)
        return button

    def set_play_button(self, text, playing):
        self.play_button.setIcon(control_icon("pause" if playing else "play", "#ffffff"))
        self.play_button.setAccessibleName(text)
        self.play_button.setToolTip(text)

    def toggle_popup(self, panel, button, visible, width, above=False):
        if not visible:
            panel.hide()
            return
        panel.ensurePolished()
        panel.adjustSize()
        panel.resize(width, panel.sizeHint().height())
        offset = QPoint(button.width() - panel.width(), -panel.height() - 10 if above else button.height() + 10)
        position = button.mapToGlobal(offset)
        available = button.screen().availableGeometry()
        position.setX(max(available.left() + 8, min(position.x(), available.right() - panel.width() - 8)))
        position.setY(max(available.top() + 8, min(position.y(), available.bottom() - panel.height() - 8)))
        panel.move(position)
        panel.show()

    def toggle_connection_panel(self, visible):
        self.toggle_popup(self.connection_panel, self.device_button, visible, 460)

    @staticmethod
    def clear_popup_button(button):
        with QSignalBlocker(button):
            button.setChecked(False)

    def update_connection_button(self, *args):
        name = self.connected_name if self.connected_kind else "Choose your TV"
        shown = self.device_button.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, 195)
        self.device_button.setText(shown + "  ▾")
        self.device_button.setToolTip(name + (" · Connected securely" if self.connected_kind == "receiver" else " · Google Cast" if self.connected_kind else " · Connection settings"))
        self.update_player()

    def show_playlist_menu(self, position):
        item = self.queue.itemAt(position)
        if item and not item.isSelected():
            self.queue.setCurrentItem(item)
        self.playlist_menu.popup(self.queue.viewport().mapToGlobal(position))

    def show_file_details(self, media_id=None):
        media = next((item for item in self.library.items if item.id == (media_id or self.selected_id())), None)
        if media is None:
            return
        self.file_details_dialog.set_media(describe_media(media.path, media.title), media.path, self._availability.get(media.id) is False)
        self.file_details_dialog.show()
        self.file_details_dialog.raise_()
        self.file_details_dialog.activateWindow()

    def update_selection_summary(self):
        count = len(self.queue.selectedItems())
        self.selection_status.setText(f"{count} selected" if count else "")
        self.remove_action.setEnabled(count > 0)
        self.details_action.setEnabled(bool(self.selected_id()))

    def update_player(self):
        selected = self.selected_id()
        media_id = self.current_id or selected
        media = next((item for item in self.library.items if item.id == media_id), None)
        web_playing = bool(self.current_id and self.current_id == self.web_source.get("id"))
        title = describe_media(media.path, media.title).title if media else self.web_source.get("title", "Video link") if web_playing else "Nothing selected"
        self.now.setText(title)
        self.now.setToolTip(title if media or web_playing else "")
        if self.current_id:
            state = "Finished" if self.finished else "Playing" if self.playing else "Paused"
            destination = state + (f" on {self.connected_name}" if self.connected_name else " on your TV")
        elif self.connected_kind:
            destination = f"Ready on {self.connected_name or 'your TV'}"
        else:
            destination = "Choose a TV to start watching"
        self.playback_destination.setText(destination)
        self.playback_destination.setToolTip(destination)
        if not self.current_id:
            text, playing = "Play on TV", False
        elif self.current_id == selected or web_playing:
            text, playing = "Pause" if self.playing else "Play again" if self.finished else "Resume", self.playing
        else:
            text, playing = "Play selected file on TV", False
        self.set_play_button(text, playing)
        self.play_button.setEnabled(bool(selected) or web_playing)
        self.stop_action.setEnabled(bool(self.current_id) or self.busy)
        self.seek.setEnabled(bool(self.current_id) and self.seekable and self.duration > 0)


    def sync_library(self):
        self.worker.submit("sync", [asdict(m) for m in self.library.items])
        self.worker.submit("check_files", [(m.id, m.path) for m in self.library.items])

    def refresh_library(self):
        selected = self.selected_id()
        selected_ids = {item.data(Qt.ItemDataRole.UserRole) for item in self.queue.selectedItems()}
        current = None
        with QSignalBlocker(self.queue):
            self.queue.clear()
            for media in self.library.items:
                display = describe_media(media.path, media.title)
                title = display.filename if self.original_names.isChecked() else display.title
                detail = "Original file unavailable" if self._availability.get(media.id) is False else " · ".join(filter(None, (display.context, *display.details)))
                item = QListWidgetItem(f"{title}\n{detail}")
                item.setData(Qt.ItemDataRole.UserRole, media.id)
                item.setData(int(Qt.ItemDataRole.UserRole) + 1, display.format)
                item.setToolTip(display.filename + "\n" + media.path)
                self.queue.addItem(item)
                if media.id in selected_ids:
                    item.setSelected(True)
                if selected == media.id:
                    current = item
            if current is not None:
                self.queue.setCurrentItem(current, QItemSelectionModel.SelectionFlag.NoUpdate)
            elif self.queue.count():
                self.queue.setCurrentRow(0)
        self.count.setText(str(len(self.library.items)))
        self.count.setToolTip(f"{len(self.library.items)} of 200 files")
        self.empty.setVisible(not self.library.items)
        self.selection_changed()

    def selected_id(self):
        current = self.queue.currentItem() if hasattr(self, "queue") else None
        return current.data(Qt.ItemDataRole.UserRole) if current else None

    def choose_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Add video or audio", "", "Media (*.mp4 *.m4v *.mov *.mkv *.webm *.avi *.mpeg *.mpg *.ts *.m2ts *.mp3 *.m4a *.aac *.flac *.wav *.ogg)")
        self.add_files(files)

    def firefox_help(self):
        QMessageBox.information(self, "Watch from Firefox", "Connect to your TV here, then open the Lantern TV extension in Firefox while a video is playing. Choose the main video and click Play on TV.\n\nKeep Lantern running and the Firefox tab open. Stop playback here or in the extension to end the session.\n\nFor this development build, extension setup is described in docs/web-streaming.md in the project folder.")

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
        title = describe_media(media.path, media.title).title if media_id else "No file selected"
        self.subtitle_scope.setText("For: " + title)
        self.subtitle_scope.setToolTip(title)
        self.captions_button.setEnabled(bool(media_id))
        self.choose_subtitles.setEnabled(bool(media_id))
        self.create_subtitles.setEnabled(bool(media_id) and not self.subtitle_busy)
        if not media_id:
            self.subtitle_panel.hide()
        self.update_selection_summary()
        self.update_player()

    def subtitle_changed(self, *args):
        if self.rendering or not self.selected_id():
            return
        media = self.library.get(self.selected_id())
        media.selected_subtitle = self.subtitles.currentData()
        self.library.save()
        self.worker.submit("subtitle", media.id, media.selected_subtitle)

    def choose_subtitle(self):
        self.subtitle_panel.hide()
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

    def open_subtitle_dialog(self):
        self.subtitle_panel.hide()
        if not self.selected_id() or self.subtitle_busy:
            return
        media = self.library.get(self.selected_id())
        self.subtitle_dialog = SubtitleDialog(self, media)
        dialog = self.subtitle_dialog
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        def cleared(_):
            if self.subtitle_dialog is dialog:
                self.subtitle_dialog = None
        dialog.finished.connect(cleared)
        self.subtitle_dialog.open()
        self.worker.submit("subtitle_inspect", media.id, media.path)

    def attach_created_subtitle(self, data):
        media = next((m for m in self.library.items if m.id == data["media_id"] and m.path == data["source"]), None)
        if media is None:
            return  # The source may have been removed while a background job ran.
        subtitle_id = uuid.uuid5(uuid.NAMESPACE_URL, data["path"]).hex
        if not any(s.id == subtitle_id for s in media.subtitles):
            media.subtitles.append(Subtitle(subtitle_id, data["path"], data["label"]))
        media.selected_subtitle = subtitle_id
        self.library.save()
        self.sync_library()
        self.selection_changed()
        self.worker.submit("subtitle", media.id, subtitle_id)
        self.status.setText("Saved subtitles attached" + (" · reused existing result" if data.get("reused") else ""))

    def connect_device(self):
        device_id = self.tv.currentData()
        if device_id not in self.devices:
            self.status.setText("Open Lantern on your TV, or use Connect by IP.")
            return
        self.connect_to(self.devices[device_id])

    def reset_pairings(self):
        if QMessageBox.question(self, "Reset saved pairings", "Forget all saved TVs? You’ll need their six-digit codes to connect again.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.worker.submit("reset_pairings")

    def connect_to(self, device):
        self.connection_panel.hide()
        pin = ""
        if device["kind"] != "receiver":
            if QMessageBox.question(self, "Google Cast connection", "Google Cast uses a compatibility connection with unencrypted local media URLs. Continue on this trusted home network?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
        self.sync_library()
        self.worker.submit("connect", device, pin.strip())

    def manual_connect(self):
        self.connection_panel.hide()
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

    def paste_video_link(self):
        if not self.connected_kind:
            self.show_error("Connect to your TV, then paste a video link.")
            return
        url, accepted = QInputDialog.getText(self, "Play a video link", "Paste an HTTPS video or watch-page link")
        if accepted and url.strip():
            self.worker.submit("play_link", url.strip())

    def play_pause(self):
        if self.current_id and (self.current_id == self.selected_id() or self.current_id == self.web_source.get("id")) and not self.finished:
            self.worker.submit("command", "pause" if self.playing else "play")
        else:
            self.play_selected()

    def transport(self, action, value):
        if action == "stop":
            self.worker.submit("stop")
        elif action == "relative":
            if self.seekable:
                self.worker.submit("command", "seek", max(0, self.position + value))
        else:
            self.worker.submit("command", action, value)

    def on_event(self, event, data):
        if event == "pairing.code_required":
            if self.quitting: return
            prompt = ("Pair this TV again.\n\n" if data.get("again") else "") + "Enter the six-digit code shown in Lantern on your TV:"
            pin, accepted = QInputDialog.getText(self, "Pair with your TV", prompt)
            if accepted:
                pin = "".join(pin.split())
                if len(pin) != 6 or not pin.isascii() or not pin.isdigit():
                    self.show_error("Enter all six digits from the TV screen.")
                    return
                self.worker.submit("connect", data["device"], pin)
            else:
                self.worker.submit("disconnect")
            return
        if event == "paired.devices":
            dialog = QDialog(self)
            dialog.setWindowTitle("Paired TVs")
            layout = QVBoxLayout(dialog)
            layout.addWidget(QLabel("Saved TVs reconnect without entering a code."))
            devices = QListWidget()
            for item in data["items"]:
                devices.addItem(item["name"])
                devices.item(devices.count() - 1).setData(Qt.ItemDataRole.UserRole, item["identity"])
            layout.addWidget(devices)
            if not data["items"]: layout.addWidget(QLabel("No TVs paired yet. Select a TV and click Connect."))
            forget = QPushButton("Forget selected TV")
            forget.setEnabled(False)
            devices.currentItemChanged.connect(lambda current, _: forget.setEnabled(current is not None))
            def forget_selected():
                selected = devices.currentItem()
                if selected:
                    self.worker.submit("forget_pairing", selected.data(Qt.ItemDataRole.UserRole))
                    dialog.accept()
            forget.clicked.connect(forget_selected)
            layout.addWidget(forget)
            dialog.exec()
            return
        if event == "connection.details":
            identity = data.get("identity")
            text = "Connect to a Lantern TV to view its details."
            if identity:
                text = "Connection: encrypted TLS\nPairing: remembered\n\nSHA-256 TV identity:\n" + " ".join(identity[index:index + 4] for index in range(0, len(identity), 4))
            QMessageBox.information(self, "Advanced connection details", text)
            return
        if event == "worker.closed":
            self.finish_quit()
            return
        if self.quitting:
            return
        if event.startswith("subtitles."):
            if event in {"subtitles.inspected", "subtitles.inspection_failed"}:
                dialog = self.subtitle_dialog
                if dialog and dialog.isVisible() and (dialog.media_id, dialog.source) == (data["media_id"], data["source"]):
                    if event == "subtitles.inspected":
                        dialog.inspected(data)
                    else:
                        dialog.inspection_failed(data["message"])
            elif event == "subtitles.started":
                self.subtitle_busy = True
                self.create_subtitles.setEnabled(False)
                self.subtitle_activity.show()
                self.subtitle_status.setText("Preparing subtitles…")
                self.subtitle_progress.setRange(0, 0)
            elif event == "subtitles.progress":
                self.subtitle_status.setText(data["message"])
                percent = data.get("percent")
                self.subtitle_progress.setRange(0, 0 if percent is None else 100)
                if percent is not None:
                    self.subtitle_progress.setValue(int(percent))
            elif event == "subtitles.ready":
                self.attach_created_subtitle(data)
            elif event == "subtitles.cancelled":
                self.status.setText("Subtitle job cancelled")
            elif event == "subtitles.failed":
                self.show_error(data["message"])
            elif event == "subtitles.finished":
                self.subtitle_busy = False
                self.create_subtitles.setEnabled(bool(self.selected_id()))
                self.subtitle_activity.hide()
            self.sleep_hold()
            return
        if event == "web.source":
            self.web_source = data
            if data:
                self.now.setText(data["title"])
                self.status.setText("Video link playing")
        elif event == "library.imported":
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
            self.connected_name = data.get("name", "TV")
            self.connect_button.setText("Reconnect")
            self.fit.setEnabled(self.connected_kind == "receiver")
            self.device_button.setChecked(False)
            self.update_connection_button()
        elif event == "disconnected":
            self.connected_kind = ""
            self.connected_name = ""
            self.current_id = None
            self.playing = False
            self.position = self.duration = 0
            self.update_time()
            self.set_play_button("Play on TV", False)
            self.connect_button.setText("Connect")
            self.fit.setEnabled(False)
            self.update_connection_button()
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
            self.live = data.get("live", False)
            self.seekable = data.get("seekable", not self.live)
            self.go_live.setVisible(self.live)
            self.seek.setEnabled(self.seekable and self.duration > 0)
            media = next((m for m in self.library.items if m.id == self.current_id), None)
            if media and "subtitleId" in data and media.selected_subtitle != data["subtitleId"]:
                media.selected_subtitle = data["subtitleId"]
                self.library.save()
                if self.selected_id() == media.id:
                    self.selection_changed()
            self.update_time()
        elif event == "error":
            self.busy = False
            self.progress.hide()
            self.show_error(data["message"])
        elif event == "notice":
            self.status.setText(data["message"])
        self.update_player()
        self.sleep_hold()

    def tick(self):
        if self.playing and not self.seek.isSliderDown():
            self.position = min(self.duration, self.position + 1) if self.duration > 0 else self.position + 1
            self.update_time()

    def update_time(self):
        self.elapsed.setText(clock_text(self.position))
        self.total.setText("LIVE" if self.live else clock_text(self.duration))
        if not self.seek.isSliderDown():
            self.seek.setValue(int(self.position * 1000 / self.duration) if self.duration else 0)

    def sleep_hold(self):
        if os.name == "nt":
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | (0x00000001 if self.playing or self.busy or self.subtitle_busy else 0))

    def show_error(self, message):
        self.status.setText(message)
        QMessageBox.warning(self, "Lantern", message)

    def show_normal(self):
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.show()
        self.raise_()
        self.activateWindow()
        modal = QApplication.activeModalWidget()
        if modal and self.isAncestorOf(modal):
            modal.raise_()
            modal.activateWindow()

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
        for panel in (self.connection_panel, self.subtitle_panel, self.volume_panel):
            panel.hide()
        self.file_details_dialog.close()
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
    instance = SingleInstance(args.data_dir)
    try:
        if not instance.acquire():
            if instance.activate_existing():
                return
            # The old instance may have finished shutting down during activation.
            if not instance.acquire():
                QMessageBox.warning(None, "Lantern", "Lantern could not reopen its existing window. It may still be starting or closing. Please try opening it again shortly.")
                return
        instance.listen()
        window = Window(args.data_dir, not args.no_discovery and not args.preview)
        instance.activated.connect(window.show_normal)
        window.show()
        if args.preview:
            def capture():
                window.grab().save(str(args.preview))
                window.quit()
            QTimer.singleShot(700, capture)
        result = app.exec()
    finally:
        instance.close()
    sys.exit(result)


if __name__ == "__main__":
    main()
