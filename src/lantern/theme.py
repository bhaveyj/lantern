"""Black and blue visual theme for the Windows sender."""
from string import Template

from PySide6.QtGui import QColor, QPalette

COLORS = {
    "background": "#0a0d13",
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
QLabel#brand { font-size:22px; font-weight:500; }
QLabel#queueHeading { font-size:20px; font-weight:500; }
QLabel#panelHeading { font-size:15px; font-weight:500; }
QLabel#detailsTitle { font-size:20px; font-weight:400; }
QLabel#fileWarning { color:#e3b77a; font-size:11px; }
QLabel#edition { color:$blue; background:$blue_surface; border:1px solid $border; border-radius:7px; padding:5px 10px; font-size:11px; font-weight:650; }
QLabel#hero { font-size:30px; font-weight:650; }
QLabel#muted { color:$muted; }
QLabel#section { color:$muted; font-size:11px; font-weight:650; }
QLabel#playerHeading { color:$blue; font-size:11px; font-weight:650; }
QLabel#now { font-size:12px; font-weight:400; }
QLabel#status { color:$muted; font-size:11px; }
QLabel#time { color:$muted; font-size:11px; }
QWidget#card { background:$surface; border:1px solid $border; border-radius:14px; }
QWidget#controlPanel { background:#121b29; border:1px solid #344154; border-radius:9px; }
QWidget#playbackDock { background:transparent; border:none; border-top:1px solid $border; border-radius:0; }
QWidget#nowDetails { background:transparent; }
QPlainTextEdit#originalFilename { background:$background; border:1px solid $border; border-radius:6px; padding:9px; color:#c6d4e6; font-family:'Consolas'; font-size:12px; selection-background-color:$blue_surface; }
QWidget#emptyState { background:transparent; border:none; }
QLabel#emptyIcon { color:$muted; background:transparent; border:none; font-size:27px; font-weight:400; }
QLabel#emptyTitle { font-size:18px; font-weight:400; }
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
QPushButton#smallQuiet { background:transparent; border:1px solid transparent; color:#a2bce2; padding:6px 7px; font-size:11px; font-weight:500; }
QPushButton#smallQuiet:hover { background:$raised; border-color:$border; }
QPushButton#smallQuiet:disabled { color:$disabled; }
QPushButton#headerAction, QPushButton#deviceButton { background:transparent; border:1px solid transparent; color:#bdcbe0; padding:8px 10px; font-size:12px; font-weight:400; }
QPushButton#headerAction:hover, QPushButton#deviceButton:hover, QPushButton#deviceButton:checked { background:$raised; border-color:transparent; }
QPushButton#playCircle { background:$blue; color:#ffffff; border:1px solid $blue; border-radius:19px; padding:0; }
QPushButton#playCircle:hover { background:$blue_hover; }
QPushButton#transport { background:transparent; border:1px solid transparent; color:#a2b8d9; padding:6px; min-width:0; font-size:12px; font-weight:400; }
QPushButton#transport:hover, QPushButton#transport:checked { background:$raised; border-color:transparent; }
QPushButton#transport:disabled { background:transparent; color:$disabled; border-color:transparent; }
QPushButton#transport::menu-indicator { image:none; width:0; }
QComboBox, QLineEdit, QSpinBox { background:$background; border:1px solid $border; border-radius:8px; padding:9px 12px; min-height:20px; selection-background-color:$blue; }
QComboBox:focus, QLineEdit:focus, QSpinBox:focus { border-color:$blue; }
QComboBox:hover, QLineEdit:hover, QSpinBox:hover { border-color:#3a5276; }
QComboBox QAbstractItemView { background:$raised; color:$text; border:1px solid $border; selection-background-color:$blue_surface; selection-color:$text; padding:6px; outline:none; }
QListWidget { background:#090d14; border:1px solid $border; border-radius:12px; padding:6px; outline:none; }
QListWidget:focus { border-color:#315287; }
QListWidget#playlist { background:transparent; border:none; padding:0; }
QListWidget#playlist:focus { border:none; }
QSlider { background:transparent; border:none; min-height:20px; }
QSlider::groove:horizontal { height:4px; background:#263348; border-radius:2px; }
QSlider::sub-page:horizontal { background:$blue; border-radius:2px; }
QSlider::handle:horizontal { background:$blue; border:none; width:8px; height:8px; margin:-2px 0; border-radius:4px; }
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
