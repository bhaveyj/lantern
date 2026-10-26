from __future__ import annotations

import os

from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QLabel, QLineEdit, QVBoxLayout

from .cloud_subtitles import estimated_cost


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
        self.duration = 0.0
        self.key_loaded = False
        layout = QVBoxLayout(self)
        self.message = QLabel("Checking this file for embedded subtitles…")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.form = QFormLayout()
        self.mode = QComboBox()
        self.mode.addItem("Use embedded subtitles", "embedded")
        self.mode.addItem("Generate locally · free", "local")
        self.mode.addItem("Generate with Deepgram · paid cloud", "cloud")
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
        self.api_key = QLineEdit()
        self.api_key.setMaxLength(1024)
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("Your Deepgram API key")
        self.form.addRow("Cloud API key", self.api_key)
        self.remember_key = QCheckBox("Remember this key with Windows user encryption")
        self.remember_key.setEnabled(os.name == "nt")
        self.form.addRow(self.remember_key)
        self.cloud_privacy = QCheckBox("Exclude this upload from model training and retention after processing")
        self.cloud_privacy.setChecked(True)
        self.form.addRow(self.cloud_privacy)
        self.cloud_budget = QDoubleSpinBox()
        self.cloud_budget.setRange(.01, 100)
        self.cloud_budget.setPrefix("US$ ")
        self.cloud_budget.setValue(1.0)
        self.form.addRow("Maximum estimated cost", self.cloud_budget)
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
        self.language.currentIndexChanged.connect(self.mode_changed)
        self.api_key.textChanged.connect(self.update_enabled)
        self.cloud_privacy.toggled.connect(self.mode_changed)
        self.cloud_budget.valueChanged.connect(self.update_enabled)
        self.finished.connect(lambda _: self.api_key.clear())
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
        self.duration = data["inspection"]["duration"]
        self.mode.model().item(0).setEnabled(bool(usable))
        self.mode.setCurrentIndex(0 if usable else 1)
        self.message.setText("This file already has text subtitles. Extract a track, or generate new captions from its audio." if usable else "No embedded text subtitles were found. Generate captions from the audio instead.")
        self.mode_changed()

    def mode_changed(self):
        embedded = self.mode.currentData() == "embedded"
        cloud = self.mode.currentData() == "cloud"
        self.form.setRowVisible(self.embedded, embedded)
        for widget in (self.audio, self.language):
            self.form.setRowVisible(widget, not embedded)
        for widget in (self.model, self.cpu, self.download):
            self.form.setRowVisible(widget, not embedded and not cloud)
        for widget in (self.api_key, self.remember_key, self.cloud_privacy, self.cloud_budget):
            self.form.setRowVisible(widget, cloud)
        self.explanation.setVisible(not embedded)
        if cloud:
            if not self.key_loaded:
                self.key_loaded = True
                try:
                    saved = self.window.cloud_credentials.load()
                    self.api_key.setText(saved)
                    self.remember_key.setChecked(bool(saved))
                except ValueError as error:
                    self.message.setText(str(error))
            cost = estimated_cost(self.duration, self.language.currentData())
            amount = "less than US$0.01" if 0 < cost < .01 else f"US${cost:.2f}"
            estimate = f"Estimated transcription cost: {amount}." if self.duration > 0 else "Duration unavailable; use local generation for this file."
            privacy = "Model training is excluded; Deepgram retains audio/transcripts only as needed to process this request." if self.cloud_privacy.isChecked() else "You allow Deepgram to retain the uploaded audio and transcript for model improvement."
            self.explanation.setText(f"Only the selected audio will be uploaded to Deepgram. {privacy} {estimate} Published Pay As You Go rates checked 26 Oct 2026 apply with or without training opt-out; your contract, taxes, and charges may differ. The limit applies to our estimate, not provider billing. Cancellation cannot undo charges. Cached subtitles are reused; this choice does not change retention of past uploads.")
        else:
            self.explanation.setText("Audio stays on this PC. Models download once (hundreds of MB to about 1.5 GB). Captions use the spoken language; translation is separate. Eco reduces CPU use by taking longer.")
        self.create.setText("Extract subtitles" if embedded else "Upload audio & generate" if cloud else "Generate subtitles")
        self.update_enabled()

    def update_enabled(self):
        mode = self.mode.currentData()
        available = self.has_embedded if mode == "embedded" else self.has_audio
        if mode == "cloud":
            available = available and bool(self.api_key.text().strip()) and self.duration > 0 and estimated_cost(self.duration, self.language.currentData()) <= self.cloud_budget.value()
        self.create.setEnabled(self.ready and available)

    def inspection_failed(self, message):
        self.message.setText(message)

    def start(self):
        self.create.setEnabled(False)
        if self.mode.currentData() == "embedded":
            self.window.worker.submit("subtitle_extract", self.media_id, self.source, self.embedded.currentData())
        elif self.mode.currentData() == "local":
            options = {"audio_index": self.audio.currentData(), "language": self.language.currentData(), "model": self.model.currentData(), "threads": self.cpu.currentData(), "allow_download": self.download.isChecked()}
            self.window.worker.submit("subtitle_local", self.media_id, self.source, options)
        else:
            api_key = self.api_key.text().strip()
            try:
                if self.remember_key.isChecked():
                    self.window.cloud_credentials.save(api_key)
                else:
                    self.window.cloud_credentials.forget()
            except (ValueError, OSError) as error:
                self.message.setText(str(error))
                self.update_enabled()
                return
            self.window.worker.submit("subtitle_cloud", self.media_id, self.source, {"audio_index": self.audio.currentData(), "language": self.language.currentData(), "api_key": api_key, "approved_duration": self.duration, "mip_opt_out": self.cloud_privacy.isChecked(), "max_estimated_cost": self.cloud_budget.value()})
        self.accept()
