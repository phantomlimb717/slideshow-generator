import sys
import shutil
from PySide6.QtWidgets import QApplication, QMessageBox
import pillow_heif

# Register HEIF before doing anything else
pillow_heif.register_heif_opener()

def check_dependencies():
    """Check if ffmpeg and ffprobe are available in the system PATH."""
    missing = []
    if shutil.which("ffmpeg") is None:
        missing.append("ffmpeg")
    if shutil.which("ffprobe") is None:
        missing.append("ffprobe")

    if missing:
        app = QApplication(sys.argv)
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Critical)
        msg.setWindowTitle("Missing Dependencies")
        msg.setText(f"The following required tools were not found in your system PATH:\n\n"
                    f"{', '.join(missing)}\n\n"
                    f"Please install FFmpeg and make sure it is in your system PATH to use this application.")
        msg.exec()
        sys.exit(1)

if __name__ == "__main__":
    check_dependencies()

    app = QApplication(sys.argv)

    from ui.main_window import MainWindow
    window = MainWindow()
    window.show()

    sys.exit(app.exec())
