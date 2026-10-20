"""Export the native Windows icon from the sender's vector drawing."""
import os
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication
from lantern.app import icon

app = QApplication([])
destination = Path(__file__).resolve().parents[1] / "assets" / "lantern-windows.ico"
destination.parent.mkdir(exist_ok=True)
if not icon().pixmap(256, 256).save(str(destination), "ICO"):
    raise RuntimeError("Could not export the Windows app icon")
print(destination)
