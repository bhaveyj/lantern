"""Native drawing primitives for the Afterdark desktop layout."""
from __future__ import annotations

from functools import lru_cache
from math import ceil

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QPolygonF, QTextLayout, QTextOption
from PySide6.QtWidgets import QFrame, QLabel, QSizePolicy, QWidget

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


class WrappedTitle(QLabel):
    def __init__(self, text="", lines=3, parent=None):
        super().__init__(text, parent)
        self.lines = lines
        self.setTextFormat(Qt.TextFormat.PlainText)
        policy = QSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def setText(self, text):
        super().setText(text)
        self.updateGeometry()

    def sizeHint(self):
        return QSize(300, self.heightForWidth(max(300, self.width())))

    def heightForWidth(self, width):
        layout = QTextLayout(self.text().replace("\n", "\u2028"), self.font())
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(option)
        layout.beginLayout()
        height = 0
        for _ in range(self.lines):
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(max(1, width))
            height += line.height()
        layout.endLayout()
        return ceil(height) + 4

    def minimumSizeHint(self):
        return QSize(0, self.fontMetrics().lineSpacing() + 4)

    def paintEvent(self, event):
        painter = QPainter(self)
        draw_lines(painter, self.text(), QRectF(self.contentsRect()), self.font(), COLORS["text"], self.lines)


class ConnectionPanel(QFrame):
    closed = Signal()

    def __init__(self, parent):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("connectionPanel")

    def hideEvent(self, event):
        super().hideEvent(event)
        self.closed.emit()


class FeaturePanel(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("featurePanel")
        self.setMinimumWidth(280)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(.5, .5, -.5, -.5)
        clip = QPainterPath()
        clip.addRoundedRect(rect, 14, 14)
        painter.setClipPath(clip)
        background = QLinearGradient(rect.topLeft(), rect.bottomRight())
        background.setColorAt(0, QColor("#0d1524"))
        background.setColorAt(.55, QColor("#101d33"))
        background.setColorAt(1, QColor("#1b355c"))
        painter.fillRect(rect, background)
        painter.save()
        painter.translate(rect.width() - 135, rect.height() * .45)
        painter.rotate(-34)
        for offset in (0, 63, 126):
            beam = QRectF(offset, -150 + offset * .45, 33, rect.height() + 230)
            light = QLinearGradient(beam.topLeft(), beam.bottomLeft())
            light.setColorAt(0, QColor(26, 53, 96, 0))
            light.setColorAt(.42, QColor("#345990"))
            light.setColorAt(.62, QColor("#658dc8"))
            light.setColorAt(.76, QColor("#284a7f"))
            light.setColorAt(1, QColor(20, 45, 82, 0))
            painter.fillRect(beam, light)
        painter.restore()
        painter.setPen(QPen(QColor("#2b3d59"), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, 14, 14)


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
        elif name == "volume":
            p.drawPolygon(QPolygonF([QPointF(4, 9), QPointF(8, 9), QPointF(13, 5), QPointF(13, 19), QPointF(8, 15), QPointF(4, 15)]))
            p.drawArc(QRectF(12, 5, 8, 14), -65 * 16, 130 * 16)
        p.end()
        icon.addPixmap(image)
    return icon
