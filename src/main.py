import sys
import shutil
import platform
import os
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtGui import QIcon
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

def main():
    check_dependencies()

    # On Windows, setting an explicit AppUserModelID ensures the taskbar
    # icon displays correctly when running from a Python script/interpreter
    # rather than just displaying the default python.exe icon.
    if platform.system() == "Windows":
        try:
            import ctypes
            myappid = 'fms.generator.app.1.0'
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
        except Exception as e:
            print(f"Warning: Could not set AppUserModelID: {e}")

    app = QApplication(sys.argv)

    # Set application-level icon
    base_dir = os.path.dirname(os.path.abspath(__file__))
    system = platform.system()
    if system == "Windows":
        icon_path = os.path.join(base_dir, "picture_photo_image_icon_131252.ico")
    elif system == "Darwin":
        icon_path = os.path.join(base_dir, "picture_photo_image_icon_131252.icns")
    else:
        # Linux and fallback
        icon_path = os.path.join(base_dir, "picture_photo_image_icon_131252.png")

    app.setWindowIcon(QIcon(icon_path))

    from ui.main_window import MainWindow
    window = MainWindow()
    window.show()

    sys.exit(app.exec())

if __name__ == "__main__":
    main()
