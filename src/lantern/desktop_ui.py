"""Native drawing, popups and file details for the Quiet Queue desktop."""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap, QPolygonF, QTextLayout, QTextOption
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QFrame, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QVBoxLayout

from .theme import COLORS


def draw_lines(painter, text, rect, font, color, lines=2):
    """Word-wrap even an unbroken filename, then elide the final visible line."""
    painter.save()
    painter.setClipRect(rect)
    painter.setFont(font)
    painter.setPen(QColor(color))
    option = QTextOption()
    option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    text = text.replace("\n", "\u2028")
    layout = QTextLayout(text, font)
    layout.setTextOption(option)
    layout.beginLayout()
    y = 0.0
    visible = []
    for number in range(lines):
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(max(1, rect.width()))
        line.setPosition(QPointF(0, y))
        visible.append(line)
        y += line.height()
    layout.endLayout()
    for number, line in enumerate(visible):
        if number == lines - 1 and line.textStart() + line.textLength() < len(text.encode("utf-16-le")) // 2:
            remainder = text.encode("utf-16-le")[line.textStart() * 2:].decode("utf-16-le").replace("\u2028", " ")
            shown = painter.fontMetrics().elidedText(remainder, Qt.TextElideMode.ElideRight, int(rect.width()))
            painter.drawText(QPointF(rect.left(), rect.top() + line.y() + line.ascent()), shown)
        else:
            line.draw(painter, rect.topLeft())
    painter.restore()


class PopupPanel(QFrame):
    closed = Signal()

    def __init__(self, parent):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("controlPanel")

    def hideEvent(self, event):
        super().hideEvent(event)
        self.closed.emit()


class FileDetailsDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("File details")
        self.resize(600, 330)
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)
        self.title = QLabel()
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        self.title.setWordWrap(True)
        self.title.setObjectName("detailsTitle")
        layout.addWidget(self.title)
        self.metadata = QLabel()
        self.metadata.setTextFormat(Qt.TextFormat.PlainText)
        self.metadata.setWordWrap(True)
        self.metadata.setObjectName("status")
        layout.addWidget(self.metadata)
        layout.addWidget(QLabel("Original filename"))
        self.filename = QPlainTextEdit()
        self.filename.setObjectName("originalFilename")
        self.filename.setAccessibleName("Original filename")
        self.filename.setReadOnly(True)
        self.filename.setTabChangesFocus(True)
        self.filename.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.filename.setMinimumHeight(75)
        self.filename.setMaximumHeight(110)
        layout.addWidget(self.filename)
        layout.addWidget(QLabel("Location"))
        self.location = QLineEdit()
        self.location.setReadOnly(True)
        self.location.setAccessibleName("Original file location")
        layout.addWidget(self.location)
        self.warning = QLabel("Original file unavailable")
        self.warning.setObjectName("fileWarning")
        self.warning.hide()
        layout.addWidget(self.warning)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        copy = QPushButton("Copy filename")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.filename.toPlainText()))
        buttons.addButton(copy, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def set_media(self, display, path, missing=False):
        self.title.setText(display.title)
        self.metadata.setText(" · ".join(filter(None, (display.context, *display.details, display.format))))
        self.filename.setPlainText(display.filename)
        self.location.setText(path)
        self.location.setCursorPosition(0)
        self.warning.setVisible(missing)


@lru_cache(maxsize=32)
def control_icon(name, color="#b4c9ea"):
    """Small monochrome icons that remain sharp at Windows display scales."""
    icon = QIcon()
    for size in (16, 24, 32, 48):
        image = QPixmap(size, size)
        image.fill(Qt.GlobalColor.transparent)
        p = QPainter(image)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.scale(size / 24, size / 24)
        p.setPen(QPen(QColor(color), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)
        if name == "play":
            p.setBrush(QColor(color))
            p.drawPolygon(QPolygonF([QPointF(8, 5), QPointF(19, 12), QPointF(8, 19)]))
        elif name == "pause":
            p.drawRoundedRect(QRectF(7, 5, 3, 14), 1, 1)
            p.drawRoundedRect(QRectF(14, 5, 3, 14), 1, 1)
        elif name in {"previous", "next"}:
            if name == "previous":
                p.translate(24, 0)
                p.scale(-1, 1)
            p.drawPolygon(QPolygonF([QPointF(6, 5), QPointF(16, 12), QPointF(6, 19)]))
            p.drawLine(18, 5, 18, 19)
        elif name == "stop":
            p.drawRoundedRect(QRectF(6, 6, 12, 12), 1, 1)
        elif name == "tv":
            p.drawRoundedRect(QRectF(3, 5, 18, 13), 2, 2)
            p.drawLine(8, 21, 16, 21)
            p.drawLine(12, 18, 12, 21)
        elif name == "captions":
            p.drawRoundedRect(QRectF(2.5, 5, 19, 14), 2, 2)
            font = QFont("Segoe UI")
            font.setPixelSize(9)
            font.setBold(True)
            p.setFont(font)
            p.drawText(QRectF(3, 5, 18, 14), Qt.AlignmentFlag.AlignCenter, "CC")
        elif name == "plus":
            p.drawLine(12, 5, 12, 19)
            p.drawLine(5, 12, 19, 12)
        elif name == "file":
            p.drawRoundedRect(QRectF(4, 4, 16, 16), 2, 2)
            p.drawLine(8, 4, 8, 20)
            p.drawLine(16, 4, 16, 20)
            for y in (8, 12, 16):
                p.drawLine(4, y, 8, y)
                p.drawLine(16, y, 20, y)
        elif name == "info":
            p.drawEllipse(QRectF(4, 4, 16, 16))
            p.drawLine(12, 11, 12, 16)
            p.drawPoint(12, 8)
        elif name == "more":
            p.setPen(QPen(QColor(color), 2.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            for x in (5, 12, 19):
                p.drawPoint(x, 12)
        elif name == "volume":
            p.drawPolygon(QPolygonF([QPointF(4, 9), QPointF(8, 9), QPointF(13, 5), QPointF(13, 19), QPointF(8, 15), QPointF(4, 15)]))
            p.drawArc(QRectF(12, 5, 8, 14), -65 * 16, 130 * 16)
        p.end()
        icon.addPixmap(image)
    return icon
