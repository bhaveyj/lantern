from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QVBoxLayout


class SubtitleDialog(QDialog):
    def __init__(self, window, media):
        super().__init__(window)
        self.window = window
        self.media_id, self.source = media.id, media.path
        self.setWindowTitle("Create subtitles")
        self.setMinimumWidth(620)
        self.ready = False
        self.has_audio = False
        self.has_embedded = False
        layout = QVBoxLayout(self)
        self.message = QLabel("Checking this file for embedded subtitles…")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.form = QFormLayout()
        self.mode = QComboBox()
        self.mode.addItem("Use embedded subtitles", "embedded")
        self.mode.addItem("Generate locally · free", "local")
        self.mode.setCurrentIndex(1)
        self.form.addRow("Method", self.mode)
        self.embedded = QComboBox()
        self.embedded.setEnabled(False)
        self.form.addRow("Subtitle track", self.embedded)
        self.audio = QComboBox()
        self.form.addRow("Audio track", self.audio)
        self.language = QComboBox()
        for text, value in (("Auto · original language", None), ("English", "en"), ("Hindi", "hi")):
            self.language.addItem(text, value)
        self.form.addRow("Spoken language", self.language)
        self.model = QComboBox()
        for text, value in (("Base · lighter, less accurate", "base"), ("Small · balanced", "small"), ("Medium · slower, higher quality", "medium")):
            self.model.addItem(text, value)
        self.model.setCurrentIndex(1)
        self.form.addRow("Speech model", self.model)
        self.cpu = QComboBox()
        self.cpu.addItem("Eco · 2 inference threads", 2)
        self.cpu.addItem("Balanced · 4 inference threads", 4)
        self.form.addRow("CPU mode", self.cpu)
        self.download = QCheckBox("Allow the free model download on first use")
        self.download.setChecked(True)
        self.form.addRow(self.download)
        self.explanation = QLabel("Audio stays on this PC. Models download once (hundreds of MB to about 1.5 GB). Captions use the spoken language; translation is separate. Eco reduces CPU use by taking longer.")
        self.explanation.setWordWrap(True)
        layout.addLayout(self.form)
        layout.addWidget(self.explanation)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.create = self.buttons.addButton("Extract subtitles", QDialogButtonBox.ButtonRole.AcceptRole)
        self.create.setEnabled(False)
        self.create.clicked.connect(self.start)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.mode.currentIndexChanged.connect(self.mode_changed)
        self.mode_changed()

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
        self.audio.clear()
        for track in data["inspection"]["audio"]:
            self.audio.addItem(f"Track {track['index']} · {track['label']}", track["index"])
        self.ready, self.has_audio, self.has_embedded = True, bool(self.audio.count()), bool(usable)
        self.mode.model().item(0).setEnabled(bool(usable))
        self.mode.setCurrentIndex(0 if usable else 1)
        self.message.setText("This file already has text subtitles. Extract a track, or generate new captions from its audio." if usable else "No embedded text subtitles were found. Generate captions from the audio instead.")
        self.mode_changed()

    def mode_changed(self):
        embedded = self.mode.currentData() == "embedded"
        self.form.setRowVisible(self.embedded, embedded)
        for widget in (self.audio, self.language, self.model, self.cpu, self.download):
            self.form.setRowVisible(widget, not embedded)
        self.explanation.setVisible(not embedded)
        self.create.setText("Extract subtitles" if embedded else "Generate subtitles")
        self.create.setEnabled(self.ready and (self.has_embedded if embedded else self.has_audio))

    def inspection_failed(self, message):
        self.message.setText(message)

    def start(self):
        self.create.setEnabled(False)
        if self.mode.currentData() == "embedded":
            self.window.worker.submit("subtitle_extract", self.media_id, self.source, self.embedded.currentData())
        else:
            options = {"audio_index": self.audio.currentData(), "language": self.language.currentData(), "model": self.model.currentData(), "threads": self.cpu.currentData(), "allow_download": self.download.isChecked()}
            self.window.worker.submit("subtitle_local", self.media_id, self.source, options)
        self.accept()
