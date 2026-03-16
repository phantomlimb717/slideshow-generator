from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar,
    QPushButton, QComboBox, QSlider, QDialogButtonBox, QWidget
)
from PySide6.QtCore import Qt

class ExportSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export Settings")
        self.setFixedSize(400, 250)

        layout = QVBoxLayout(self)

        # Resolution Preset
        res_layout = QHBoxLayout()
        res_layout.addWidget(QLabel("Resolution:"))
        self.combo_res = QComboBox()
        self.combo_res.addItems(["1280x720 (720p)", "1920x1080 (1080p)", "3840x2160 (4K)"])
        self.combo_res.setCurrentIndex(1) # default 1080p
        res_layout.addWidget(self.combo_res)
        layout.addLayout(res_layout)

        # Framerate
        fps_layout = QHBoxLayout()
        fps_layout.addWidget(QLabel("Framerate:"))
        self.combo_fps = QComboBox()
        self.combo_fps.addItems(["24", "30", "60"])
        self.combo_fps.setCurrentIndex(1) # default 30
        fps_layout.addWidget(self.combo_fps)
        layout.addLayout(fps_layout)

        # Quality
        qual_layout = QHBoxLayout()
        qual_layout.addWidget(QLabel("Quality (CRF):"))
        self.slider_qual = QSlider(Qt.Horizontal)
        self.slider_qual.setRange(18, 32)  # lower is better quality for x264
        self.slider_qual.setValue(23)
        self.slider_qual.setInvertedAppearance(True) # Invert so sliding right means higher quality (lower CRF)
        qual_layout.addWidget(self.slider_qual)
        layout.addLayout(qual_layout)

        # Buttons
        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

    def get_settings(self) -> dict:
        res_str = self.combo_res.currentText()
        if "720p" in res_str:
            res = (1280, 720)
        elif "4K" in res_str:
            res = (3840, 2160)
        else:
            res = (1920, 1080)

        return {
            "resolution": res,
            "fps": int(self.combo_fps.currentText()),
            "quality": self.slider_qual.value()
        }

import time

class ExportProgressDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Exporting...")
        self.setFixedSize(400, 180)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowCloseButtonHint)

        layout = QVBoxLayout(self)

        self.label = QLabel("Exporting slideshow. Please wait...", self)
        layout.addWidget(self.label)

        self.progress_bar = QProgressBar(self)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        self.time_label = QLabel("Estimated time remaining: Calculating...", self)
        layout.addWidget(self.time_label)

        self.btn_cancel = QPushButton("Cancel", self)
        layout.addWidget(self.btn_cancel, alignment=Qt.AlignCenter)

        self.start_time = time.time()

    def set_progress(self, value: int):
        self.progress_bar.setValue(value)
        if value > 0 and value < 100:
            elapsed = time.time() - self.start_time
            total_est = elapsed / (value / 100.0)
            remaining = total_est - elapsed

            mins = int(remaining // 60)
            secs = int(remaining % 60)

            if mins > 0:
                self.time_label.setText(f"Estimated time remaining: {mins}m {secs}s")
            else:
                self.time_label.setText(f"Estimated time remaining: {secs}s")
        elif value == 100:
            self.time_label.setText("Finishing up...")

    def set_text(self, text: str):
        self.label.setText(text)
