from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QVBoxLayout


class SubtitleDialog(QDialog):
    def __init__(self, window, media):
        super().__init__(window)
        self.window = window
        self.media_id, self.source = media.id, media.path
        self.setWindowTitle("Create subtitles")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        self.message = QLabel("Checking this file for embedded subtitles…")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        form = QFormLayout()
        self.embedded = QComboBox()
        self.embedded.setEnabled(False)
        form.addRow("Embedded subtitles", self.embedded)
        layout.addLayout(form)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.create = self.buttons.addButton("Extract subtitles", QDialogButtonBox.ButtonRole.AcceptRole)
        self.create.setEnabled(False)
        self.create.clicked.connect(self.start)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def inspected(self, data):
        self.embedded.clear()
        usable = 0
        for track in data["inspection"]["subtitles"]:
            suffix = "" if track["text"] else " — image subtitles"
            self.embedded.addItem(track["label"] + suffix, track["index"])
            row = self.embedded.count() - 1
            if not track["text"]:
                self.embedded.model().item(row).setEnabled(False)
            else:
                if usable == 0:
                    self.embedded.setCurrentIndex(row)
                usable += 1
        self.embedded.setEnabled(bool(usable))
        self.create.setEnabled(bool(usable))
        self.message.setText("This file already has subtitles. Extract a text track without changing the video." if usable else "No embedded text subtitles were found in this file.")

    def inspection_failed(self, message):
        self.message.setText(message)

    def start(self):
        self.window.worker.submit("subtitle_extract", self.media_id, self.source, self.embedded.currentData())
        self.accept()
