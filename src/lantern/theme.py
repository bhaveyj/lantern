"""Black and blue visual theme for the Windows sender."""
from string import Template

from PySide6.QtGui import QColor, QPalette

COLORS = {
    "background": "#07090d",
    "surface": "#0e121a",
    "raised": "#141b27",
    "border": "#202b3d",
    "text": "#edf3ff",
    "muted": "#8c9ab0",
    "disabled": "#536079",
    "blue": "#3984ff",
    "blue_hover": "#5b9aff",
    "blue_pressed": "#2469dc",
    "blue_surface": "#102447",
}

STYLES = Template("""
QWidget { background:$background; color:$text; font-family:'Segoe UI'; font-size:13px; }
QMainWindow, QDialog, QMessageBox { background:$background; }
QLabel { background:transparent; border:none; }
QLabel#brand { font-size:25px; font-weight:700; }
QLabel#edition { color:$blue; background:$blue_surface; border:1px solid $border; border-radius:7px; padding:5px 10px; font-size:11px; font-weight:650; }
QLabel#hero { font-size:30px; font-weight:650; }
QLabel#muted { color:$muted; }
QLabel#section { color:$muted; font-size:11px; font-weight:650; }
QLabel#playerHeading { color:$blue; font-size:11px; font-weight:650; }
QLabel#now { font-size:18px; font-weight:600; }
QLabel#status { color:$muted; font-size:11px; }
QWidget#card { background:$surface; border:1px solid $border; border-radius:14px; }
QWidget#emptyState { background:transparent; border:none; }
QLabel#emptyIcon { color:$blue; background:$blue_surface; border:1px solid $border; border-radius:13px; font-size:27px; font-weight:500; }
QLabel#emptyTitle { font-size:18px; font-weight:600; }
QPushButton { background:$raised; border:1px solid $border; border-radius:8px; padding:10px 14px; font-weight:600; }
QPushButton:hover { background:$blue_surface; border-color:$blue; }
QPushButton:pressed { background:#153566; border-color:$blue; }
QPushButton:focus { border:1px solid $blue; }
QPushButton:checked { background:$blue_surface; color:$blue_hover; border-color:$blue; }
QPushButton:disabled { color:$disabled; background:$surface; border-color:#1a2230; }
QPushButton#primary { background:$blue; color:#ffffff; border:1px solid $blue; }
QPushButton#primary:hover { background:$blue_hover; border-color:$blue_hover; }
QPushButton#primary:pressed { background:$blue_pressed; border-color:$blue_pressed; }
QPushButton#primary:focus { border:1px solid #bdd7ff; }
QPushButton#outline { background:$blue_surface; color:#a5c8ff; border:1px solid #2d5290; }
QPushButton#outline:hover { color:#ffffff; border-color:$blue; }
QPushButton#quiet { background:transparent; color:$muted; border:1px solid transparent; }
QPushButton#quiet:hover { background:$raised; color:$text; border-color:$border; }
QComboBox, QLineEdit, QSpinBox { background:$background; border:1px solid $border; border-radius:8px; padding:9px 12px; min-height:20px; selection-background-color:$blue; }
QComboBox:focus, QLineEdit:focus, QSpinBox:focus { border-color:$blue; }
QComboBox:hover, QLineEdit:hover, QSpinBox:hover { border-color:#3a5276; }
QComboBox QAbstractItemView { background:$raised; color:$text; border:1px solid $border; selection-background-color:$blue_surface; selection-color:$text; padding:6px; outline:none; }
QListWidget { background:#090d14; border:1px solid $border; border-radius:12px; padding:6px; outline:none; }
QListWidget:focus { border-color:#315287; }
QSlider { background:transparent; border:none; min-height:20px; }
QSlider::groove:horizontal { height:4px; background:#263348; border-radius:2px; }
QSlider::sub-page:horizontal { background:$blue; border-radius:2px; }
QSlider::handle:horizontal { background:$blue; border:2px solid #9bc3ff; width:10px; height:10px; margin:-5px 0; border-radius:7px; }
QSlider::handle:horizontal:hover { background:$blue_hover; border-color:#d1e3ff; }
QProgressBar { border:none; background:$blue_surface; height:4px; border-radius:2px; }
QProgressBar::chunk { background:$blue; border-radius:2px; }
QScrollBar:vertical { background:transparent; width:9px; margin:4px 1px; }
QScrollBar::handle:vertical { background:#2a3c59; border-radius:4px; min-height:30px; }
QScrollBar::handle:vertical:hover { background:$blue; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background:transparent; }
QMenu { background:$surface; border:1px solid $border; border-radius:8px; padding:6px; }
QMenu::item { padding:8px 18px; border-radius:5px; }
QMenu::item:selected { background:$blue_surface; color:$text; }
QMenu::separator { height:1px; background:$border; margin:5px; }
QToolTip { background:$raised; color:$text; border:1px solid $border; padding:7px; }
""").substitute(COLORS)


def apply_theme(app):
    palette = QPalette()
    for role, color in {
        QPalette.ColorRole.Window: "background",
        QPalette.ColorRole.WindowText: "text",
        QPalette.ColorRole.Base: "background",
        QPalette.ColorRole.AlternateBase: "surface",
        QPalette.ColorRole.Text: "text",
        QPalette.ColorRole.Button: "raised",
        QPalette.ColorRole.ButtonText: "text",
        QPalette.ColorRole.Highlight: "blue",
        QPalette.ColorRole.HighlightedText: "text",
        QPalette.ColorRole.PlaceholderText: "muted",
        QPalette.ColorRole.ToolTipBase: "raised",
        QPalette.ColorRole.ToolTipText: "text",
    }.items():
        palette.setColor(role, QColor(COLORS[color]))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(COLORS["disabled"]))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(COLORS["disabled"]))
    app.setPalette(palette)
    app.setStyleSheet(STYLES)
