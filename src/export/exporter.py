import os
import tempfile
import threading
from PySide6.QtCore import QObject, Signal
from rendering.renderer import SlideshowRenderer
from audio.mixer import build_audio_mix
from models.project import Project
import ffmpeg

class Exporter(QObject):
    progress_updated = Signal(int)
    export_complete = Signal(str)
    error_occurred = Signal(str)

    def __init__(self, project: Project, output_path: str, fps: int = 30, resolution: tuple[int, int] = (1920, 1080), quality: int = 23):
        super().__init__()
        self.project = project
        self.output_path = output_path
        self.fps = fps
        self.resolution = resolution
        self.quality = quality
        self._cancel = False
        self._thread = None
        self.temp_dir = tempfile.mkdtemp(prefix="fms_export_")

    def cancel(self):
        self._cancel = True
        if self._thread:
            self._thread.join()

    def export(self):
        if self._thread and self._thread.is_alive():
            self.cancel()

        self._cancel = False
        self._thread = threading.Thread(target=self._run_export)
        self._thread.start()

    def _run_export(self):
        try:
            # 1. Generate audio mix
            audio_path = os.path.join(self.temp_dir, "export_audio.wav")
            build_audio_mix(self.project, audio_path)

            if self._cancel:
                return

            # 2. Render frames
            renderer = SlideshowRenderer(self.project, fps=self.fps, resolution=self.resolution)

            # Use ffmpeg-python to create a subprocess for writing frames
            process = (
                ffmpeg
                .input('pipe:', format='rawvideo', pix_fmt='rgb24', s=f'{self.resolution[0]}x{self.resolution[1]}', r=self.fps)
                .output(self.output_path, pix_fmt='yuv420p', vcodec='libx264', crf=self.quality, preset='medium')
                .overwrite_output()
                .run_async(pipe_stdin=True, quiet=True)
            )

            for frame_idx, total_frames, frame_data in renderer.render_project():
                if self._cancel:
                    process.stdin.close()
                    process.wait()
                    return

                process.stdin.write(frame_data.tobytes())

                # Update progress
                progress = int((frame_idx / total_frames) * 100)
                self.progress_updated.emit(progress)

            process.stdin.close()
            process.wait()

            if self._cancel:
                return

            # 3. Mux audio and video
            if os.path.exists(audio_path):
                # Mix them
                final_path = self.output_path
                temp_video = os.path.join(self.temp_dir, "temp_video.mp4")
                os.rename(final_path, temp_video)
                try:
                    video_stream = ffmpeg.input(temp_video)
                    audio_stream = ffmpeg.input(audio_path)
                    (
                        ffmpeg
                        .output(video_stream, audio_stream, final_path, vcodec='copy', acodec='aac', strict='experimental')
                        .overwrite_output()
                        .run(quiet=True)
                    )
                except ffmpeg.Error as e:
                    print(f"Muxing error: {e.stderr.decode()}")
                    self.error_occurred.emit("Failed to mix export audio and video.")
                    return

            self.export_complete.emit(self.output_path)

        except Exception as e:
            self.error_occurred.emit(str(e))
