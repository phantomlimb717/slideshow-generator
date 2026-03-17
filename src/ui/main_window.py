import sys
import os
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QToolBar, QSplitter, QLabel, QPushButton, QSlider, QComboBox,
    QSpinBox, QDoubleSpinBox, QCheckBox, QListWidget, QListWidgetItem,
    QFrame, QFileDialog, QMessageBox, QProgressDialog, QSizePolicy,
    QStyledItemDelegate, QStyle
)
from PySide6.QtCore import Qt, QSize, QUrl, QTimer, Signal, QThread, QEvent, QRect
from PySide6.QtGui import QIcon, QAction, QPixmap, QImage, QPainter, QColor, QPalette
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from PIL import ImageQt

from models.project import Project, SlideItem, MediaType, EffectPreset, AudioItem


class AspectRatioContainer(QWidget):
    """A container widget that constrains its child to a 16:9 aspect ratio."""
    def __init__(self, child_widget, parent=None):
        super().__init__(parent)
        self.child_widget = child_widget
        self.child_widget.setParent(self)
        self.setStyleSheet("background-color: #1e1e1e;")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition_child()

    def _reposition_child(self):
        w = self.width()
        h = self.height()
        target_ratio = 16 / 9

        # Calculate largest 16:9 rect that fits
        if w / max(1, h) > target_ratio:
            # Container is wider than 16:9 — constrain by height
            child_h = h
            child_w = int(h * target_ratio)
        else:
            # Container is taller than 16:9 — constrain by width
            child_w = w
            child_h = int(w / target_ratio)

        # Center the child
        x = (w - child_w) // 2
        y = (h - child_h) // 2
        self.child_widget.setGeometry(x, y, child_w, child_h)

from utils.media_import import scan_directory_for_media, extract_thumbnail
from rendering.preview import PreviewGenerator
from export.exporter import Exporter
from ui.export_dialog import ExportProgressDialog, ExportSettingsDialog
from models.serialization import save_project, load_project

class ThumbnailWorker(QThread):
    thumbnail_ready = Signal(int, QImage, str) # index, qimage, target_list ('media' or 'timeline')

    def __init__(self, items: list[tuple[int, str, tuple[int, int], str]], parent=None):
        super().__init__(parent)
        # items is a list of tuples: (index, media_path, size, target_list)
        self.items = items
        self._cancel = False

    def run(self):
        for index, path, size, target_list in self.items:
            if self._cancel:
                break
            try:
                thumb = extract_thumbnail(path, size=size)
                if thumb and not self._cancel:
                    qimg = ImageQt.ImageQt(thumb.convert("RGBA"))
                    self.thumbnail_ready.emit(index, QImage(qimg), target_list)
            except Exception as e:
                print(f"Error generating thumbnail for {path}: {e}")

    def cancel(self):
        self._cancel = True

class TimelineItemDelegate(QStyledItemDelegate):
    """Custom delegate that renders timeline items as thumbnail cards with text below."""

    ITEM_WIDTH = 160
    ITEM_HEIGHT = 120
    ICON_WIDTH = 140
    ICON_HEIGHT = 80
    TEXT_HEIGHT = 30
    PADDING = 4

    def paint(self, painter, option, index):
        painter.save()

        # Draw selection/hover background
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, QColor("#007acc"))
        elif option.state & QStyle.State_MouseOver:
            painter.fillRect(option.rect, QColor("#3f3f46"))

        rect = option.rect

        # Calculate icon position (centered horizontally, at the top)
        icon = index.data(Qt.DecorationRole)
        if icon:
            icon_x = rect.x() + (rect.width() - self.ICON_WIDTH) // 2
            icon_y = rect.y() + self.PADDING
            icon_rect = QRect(icon_x, icon_y, self.ICON_WIDTH, self.ICON_HEIGHT)
            icon.paint(painter, icon_rect)

        # Draw text below the icon (centered)
        text = index.data(Qt.DisplayRole)
        if text:
            text_rect = QRect(
                rect.x() + self.PADDING,
                rect.y() + self.PADDING + self.ICON_HEIGHT + 2,
                rect.width() - 2 * self.PADDING,
                self.TEXT_HEIGHT
            )
            painter.setPen(QColor("#cccccc"))
            painter.drawText(text_rect, Qt.AlignHCenter | Qt.AlignTop, text)

        painter.restore()

    def sizeHint(self, option, index):
        return QSize(self.ITEM_WIDTH, self.ITEM_HEIGHT)

class ListWidgetDraggable(QListWidget):
    itemsDropped = Signal()

    SCROLL_MARGIN = 40       # pixels from edge to trigger scroll
    SCROLL_SPEED = 8         # pixels per event

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QListWidget.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)

    def dragMoveEvent(self, event):
        pos = event.position().toPoint()

        # Auto-scroll when dragging near the edges
        if pos.x() < self.SCROLL_MARGIN:
            # Near left edge — scroll left
            scrollbar = self.horizontalScrollBar()
            scrollbar.setValue(scrollbar.value() - self.SCROLL_SPEED)
        elif pos.x() > self.viewport().width() - self.SCROLL_MARGIN:
            # Near right edge — scroll right
            scrollbar = self.horizontalScrollBar()
            scrollbar.setValue(scrollbar.value() + self.SCROLL_SPEED)

        super().dragMoveEvent(event)

    def dropEvent(self, event):
        super().dropEvent(event)
        self.itemsDropped.emit()

class MediaLibraryWidget(QListWidget):
    filesDropped = Signal(list)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setSelectionMode(QListWidget.ExtendedSelection)
        self.setIconSize(QSize(160, 90))
        self.setGridSize(QSize(170, 120))
        self.setViewMode(QListWidget.IconMode)
        self.setResizeMode(QListWidget.Adjust)
        self.setUniformItemSizes(True)
        self.setHorizontalScrollMode(QListWidget.ScrollPerPixel)
        self.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        # Enable dragging OUT to timeline
        self.setDragEnabled(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            paths = [u.toLocalFile() for u in urls if u.isLocalFile()]
            if paths:
                self.filesDropped.emit(paths)
            event.accept()
        else:
            super().dropEvent(event)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.base_title = "Family Memories Slideshow Generator"
        self.setWindowTitle(self.base_title)
        self.setMinimumSize(1200, 800)

        self.project = Project()
        self.media_library: list[SlideItem] = []
        self._is_dirty = False
        self.current_project_path = None

        self.preview_generator = None
        self.thumbnail_worker = None
        self._old_thumbnail_workers = []

        self.setup_ui()
        self.apply_dark_theme()

        # Initial state setup
        self.statusBar().showMessage("Ready")
        self.update_inspector_state(None)

    @property
    def is_dirty(self):
        return self._is_dirty

    @is_dirty.setter
    def is_dirty(self, value):
        self._is_dirty = value
        if self._is_dirty:
            self.setWindowTitle(self.base_title + " *")
        else:
            self.setWindowTitle(self.base_title)

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.setup_toolbar()

        main_splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(main_splitter)

        # --- Left Panel: Media Library ---
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(4, 4, 4, 4)
        left_layout.setSpacing(4)
        left_layout.addWidget(QLabel("Media Library (Drag & Drop)"))

        self.media_list = MediaLibraryWidget()
        self.media_list.setIconSize(QSize(160, 90))
        self.media_list.filesDropped.connect(self.handle_files_dropped)
        left_layout.addWidget(self.media_list)

        btn_add_folder = QPushButton("Add Folder...")
        btn_add_folder.clicked.connect(self.add_media_dialog)
        left_layout.addWidget(btn_add_folder)

        btn_add_files = QPushButton("Add Files...")
        btn_add_files.clicked.connect(self.add_files_dialog)
        left_layout.addWidget(btn_add_files)

        main_splitter.addWidget(left_panel)

        # --- Center Panel: Preview & Timeline ---
        center_panel = QWidget()
        center_layout = QVBoxLayout(center_panel)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)

        center_splitter = QSplitter(Qt.Vertical)
        center_layout.addWidget(center_splitter)

        # --- Top: Preview Area ---
        preview_widget = QWidget()
        preview_layout = QVBoxLayout(preview_widget)
        preview_layout.setContentsMargins(4, 4, 4, 0)
        preview_layout.setSpacing(2)

        self.video_widget = QVideoWidget()
        self.video_widget.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)
        self.video_container = AspectRatioContainer(self.video_widget)
        preview_layout.addWidget(self.video_container)

        self.audio_output = QAudioOutput()
        self.media_player = QMediaPlayer()
        self.media_player.setVideoOutput(self.video_widget)
        self.media_player.setAudioOutput(self.audio_output)

        self.media_player.positionChanged.connect(self.update_scrubber)
        self.media_player.durationChanged.connect(self.update_duration)
        self.media_player.mediaStatusChanged.connect(self.handle_media_status)
        self.media_player.errorOccurred.connect(self.on_media_error)

        # Controls
        controls_layout = QHBoxLayout()
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(4)
        self.btn_play_pause = QPushButton("Play")
        self.btn_play_pause.clicked.connect(self.toggle_play_pause)
        self.btn_play_pause.setEnabled(False)
        controls_layout.addWidget(self.btn_play_pause)

        self.scrubber = QSlider(Qt.Horizontal)
        self.scrubber.sliderMoved.connect(self.set_position)
        self.scrubber.setEnabled(False)
        controls_layout.addWidget(self.scrubber)

        self.time_label = QLabel("00:00 / 00:00")
        controls_layout.addWidget(self.time_label)

        preview_layout.addLayout(controls_layout)

        # Generation overlay
        self.gen_overlay = QWidget(preview_widget)
        self.gen_overlay.setStyleSheet("background-color: rgba(30, 30, 30, 220); border-radius: 5px;")
        gen_layout = QVBoxLayout(self.gen_overlay)
        gen_layout.setAlignment(Qt.AlignCenter)

        self.gen_label = QLabel("Generating preview...")
        self.gen_label.setStyleSheet("color: white; padding: 10px; font-weight: bold; background-color: transparent;")
        self.gen_label.setAlignment(Qt.AlignCenter)
        gen_layout.addWidget(self.gen_label)

        self.btn_cancel_preview = QPushButton("Cancel")
        self.btn_cancel_preview.setStyleSheet("QPushButton { background-color: #3f3f46; color: white; border: 1px solid #555555; padding: 5px 15px; border-radius: 3px; } QPushButton:hover { background-color: #4f4f56; } QPushButton:pressed { background-color: #007acc; }")
        self.btn_cancel_preview.clicked.connect(self.cancel_preview_generation)
        gen_layout.addWidget(self.btn_cancel_preview, alignment=Qt.AlignCenter)

        self.gen_overlay.hide()

        # Install event filter to keep overlay positioned and sized correctly
        preview_widget.installEventFilter(self)

        center_splitter.addWidget(preview_widget)

        # --- Bottom: Timeline + Audio ---
        timeline_widget = QWidget()
        timeline_layout = QVBoxLayout(timeline_widget)
        timeline_layout.setContentsMargins(4, 0, 4, 4)
        timeline_layout.setSpacing(2)

        # Timeline
        timeline_header = QHBoxLayout()
        timeline_header.addWidget(QLabel("Timeline (Slides)"))
        btn_add_to_timeline = QPushButton("Add Selected to Timeline")
        btn_add_to_timeline.clicked.connect(self.add_selected_to_timeline)
        timeline_header.addWidget(btn_add_to_timeline)

        timeline_header.addStretch()

        timeline_header.addWidget(QLabel("Global Crossfade (s):"))
        self.global_crossfade_spin = QDoubleSpinBox()
        self.global_crossfade_spin.setRange(0.0, 10.0)
        self.global_crossfade_spin.setSingleStep(0.5)
        self.global_crossfade_spin.setValue(1.0)
        self.global_crossfade_spin.valueChanged.connect(self.global_settings_changed)
        timeline_header.addWidget(self.global_crossfade_spin)

        timeline_layout.addLayout(timeline_header)

        self.timeline_list = ListWidgetDraggable()
        self.timeline_list.setFlow(QListWidget.LeftToRight)
        self.timeline_list.setWrapping(False)
        self.timeline_list.setFixedHeight(134)  # 124 grid height + ~10 for scrollbar
        self.timeline_list.setViewMode(QListWidget.ListMode)
        self.timeline_list.setDragDropMode(QListWidget.InternalMove)
        self.timeline_list.setDefaultDropAction(Qt.MoveAction)
        self.timeline_list.setIconSize(QSize(140, 80))
        self.timeline_list.setItemDelegate(TimelineItemDelegate(self.timeline_list))
        self.timeline_list.setGridSize(QSize(160, 124))
        self.timeline_list.setUniformItemSizes(True)
        self.timeline_list.setSelectionMode(QListWidget.SingleSelection)
        self.timeline_list.setHorizontalScrollMode(QListWidget.ScrollPerPixel)
        self.timeline_list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.timeline_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.timeline_list.itemsDropped.connect(self.sync_timeline_order)
        self.timeline_list.itemSelectionChanged.connect(self.on_timeline_selection)
        timeline_layout.addWidget(self.timeline_list)

        # Audio Track
        audio_header = QHBoxLayout()
        audio_header.addWidget(QLabel("Audio Tracks"))
        btn_add_audio = QPushButton("Add Audio...")
        btn_add_audio.clicked.connect(self.add_audio_dialog)
        audio_header.addWidget(btn_add_audio)
        audio_header.addStretch()
        audio_header.addWidget(QLabel("Global Volume:"))
        self.global_audio_slider = QSlider(Qt.Horizontal)
        self.global_audio_slider.setRange(0, 100)
        self.global_audio_slider.setValue(100)
        self.global_audio_slider.valueChanged.connect(self.global_settings_changed)
        audio_header.addWidget(self.global_audio_slider)
        self.global_audio_label = QLabel("100%")
        self.global_audio_slider.valueChanged.connect(lambda v: self.global_audio_label.setText(f"{v}%"))
        audio_header.addWidget(self.global_audio_label)
        timeline_layout.addLayout(audio_header)

        self.audio_list = ListWidgetDraggable()
        self.audio_list.setFlow(QListWidget.LeftToRight)
        self.audio_list.setWrapping(False)
        self.audio_list.setFixedHeight(60)
        self.audio_list.setViewMode(QListWidget.ListMode)
        self.audio_list.setDragDropMode(QListWidget.InternalMove)
        self.audio_list.setDefaultDropAction(Qt.MoveAction)
        self.audio_list.setSelectionMode(QListWidget.SingleSelection)
        self.audio_list.setHorizontalScrollMode(QListWidget.ScrollPerPixel)
        self.audio_list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.audio_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.audio_list.itemsDropped.connect(self.sync_audio_order)
        timeline_layout.addWidget(self.audio_list)

        center_splitter.addWidget(timeline_widget)

        # Set default proportions: ~70% preview, ~30% timeline
        center_splitter.setSizes([500, 200])

        main_splitter.addWidget(center_panel)

        # --- Right Panel: Inspector ---
        right_panel = QWidget()
        right_panel.setMinimumWidth(250)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(8, 8, 8, 8)
        right_layout.setSpacing(4)
        right_layout.addWidget(QLabel("Inspector"))

        slide_props_frame = QFrame()
        slide_props_frame.setFrameShape(QFrame.StyledPanel)
        slide_props_layout = QVBoxLayout(slide_props_frame)

        slide_props_layout.addWidget(self._create_section_header("Effect & Motion"))

        slide_props_layout.addWidget(QLabel("Effect Preset:"))
        self.effect_combo = QComboBox()
        self.effect_combo.addItems([e.value for e in EffectPreset])
        self.effect_combo.currentIndexChanged.connect(self.inspector_changed)
        slide_props_layout.addWidget(self.effect_combo)

        zoom_layout = QHBoxLayout()
        zoom_layout.addWidget(QLabel("Starting Zoom:"))
        self.zoom_spin = QDoubleSpinBox()
        self.zoom_spin.setRange(1.0, 3.0)
        self.zoom_spin.setSingleStep(0.1)
        self.zoom_spin.setValue(1.0)
        self.zoom_spin.valueChanged.connect(self.inspector_changed)
        zoom_layout.addWidget(self.zoom_spin)
        slide_props_layout.addLayout(zoom_layout)

        slide_props_layout.addWidget(QLabel("Focal Point (X, Y):"))
        focal_layout = QHBoxLayout()
        self.focal_x = QDoubleSpinBox()
        self.focal_x.setRange(0.0, 1.0)
        self.focal_x.setSingleStep(0.05)
        self.focal_x.setValue(0.5)
        self.focal_x.valueChanged.connect(self.inspector_changed)
        focal_layout.addWidget(self.focal_x)
        self.focal_y = QDoubleSpinBox()
        self.focal_y.setRange(0.0, 1.0)
        self.focal_y.setSingleStep(0.05)
        self.focal_y.setValue(0.5)
        self.focal_y.valueChanged.connect(self.inspector_changed)
        focal_layout.addWidget(self.focal_y)
        slide_props_layout.addLayout(focal_layout)

        slide_props_layout.addWidget(self._create_section_header("Timing"))

        dur_layout = QHBoxLayout()
        dur_layout.addWidget(QLabel("Duration (s):"))
        self.duration_spin = QDoubleSpinBox()
        self.duration_spin.setRange(1.0, 60.0)
        self.duration_spin.setSingleStep(0.5)
        self.duration_spin.setValue(5.0)
        self.duration_spin.valueChanged.connect(self.inspector_changed)
        dur_layout.addWidget(self.duration_spin)
        slide_props_layout.addLayout(dur_layout)

        # Crossfade Override
        self.cb_crossfade_global = QCheckBox("Use Global Default")
        self.cb_crossfade_global.setChecked(True)
        self.cb_crossfade_global.stateChanged.connect(self.inspector_changed)
        slide_props_layout.addWidget(self.cb_crossfade_global)

        crossfade_layout = QHBoxLayout()
        crossfade_layout.addWidget(QLabel("Crossfade override (s):"))
        self.crossfade_override = QDoubleSpinBox()
        self.crossfade_override.setRange(0.0, 10.0)
        self.crossfade_override.setSingleStep(0.5)
        self.crossfade_override.setValue(0.0)
        self.crossfade_override.valueChanged.connect(self.inspector_changed)
        crossfade_layout.addWidget(self.crossfade_override)
        slide_props_layout.addLayout(crossfade_layout)

        # Live Photo toggle
        self.lp_group = QWidget()
        lp_layout = QVBoxLayout(self.lp_group)
        lp_layout.setContentsMargins(0, 0, 0, 0)
        lp_layout.addWidget(self._create_section_header("Live Photo"))
        self.cb_use_video = QCheckBox("Use Video Clip (Live Photo)")
        self.cb_use_video.stateChanged.connect(self.inspector_changed)
        lp_layout.addWidget(self.cb_use_video)
        slide_props_layout.addWidget(self.lp_group)

        # Video Specific
        self.video_group = QWidget()
        video_layout = QVBoxLayout(self.video_group)
        video_layout.setContentsMargins(0, 0, 0, 0)
        video_layout.addWidget(self._create_section_header("Video Audio"))

        trim_layout = QHBoxLayout()
        trim_layout.addWidget(QLabel("Trim Start (s):"))
        self.trim_in_spin = QDoubleSpinBox()
        self.trim_in_spin.setRange(0.0, 3600.0)
        self.trim_in_spin.setSingleStep(0.5)
        self.trim_in_spin.setValue(0.0)
        self.trim_in_spin.valueChanged.connect(self.inspector_changed)
        trim_layout.addWidget(self.trim_in_spin)
        video_layout.addLayout(trim_layout)

        self.cb_include_audio = QCheckBox("Include original audio")
        self.cb_include_audio.stateChanged.connect(self.inspector_changed)
        video_layout.addWidget(self.cb_include_audio)

        vol_layout = QHBoxLayout()
        vol_layout.addWidget(QLabel("Volume:"))
        self.video_vol_slider = QSlider(Qt.Horizontal)
        self.video_vol_slider.setRange(0, 100)
        self.video_vol_slider.setValue(100)
        self.video_vol_slider.valueChanged.connect(self.inspector_changed)
        vol_layout.addWidget(self.video_vol_slider)
        video_layout.addLayout(vol_layout)
        slide_props_layout.addWidget(self.video_group)

        right_layout.addWidget(slide_props_frame)
        right_layout.addStretch()

        main_splitter.addWidget(right_panel)
        main_splitter.setSizes([200, 750, 250])

        self._updating_inspector = False # guard flag to prevent feedback loops

    def eventFilter(self, source, event):
        if hasattr(self, 'video_container') and source is self.video_container.parent():
            if event.type() == QEvent.Resize:
                # Reposition the overlay to cover the video widget area
                self.gen_overlay.setGeometry(self.video_container.geometry())
        return super().eventFilter(source, event)

    def _create_section_header(self, text):
        label = QLabel(text)
        label.setStyleSheet("color: #007acc; font-weight: bold; font-size: 11px; padding-top: 8px; border-bottom: 1px solid #3e3e42; padding-bottom: 4px;")
        return label

    def setup_toolbar(self):
        toolbar = QToolBar("Main Toolbar")
        self.addToolBar(toolbar)

        act_open = QAction("Open Project", self)
        act_open.triggered.connect(self.open_project_dialog)
        toolbar.addAction(act_open)

        act_save = QAction("Save Project", self)
        act_save.triggered.connect(self.save_project_dialog)
        toolbar.addAction(act_save)

        toolbar.addSeparator()

        act_add = QAction("Add Folder...", self)
        act_add.triggered.connect(self.add_media_dialog)
        toolbar.addAction(act_add)

        act_add_files = QAction("Add Files...", self)
        act_add_files.triggered.connect(self.add_files_dialog)
        toolbar.addAction(act_add_files)

        act_rm = QAction("Remove Slide/Audio", self)
        act_rm.triggered.connect(self.remove_selected)
        toolbar.addAction(act_rm)

        act_move_up = QAction("Move Up", self)
        act_move_up.triggered.connect(self.move_slide_up)
        toolbar.addAction(act_move_up)

        act_move_down = QAction("Move Down", self)
        act_move_down.triggered.connect(self.move_slide_down)
        toolbar.addAction(act_move_down)

        toolbar.addSeparator()

        act_prev = QAction("Generate Preview", self)
        act_prev.triggered.connect(self.trigger_preview_generation)
        toolbar.addAction(act_prev)

        act_exp = QAction("Export Slideshow", self)
        act_exp.triggered.connect(self.export_dialog)
        toolbar.addAction(act_exp)

    def apply_dark_theme(self):
        dark_qss = """
        QMainWindow { background-color: #1e1e1e; color: #d4d4d4; }
        QWidget { background-color: #1e1e1e; color: #d4d4d4; font-family: "Segoe UI", sans-serif; }
        QListWidget { background-color: #252526; border: 1px solid #3e3e42; }
        QListWidget::item { padding: 4px; }
        QListWidget::item:selected { background-color: #007acc; color: white; }
        QLabel { color: #cccccc; }
        QPushButton { background-color: #3f3f46; border: 1px solid #555555; padding: 5px 15px; border-radius: 3px; }
        QPushButton:hover { background-color: #4f4f56; }
        QPushButton:pressed { background-color: #007acc; }
        QSlider::groove:horizontal { border: 1px solid #3e3e42; height: 8px; background: #2d2d30; margin: 2px 0; }
        QSlider::handle:horizontal { background: #007acc; width: 14px; margin: -4px 0; border-radius: 7px; }
        QComboBox, QSpinBox, QDoubleSpinBox { background-color: #333337; border: 1px solid #3e3e42; color: white; padding: 2px; }
        QToolBar { background-color: #2d2d30; border-bottom: 1px solid #1e1e1e; }
        QSplitter::handle { background-color: #3e3e42; }
        """
        self.setStyleSheet(dark_qss)

    # --- Media Library Methods ---
    def add_media_dialog(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Folder to Import")
        if folder:
            self.handle_files_dropped([folder])

    def add_files_dialog(self):
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Media Files",
            "",
            "Media Files (*.jpg *.jpeg *.png *.tiff *.bmp *.webp *.heic *.heif *.mp4 *.mov *.avi *.mkv)"
        )
        if files:
            self.handle_files_dropped(files)

    def handle_files_dropped(self, paths):
        slides = []
        for path in paths:
            if os.path.isdir(path):
                slides.extend(scan_directory_for_media(path))
            else:
                pdir = os.path.dirname(path)
                ext = os.path.splitext(path)[1].lower()
                base_name = os.path.splitext(os.path.basename(path))[0]

                # Check for Live Photo pairs
                if ext in ['.heic', '.heif']:
                    mov_path = os.path.join(pdir, base_name + '.mov')
                    if not os.path.exists(mov_path):
                        mov_path = os.path.join(pdir, base_name + '.MOV')

                    if os.path.exists(mov_path):
                        s = SlideItem(media_path=path, media_type=MediaType.LIVE_PHOTO, video_path=mov_path)
                    else:
                        s = SlideItem(media_path=path, media_type=MediaType.IMAGE)
                elif ext in ['.mov', '.mp4']:
                    # It might be the video half of a live photo, check for image
                    heic_path = os.path.join(pdir, base_name + '.heic')
                    if not os.path.exists(heic_path):
                        heic_path = os.path.join(pdir, base_name + '.HEIC')

                    if os.path.exists(heic_path):
                        s = SlideItem(media_path=heic_path, media_type=MediaType.LIVE_PHOTO, video_path=path)
                    else:
                        s = SlideItem(media_path=path, media_type=MediaType.VIDEO)
                elif ext in ['.jpg', '.jpeg', '.png', '.tiff', '.bmp', '.webp']:
                    s = SlideItem(media_path=path, media_type=MediaType.IMAGE)
                else:
                    continue

                if not any((ls.media_path == s.media_path and ls.video_path == s.video_path) for ls in self.media_library) and not any((ls.media_path == s.media_path and ls.video_path == s.video_path) for ls in slides):
                    slides.append(s)

        self.media_library.extend(slides)
        self.refresh_media_list()
        self.statusBar().showMessage(f"Added {len(slides)} slides to library")

    def refresh_media_list(self):
        self.media_list.clear()

        # Stop existing worker if running
        if self.thumbnail_worker and self.thumbnail_worker.isRunning():
            self.thumbnail_worker.cancel()
            self._old_thumbnail_workers.append(self.thumbnail_worker)
            self.thumbnail_worker.finished.connect(
                lambda w=self.thumbnail_worker: self._cleanup_thumbnail_worker(w)
            )
            self.thumbnail_worker = None

        items_to_process = []
        for index, slide in enumerate(self.media_library):
            item = QListWidgetItem()

            # Create a solid gray placeholder pixmap matching #2d2d30
            placeholder = QPixmap(160, 90)
            placeholder.fill(QColor("#2d2d30"))
            item.setIcon(QIcon(placeholder))

            thumb_path = slide.media_path
            if slide.media_type == MediaType.LIVE_PHOTO and slide.use_video_clip and slide.video_path:
                thumb_path = slide.video_path

            items_to_process.append((index, thumb_path, (160, 90), 'media'))

            name = os.path.basename(slide.media_path)
            if slide.media_type == MediaType.LIVE_PHOTO:
                name = "[LIVE] " + name
            item.setText(name)
            item.setData(Qt.UserRole, slide)
            self.media_list.addItem(item)

        if items_to_process:
            self.thumbnail_worker = ThumbnailWorker(items_to_process, self)
            self.thumbnail_worker.thumbnail_ready.connect(self.on_thumbnail_ready)
            self.thumbnail_worker.start()

    def _cleanup_thumbnail_worker(self, worker):
        if worker in self._old_thumbnail_workers:
            self._old_thumbnail_workers.remove(worker)
        worker.deleteLater()

    def on_thumbnail_ready(self, index, qimage, target_list):
        pixmap = QPixmap.fromImage(qimage)
        if target_list == 'media':
            if index < self.media_list.count():
                item = self.media_list.item(index)
                item.setIcon(QIcon(pixmap))
        elif target_list == 'timeline':
            if index < self.timeline_list.count():
                item = self.timeline_list.item(index)
                item.setIcon(QIcon(pixmap))

    # --- Timeline & Audio Methods ---
    def add_selected_to_timeline(self):
        selected = self.media_list.selectedItems()
        for item in selected:
            slide_data = item.data(Qt.UserRole)
            # Create a true copy for the timeline so library isn't mutated
            import copy
            new_slide = copy.deepcopy(slide_data)
            self.project.slides.append(new_slide)

        self.refresh_timeline()
        self.is_dirty = True

    def refresh_timeline(self):
        self.timeline_list.clear()

        # Stop existing worker if running
        if self.thumbnail_worker and self.thumbnail_worker.isRunning():
            self.thumbnail_worker.cancel()
            self._old_thumbnail_workers.append(self.thumbnail_worker)
            self.thumbnail_worker.finished.connect(
                lambda w=self.thumbnail_worker: self._cleanup_thumbnail_worker(w)
            )
            self.thumbnail_worker = None

        items_to_process = []
        for index, slide in enumerate(self.project.slides):
            item = QListWidgetItem()

            # Create a solid gray placeholder pixmap matching #2d2d30
            placeholder = QPixmap(140, 80)
            placeholder.fill(QColor("#2d2d30"))
            item.setIcon(QIcon(placeholder))

            thumb_path = slide.media_path
            if slide.media_type == MediaType.LIVE_PHOTO and slide.use_video_clip and slide.video_path:
                thumb_path = slide.video_path

            items_to_process.append((index, thumb_path, (140, 80), 'timeline'))

            dur_str = f"{slide.duration:.1f}s"
            item.setText(f"{dur_str}\n{slide.effect_preset.value}")
            item.setData(Qt.UserRole, slide)
            self.timeline_list.addItem(item)

        if items_to_process:
            self.thumbnail_worker = ThumbnailWorker(items_to_process, self)
            self.thumbnail_worker.thumbnail_ready.connect(self.on_thumbnail_ready)
            self.thumbnail_worker.start()

    def sync_timeline_order(self):
        # Update project.slides based on the new list order
        new_slides = []
        for i in range(self.timeline_list.count()):
            item = self.timeline_list.item(i)
            new_slides.append(item.data(Qt.UserRole))
        self.project.slides = new_slides
        self.is_dirty = True

    def move_slide_up(self):
        selected = self.timeline_list.selectedItems()
        if not selected:
            return

        item = selected[0]
        row = self.timeline_list.row(item)

        if row > 0:
            # Swap in model
            self.project.slides[row], self.project.slides[row - 1] = self.project.slides[row - 1], self.project.slides[row]

            # Swap in view
            taken = self.timeline_list.takeItem(row)
            self.timeline_list.insertItem(row - 1, taken)

            # Reselect
            taken.setSelected(True)
            self.is_dirty = True

    def move_slide_down(self):
        selected = self.timeline_list.selectedItems()
        if not selected:
            return

        item = selected[0]
        row = self.timeline_list.row(item)

        if row < self.timeline_list.count() - 1:
            # Swap in model
            self.project.slides[row], self.project.slides[row + 1] = self.project.slides[row + 1], self.project.slides[row]

            # Swap in view
            taken = self.timeline_list.takeItem(row)
            self.timeline_list.insertItem(row + 1, taken)

            # Reselect
            taken.setSelected(True)
            self.is_dirty = True

    def remove_selected(self):
        # Remove from timeline
        t_sel = self.timeline_list.selectedItems()
        if t_sel:
            for item in t_sel:
                slide = item.data(Qt.UserRole)
                if slide in self.project.slides:
                    self.project.slides.remove(slide)
            self.refresh_timeline()
            self.is_dirty = True

        # Remove from audio
        a_sel = self.audio_list.selectedItems()
        if a_sel:
            for item in a_sel:
                audio = item.data(Qt.UserRole)
                if audio in self.project.audio_tracks:
                    self.project.audio_tracks.remove(audio)
            self.refresh_audio_list()
            self.is_dirty = True

    def add_audio_dialog(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Select Audio Files", "", "Audio Files (*.mp3 *.wav *.m4a *.aac *.flac *.ogg)")
        for f in files:
            self.project.audio_tracks.append(AudioItem(file_path=f))
        self.refresh_audio_list()
        self.is_dirty = True

    def refresh_audio_list(self):
        self.audio_list.clear()
        for track in self.project.audio_tracks:
            item = QListWidgetItem()
            item.setText(os.path.basename(track.file_path))
            item.setData(Qt.UserRole, track)
            self.audio_list.addItem(item)

    def sync_audio_order(self):
        new_audio = []
        for i in range(self.audio_list.count()):
            item = self.audio_list.item(i)
            new_audio.append(item.data(Qt.UserRole))
        self.project.audio_tracks = new_audio
        self.is_dirty = True

    # --- Global Project Methods ---
    def global_settings_changed(self):
        self.project.backing_track_volume = self.global_audio_slider.value() / 100.0
        self.project.global_transition_duration = self.global_crossfade_spin.value()
        self.is_dirty = True

    # --- Inspector Methods ---
    def on_timeline_selection(self):
        items = self.timeline_list.selectedItems()
        if items:
            slide = items[0].data(Qt.UserRole)
            self.update_inspector_state(slide)
        else:
            self.update_inspector_state(None)

    def update_inspector_state(self, slide: SlideItem):
        self._updating_inspector = True

        if slide is None:
            self.effect_combo.setEnabled(False)
            self.zoom_spin.setEnabled(False)
            self.focal_x.setEnabled(False)
            self.focal_y.setEnabled(False)
            self.duration_spin.setEnabled(False)
            self.cb_crossfade_global.setEnabled(False)
            self.crossfade_override.setEnabled(False)
            self.lp_group.setVisible(False)
            self.video_group.setVisible(False)
        else:
            self.cb_crossfade_global.setEnabled(True)
            self.effect_combo.setEnabled(True)
            self.zoom_spin.setEnabled(True)
            self.focal_x.setEnabled(True)
            self.focal_y.setEnabled(True)
            self.duration_spin.setEnabled(True)

            # Find combo index
            for i in range(self.effect_combo.count()):
                if self.effect_combo.itemText(i) == slide.effect_preset.value:
                    self.effect_combo.setCurrentIndex(i)
                    break

            self.zoom_spin.setValue(slide.start_zoom)
            self.focal_x.setValue(slide.focal_point[0])
            self.focal_y.setValue(slide.focal_point[1])
            self.duration_spin.setValue(slide.duration)

            if slide.transition_duration is None:
                self.cb_crossfade_global.setChecked(True)
                self.crossfade_override.setEnabled(False)
                # Display current global just for context but disable editing
                self.crossfade_override.setValue(self.project.global_transition_duration)
            else:
                self.cb_crossfade_global.setChecked(False)
                self.crossfade_override.setEnabled(True)
                self.crossfade_override.setValue(slide.transition_duration)

            # Live photo logic
            if slide.media_type == MediaType.LIVE_PHOTO:
                self.lp_group.setVisible(True)
                self.cb_use_video.setChecked(slide.use_video_clip)
            else:
                self.lp_group.setVisible(False)

            # Video logic
            is_vid = slide.media_type == MediaType.VIDEO or (slide.media_type == MediaType.LIVE_PHOTO and slide.use_video_clip)
            if is_vid:
                self.video_group.setVisible(True)
                self.trim_in_spin.setValue(slide.trim_in)
                self.cb_include_audio.setChecked(slide.include_audio)
                self.video_vol_slider.setValue(int(slide.audio_volume * 100))
            else:
                self.video_group.setVisible(False)

        self._updating_inspector = False

    def inspector_changed(self):
        if self._updating_inspector:
            return

        items = self.timeline_list.selectedItems()
        if not items:
            return

        slide: SlideItem = items[0].data(Qt.UserRole)

        slide.effect_preset = EffectPreset(self.effect_combo.currentText())
        slide.start_zoom = self.zoom_spin.value()
        slide.focal_point = (self.focal_x.value(), self.focal_y.value())
        slide.duration = self.duration_spin.value()

        if self.cb_crossfade_global.isChecked():
            slide.transition_duration = None
            self.crossfade_override.setEnabled(False)
        else:
            slide.transition_duration = self.crossfade_override.value()
            self.crossfade_override.setEnabled(True)

        if slide.media_type == MediaType.LIVE_PHOTO:
            slide.use_video_clip = self.cb_use_video.isChecked()

        is_vid = slide.media_type == MediaType.VIDEO or (slide.media_type == MediaType.LIVE_PHOTO and slide.use_video_clip)
        if is_vid:
            slide.trim_in = self.trim_in_spin.value()

        slide.include_audio = self.cb_include_audio.isChecked()
        slide.audio_volume = self.video_vol_slider.value() / 100.0

        # We need to refresh the timeline text for duration/effect
        idx = self.timeline_list.row(items[0])
        self.timeline_list.item(idx).setText(f"{slide.duration:.1f}s\n{slide.effect_preset.value}")

        # If they toggle live photo video clip, we might need to show/hide the video audio controls
        self.update_inspector_state(slide)

        self.is_dirty = True

    # --- Project Save/Load Methods ---
    def check_unsaved_changes(self) -> bool:
        """Shows confirmation dialog if there are unsaved changes. Returns True if we can proceed (saved or discarded), False to cancel."""
        if not self.is_dirty:
            return True

        reply = QMessageBox.question(self, "Unsaved Changes",
                                     "You have unsaved changes. Do you want to save before proceeding?",
                                     QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                                     QMessageBox.Save)

        if reply == QMessageBox.Save:
            self.save_project_dialog()
            return not self.is_dirty # True if save was successful
        elif reply == QMessageBox.Discard:
            return True
        else:
            return False

    def save_project_dialog(self):
        if not self.current_project_path:
            path, _ = QFileDialog.getSaveFileName(self, "Save Project", "", "Slideshow Project (*.fms)")
            if not path:
                return
            if not path.endswith('.fms'):
                path += '.fms'
            self.current_project_path = path

        try:
            save_project(self.project, self.current_project_path)
            self.is_dirty = False
            QMessageBox.information(self, "Project Saved", f"Project successfully saved to:\n{self.current_project_path}")
        except Exception as e:
            QMessageBox.critical(self, "Save Error", f"Failed to save project:\n{str(e)}")

    def open_project_dialog(self):
        if not self.check_unsaved_changes():
            return

        path, _ = QFileDialog.getOpenFileName(self, "Open Project", "", "Slideshow Project (*.fms)")
        if not path:
            return

        try:
            project, missing_files = load_project(path)
            self.project = project
            self.current_project_path = path
            self.is_dirty = False

            # Rebuild media library
            # Extract unique paths from project.slides
            paths = []
            for slide in self.project.slides:
                if slide.media_path not in paths:
                    paths.append(slide.media_path)

            self.media_library.clear()
            self.handle_files_dropped(paths)

            # Refresh UI
            self.refresh_timeline()
            self.refresh_audio_list()
            self.global_audio_slider.setValue(int(self.project.backing_track_volume * 100))
            self.global_crossfade_spin.setValue(self.project.global_transition_duration)
            self.update_inspector_state(None)

            if missing_files:
                missing_str = "\n".join(missing_files[:10])
                if len(missing_files) > 10:
                    missing_str += f"\n... and {len(missing_files) - 10} more."
                QMessageBox.warning(self, "Missing Files",
                                    f"The following files could not be found. Please relocate them to resolve missing media:\n\n{missing_str}")

        except Exception as e:
            QMessageBox.critical(self, "Load Error", f"Failed to load project:\n{str(e)}")

    # --- Preview Methods ---
    def trigger_preview_generation(self):
        import time
        print(f"[{time.strftime('%H:%M:%S')}] [UI] Preview generation triggered with {len(self.project.slides)} slides")

        # Release the current preview file before generating a new one to prevent PermissionError on cleanup
        self.media_player.stop()
        self.media_player.setSource(QUrl())

        if not self.project.slides:
            self.btn_play_pause.setText("Play")
            self.btn_play_pause.setEnabled(False)
            self.scrubber.setEnabled(False)
            return

        self.btn_play_pause.setText("Play")
        self.btn_play_pause.setEnabled(False)
        self.scrubber.setEnabled(False)
        self.gen_overlay.show()

        if self.preview_generator:
            self.preview_generator.cancel()

        self.preview_generator = PreviewGenerator(self.project)
        self.preview_generator.preview_ready.connect(self.on_preview_ready)
        self.preview_generator.error_occurred.connect(self.on_preview_error)
        self.preview_generator.generate()

    def on_preview_ready(self, video_path):
        import time
        print(f"[{time.strftime('%H:%M:%S')}] [UI] Preview ready: {video_path}")
        total_duration = self.project.get_total_duration()
        self.statusBar().showMessage(f"Preview ready — {total_duration:.1f}s slideshow")
        self.gen_overlay.hide()
        self.media_player.setSource(QUrl.fromLocalFile(video_path))
        self.btn_play_pause.setEnabled(True)
        self.scrubber.setEnabled(True)
        self.media_player.play()
        self.btn_play_pause.setText("Pause")

    def on_preview_error(self, err_msg):
        import time
        print(f"[{time.strftime('%H:%M:%S')}] [UI] Preview error: {err_msg}")
        self.gen_overlay.hide()
        print(f"Preview Generation Error: {err_msg}")

    def on_media_error(self, error, error_string):
        print(f"[UI] QMediaPlayer error: {error} — {error_string}")

    def cancel_preview_generation(self):
        import time
        print(f"[{time.strftime('%H:%M:%S')}] [UI] Preview cancelled by user")
        if self.preview_generator:
            self.preview_generator.cancel()
        self.gen_overlay.hide()

    # --- Player Controls ---
    def toggle_play_pause(self):
        if self.media_player.playbackState() == QMediaPlayer.PlayingState:
            self.media_player.pause()
            self.btn_play_pause.setText("Play")
        else:
            self.media_player.play()
            self.btn_play_pause.setText("Pause")

    def update_duration(self, duration):
        self.scrubber.setRange(0, duration)
        self.update_time_label()

    def update_scrubber(self, position):
        self.scrubber.setValue(position)
        self.update_time_label()

    def set_position(self, position):
        self.media_player.setPosition(position)

    def handle_media_status(self, status):
        if status == QMediaPlayer.EndOfMedia:
            self.media_player.setPosition(0)
            self.media_player.pause()
            self.btn_play_pause.setText("Play")

    def update_time_label(self):
        pos = self.media_player.position() // 1000
        dur = self.media_player.duration() // 1000
        self.time_label.setText(f"{pos//60:02d}:{pos%60:02d} / {dur//60:02d}:{dur%60:02d}")

    # --- Export Methods ---
    def export_dialog(self):
        if not self.project.slides:
            QMessageBox.warning(self, "No Slides", "Please add at least one slide to the timeline before exporting.")
            return

        # Show settings dialog first
        settings_dlg = ExportSettingsDialog(self)
        if settings_dlg.exec():
            settings = settings_dlg.get_settings()
            self.project.output_resolution = settings["resolution"]
            self.project.target_fps = settings["fps"]

            out_path, _ = QFileDialog.getSaveFileName(self, "Export Slideshow", "", "MP4 Video (*.mp4)")
            if not out_path:
                return

            self.export_dlg = ExportProgressDialog(self)
            self.export_dlg.show()

            self.exporter = Exporter(self.project, out_path, fps=settings["fps"], resolution=settings["resolution"], quality=settings["quality"])
            self.exporter.progress_updated.connect(self.export_dlg.set_progress)
            self.exporter.export_complete.connect(self.on_export_complete)
            self.exporter.error_occurred.connect(self.on_export_error)

            self.export_dlg.btn_cancel.clicked.connect(self.cancel_export)
            self.exporter.export()

    def on_export_complete(self, path):
        self.export_dlg.accept()
        self.statusBar().showMessage(f"Exported to {path}")
        QMessageBox.information(self, "Export Complete", f"Slideshow exported successfully to:\n{path}")

    def on_export_error(self, err):
        self.export_dlg.reject()
        QMessageBox.critical(self, "Export Error", f"An error occurred during export:\n{err}")

    def cancel_export(self):
        if self.exporter:
            self.exporter.cancel()
        self.export_dlg.reject()

    def closeEvent(self, event):
        """Clean up when application is closing."""
        if self.is_dirty:
            reply = QMessageBox.question(self, 'Unsaved Changes',
                                         "You have unsaved changes. Save before closing?",
                                         QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                                         QMessageBox.Save)

            if reply == QMessageBox.Save:
                self.save_project_dialog()
                if self.is_dirty: # Save failed or cancelled
                    event.ignore()
                    return
            elif reply == QMessageBox.Cancel:
                event.ignore()
                return

        if self.preview_generator:
            self.preview_generator.cancel()
            self.preview_generator.cleanup()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
