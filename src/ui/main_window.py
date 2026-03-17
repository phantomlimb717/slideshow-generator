import sys
import os
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QToolBar, QSplitter, QLabel, QPushButton, QSlider, QComboBox,
    QSpinBox, QDoubleSpinBox, QCheckBox, QListWidget, QListWidgetItem,
    QFrame, QFileDialog, QMessageBox, QProgressDialog
)
from PySide6.QtCore import Qt, QSize, QUrl, QTimer, Signal
from PySide6.QtGui import QIcon, QAction, QPixmap, QImage
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from PIL import ImageQt

from models.project import Project, SlideItem, MediaType, EffectPreset, AudioItem
from utils.media_import import scan_directory_for_media, extract_thumbnail
from rendering.preview import PreviewGenerator
from export.exporter import Exporter
from ui.export_dialog import ExportProgressDialog, ExportSettingsDialog

class ListWidgetDraggable(QListWidget):
    itemsDropped = Signal()
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QListWidget.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)

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
        self.setGridSize(QSize(170, 110))
        self.setViewMode(QListWidget.IconMode)
        self.setResizeMode(QListWidget.Adjust)
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
        self.setWindowTitle("Family Memories Slideshow Generator")
        self.setMinimumSize(1200, 800)

        self.project = Project()
        self.media_library: list[SlideItem] = []

        # Debounce timer for preview regeneration
        self.preview_timer = QTimer()
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(1000) # 1 second debounce
        self.preview_timer.timeout.connect(self.trigger_preview_generation)

        self.preview_generator = None

        self.setup_ui()
        self.apply_dark_theme()

        # Initial state setup
        self.update_inspector_state(None)

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)

        self.setup_toolbar()

        main_splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(main_splitter)

        # --- Left Panel: Media Library ---
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.addWidget(QLabel("Media Library (Drag & Drop)"))

        self.media_list = MediaLibraryWidget()
        self.media_list.filesDropped.connect(self.handle_files_dropped)
        left_layout.addWidget(self.media_list)

        btn_add_files = QPushButton("Add Folder / Files...")
        btn_add_files.clicked.connect(self.add_media_dialog)
        left_layout.addWidget(btn_add_files)

        main_splitter.addWidget(left_panel)

        # --- Center Panel: Preview & Timeline ---
        center_panel = QWidget()
        center_layout = QVBoxLayout(center_panel)

        # Video Player
        preview_frame = QFrame()
        preview_frame.setFrameShape(QFrame.StyledPanel)
        preview_layout = QVBoxLayout(preview_frame)

        self.video_widget = QVideoWidget()
        preview_layout.addWidget(self.video_widget)

        self.audio_output = QAudioOutput()
        self.media_player = QMediaPlayer()
        self.media_player.setVideoOutput(self.video_widget)
        self.media_player.setAudioOutput(self.audio_output)

        self.media_player.positionChanged.connect(self.update_scrubber)
        self.media_player.durationChanged.connect(self.update_duration)
        self.media_player.mediaStatusChanged.connect(self.handle_media_status)

        # Controls
        controls_layout = QHBoxLayout()
        self.btn_play_pause = QPushButton("Play")
        self.btn_play_pause.clicked.connect(self.toggle_play_pause)
        controls_layout.addWidget(self.btn_play_pause)

        self.scrubber = QSlider(Qt.Horizontal)
        self.scrubber.sliderMoved.connect(self.set_position)
        controls_layout.addWidget(self.scrubber)

        self.time_label = QLabel("00:00 / 00:00")
        controls_layout.addWidget(self.time_label)

        preview_layout.addLayout(controls_layout)

        # Generation overlay
        self.gen_label = QLabel("Generating preview...")
        self.gen_label.setStyleSheet("background-color: rgba(0,0,0,150); color: white; padding: 10px; border-radius: 5px;")
        self.gen_label.setAlignment(Qt.AlignCenter)
        self.gen_label.hide()
        # Overlay hack: put it in the same layout but we'd need a layered layout to really float it.
        # For simplicity, we just put it below the video widget and show/hide.
        preview_layout.addWidget(self.gen_label)

        center_layout.addWidget(preview_frame, stretch=2)

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

        center_layout.addLayout(timeline_header)

        self.timeline_list = ListWidgetDraggable()
        self.timeline_list.setFlow(QListWidget.LeftToRight)
        self.timeline_list.setWrapping(False)
        self.timeline_list.setMinimumHeight(150)
        self.timeline_list.setIconSize(QSize(120, 68))
        self.timeline_list.setViewMode(QListWidget.IconMode)
        self.timeline_list.setDragDropMode(QListWidget.InternalMove)
        self.timeline_list.itemsDropped.connect(self.sync_timeline_order)
        self.timeline_list.itemSelectionChanged.connect(self.on_timeline_selection)
        center_layout.addWidget(self.timeline_list, stretch=1)

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
        center_layout.addLayout(audio_header)

        self.audio_list = ListWidgetDraggable()
        self.audio_list.setFlow(QListWidget.LeftToRight)
        self.audio_list.setWrapping(False)
        self.audio_list.setFixedHeight(60)
        self.audio_list.setDragDropMode(QListWidget.InternalMove)
        self.audio_list.itemsDropped.connect(self.sync_audio_order)
        center_layout.addWidget(self.audio_list)

        main_splitter.addWidget(center_panel)

        # --- Right Panel: Inspector ---
        right_panel = QWidget()
        right_panel.setMinimumWidth(300)
        right_layout = QVBoxLayout(right_panel)
        right_layout.addWidget(QLabel("Inspector"))

        slide_props_frame = QFrame()
        slide_props_frame.setFrameShape(QFrame.StyledPanel)
        slide_props_layout = QVBoxLayout(slide_props_frame)

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
        crossfade_layout = QHBoxLayout()
        crossfade_layout.addWidget(QLabel("Crossfade override (s):"))
        self.crossfade_override = QDoubleSpinBox()
        self.crossfade_override.setRange(0.0, 10.0)
        self.crossfade_override.setSingleStep(0.5)
        self.crossfade_override.setSpecialValueText("Global Default")
        self.crossfade_override.setValue(0.0)
        self.crossfade_override.valueChanged.connect(self.inspector_changed)
        crossfade_layout.addWidget(self.crossfade_override)
        slide_props_layout.addLayout(crossfade_layout)

        # Live Photo toggle
        self.lp_group = QWidget()
        lp_layout = QVBoxLayout(self.lp_group)
        lp_layout.setContentsMargins(0, 0, 0, 0)
        self.cb_use_video = QCheckBox("Use Video Clip (Live Photo)")
        self.cb_use_video.stateChanged.connect(self.inspector_changed)
        lp_layout.addWidget(self.cb_use_video)
        slide_props_layout.addWidget(self.lp_group)

        # Video Specific
        self.video_group = QWidget()
        video_layout = QVBoxLayout(self.video_group)
        video_layout.setContentsMargins(0, 0, 0, 0)

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
        main_splitter.setSizes([300, 600, 300])

        self._updating_inspector = False # guard flag to prevent feedback loops

    def setup_toolbar(self):
        toolbar = QToolBar("Main Toolbar")
        self.addToolBar(toolbar)

        act_add = QAction("Add Folder", self)
        act_add.triggered.connect(self.add_media_dialog)
        toolbar.addAction(act_add)

        act_rm = QAction("Remove Slide/Audio", self)
        act_rm.triggered.connect(self.remove_selected)
        toolbar.addAction(act_rm)

        toolbar.addSeparator()

        act_prev = QAction("Force Preview Update", self)
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

    def handle_files_dropped(self, paths):
        slides = []
        for path in paths:
            if os.path.isdir(path):
                slides.extend(scan_directory_for_media(path))
            else:
                # To simplify, we rely on the scan function if we want pairing logic.
                # If they drop a single file, we could manually parse it, but let's just use the scanner on its dir
                # and filter out.
                pdir = os.path.dirname(path)
                found = scan_directory_for_media(pdir)
                for s in found:
                    if s.media_path == path or s.video_path == path:
                        # Avoid duplicates
                        if not any(ls.media_path == s.media_path for ls in self.media_library):
                            slides.append(s)

        self.media_library.extend(slides)
        self.refresh_media_list()

    def refresh_media_list(self):
        self.media_list.clear()
        for slide in self.media_library:
            item = QListWidgetItem()
            # Generate thumbnail
            thumb_path = slide.media_path
            if slide.media_type == MediaType.LIVE_PHOTO and slide.use_video_clip and slide.video_path:
                thumb_path = slide.video_path

            thumb = extract_thumbnail(thumb_path)
            if thumb:
                # Convert PIL to QPixmap
                qimg = ImageQt.ImageQt(thumb.convert("RGBA"))
                pixmap = QPixmap.fromImage(qimg)
                item.setIcon(QIcon(pixmap))

            name = os.path.basename(slide.media_path)
            if slide.media_type == MediaType.LIVE_PHOTO:
                name = "[LIVE] " + name
            item.setText(name)
            item.setData(Qt.UserRole, slide)
            self.media_list.addItem(item)

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
        self.schedule_preview_update()

    def refresh_timeline(self):
        self.timeline_list.clear()
        for slide in self.project.slides:
            item = QListWidgetItem()

            thumb_path = slide.media_path
            if slide.media_type == MediaType.LIVE_PHOTO and slide.use_video_clip and slide.video_path:
                thumb_path = slide.video_path

            thumb = extract_thumbnail(thumb_path, size=(120, 68))
            if thumb:
                qimg = ImageQt.ImageQt(thumb.convert("RGBA"))
                item.setIcon(QIcon(QPixmap.fromImage(qimg)))

            dur_str = f"{slide.duration:.1f}s"
            item.setText(f"{dur_str}\n{slide.effect_preset.value}")
            item.setData(Qt.UserRole, slide)
            self.timeline_list.addItem(item)

    def sync_timeline_order(self):
        # Update project.slides based on the new list order
        new_slides = []
        for i in range(self.timeline_list.count()):
            item = self.timeline_list.item(i)
            new_slides.append(item.data(Qt.UserRole))
        self.project.slides = new_slides
        self.schedule_preview_update()

    def remove_selected(self):
        # Remove from timeline
        t_sel = self.timeline_list.selectedItems()
        if t_sel:
            for item in t_sel:
                slide = item.data(Qt.UserRole)
                if slide in self.project.slides:
                    self.project.slides.remove(slide)
            self.refresh_timeline()
            self.schedule_preview_update()

        # Remove from audio
        a_sel = self.audio_list.selectedItems()
        if a_sel:
            for item in a_sel:
                audio = item.data(Qt.UserRole)
                if audio in self.project.audio_tracks:
                    self.project.audio_tracks.remove(audio)
            self.refresh_audio_list()
            self.schedule_preview_update()

    def add_audio_dialog(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Select Audio Files", "", "Audio Files (*.mp3 *.wav *.m4a *.aac *.flac *.ogg)")
        for f in files:
            self.project.audio_tracks.append(AudioItem(file_path=f))
        self.refresh_audio_list()
        self.schedule_preview_update()

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
        self.schedule_preview_update()

    # --- Global Project Methods ---
    def global_settings_changed(self):
        self.project.backing_track_volume = self.global_audio_slider.value() / 100.0
        self.project.global_transition_duration = self.global_crossfade_spin.value()
        self.schedule_preview_update()

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
            self.crossfade_override.setEnabled(False)
            self.lp_group.setVisible(False)
            self.video_group.setVisible(False)
        else:
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

        self.schedule_preview_update()

    # --- Preview Methods ---
    def schedule_preview_update(self):
        self.preview_timer.start()

    def trigger_preview_generation(self):
        if not self.project.slides:
            # Stop playback, clear video
            self.media_player.stop()
            self.media_player.setSource(QUrl())
            return

        self.media_player.pause()
        self.gen_label.show()

        if self.preview_generator:
            self.preview_generator.cancel()

        self.preview_generator = PreviewGenerator(self.project)
        self.preview_generator.preview_ready.connect(self.on_preview_ready)
        self.preview_generator.error_occurred.connect(self.on_preview_error)
        self.preview_generator.generate()

    def on_preview_ready(self, video_path):
        self.gen_label.hide()
        self.media_player.setSource(QUrl.fromLocalFile(video_path))
        self.media_player.play()
        self.btn_play_pause.setText("Pause")

    def on_preview_error(self, err_msg):
        self.gen_label.hide()
        print(f"Preview Generation Error: {err_msg}")

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
        if self.preview_generator:
            self.preview_generator.cancel()
            self.preview_generator.cleanup()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
