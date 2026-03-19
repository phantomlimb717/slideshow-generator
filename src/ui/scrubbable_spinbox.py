from PySide6.QtWidgets import QSpinBox, QDoubleSpinBox, QApplication
from PySide6.QtCore import Qt, QPoint, QEvent
from PySide6.QtGui import QCursor

class ScrubbableMixin:
    """Mixin to add scrubbable behavior to spinboxes."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._is_scrubbing = False
        self._start_pos = QPoint()
        self._accumulated_delta = 0.0

        # We need fine tune adjustment,
        # so let's say every 10 pixels of drag = 1 singleStep
        self._pixels_per_step = 10

        # Install an event filter on the internal lineEdit so we can catch mouse events on the text field itself
        self.lineEdit().installEventFilter(self)
        self.lineEdit().setCursor(Qt.SizeHorCursor)

    def eventFilter(self, obj, event):
        if obj == self.lineEdit():
            if event.type() == QEvent.Wheel:
                # Wheel event on the lineEdit
                delta = event.angleDelta().y()
                if delta > 0:
                    self.stepBy(1)
                elif delta < 0:
                    self.stepBy(-1)
                return True

            elif event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self._is_scrubbing = True
                self._start_pos = QCursor.pos()
                self._accumulated_delta = 0.0
                QApplication.setOverrideCursor(Qt.BlankCursor)
                # Allow the press to pass through if they just wanted to click,
                # but we're starting to track just in case they drag
                return False

            elif event.type() == QEvent.MouseMove:
                if self._is_scrubbing:
                    current_pos = QCursor.pos()
                    dx = current_pos.x() - self._start_pos.x()

                    if dx != 0:
                        self._accumulated_delta += dx

                        steps = int(self._accumulated_delta / self._pixels_per_step)
                        if steps != 0:
                            self.stepBy(steps)
                            self._accumulated_delta -= steps * self._pixels_per_step

                        # Instantly teleport cursor back to the start position
                        # to prevent hitting the edge of the screen
                        QCursor.setPos(self._start_pos)

                    # Consume the event so it doesn't select text while we're scrubbing
                    return True

            elif event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
                if self._is_scrubbing:
                    self._is_scrubbing = False
                    QApplication.restoreOverrideCursor()
                    # Allow it to pass through so the click registers normally if they didn't drag
                    return False

        return super().eventFilter(obj, event)

    def wheelEvent(self, event):
        # Override wheelEvent on the spinbox itself to catch scroll-on-hover
        # that doesn't target the lineEdit
        delta = event.angleDelta().y()
        if delta > 0:
            self.stepBy(1)
        elif delta < 0:
            self.stepBy(-1)
        event.accept()

class ScrubbableSpinBox(ScrubbableMixin, QSpinBox):
    def __init__(self, parent=None):
        super().__init__(parent)

class ScrubbableDoubleSpinBox(ScrubbableMixin, QDoubleSpinBox):
    def __init__(self, parent=None):
        super().__init__(parent)
