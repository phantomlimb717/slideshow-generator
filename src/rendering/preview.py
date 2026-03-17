import os
import tempfile
import threading
from PySide6.QtCore import QObject, Signal, QTimer
from rendering.renderer import SlideshowRenderer
from audio.mixer import build_audio_mix
from models.project import Project
import ffmpeg

class PreviewGenerator(QObject):
    progress_updated = Signal(int)
    preview_ready = Signal(str)
    error_occurred = Signal(str)

    def __init__(self, project: Project):
        super().__init__()
        self.project = project
        self._cancel = False
        self._thread = None
        self.temp_dir_obj = None
        self.temp_dir = None
        self.prev_temp_dir_obj = None

    def cleanup(self):
        """Clean up current temp directory."""
        if self.temp_dir_obj:
            try:
                self.temp_dir_obj.cleanup()
            except Exception:
                pass
        if self.prev_temp_dir_obj:
            try:
                self.prev_temp_dir_obj.cleanup()
            except Exception:
                pass

    def cancel(self):
        self._cancel = True
        if self._thread:
            self._thread.join()

    def generate(self):
        if self._thread and self._thread.is_alive():
            self.cancel()

        self._cancel = False

        # Clean up the previous temp dir before starting a new one
        if self.prev_temp_dir_obj:
            try:
                self.prev_temp_dir_obj.cleanup()
            except Exception:
                pass

        self.prev_temp_dir_obj = self.temp_dir_obj
        self.temp_dir_obj = tempfile.TemporaryDirectory(prefix="fms_preview_")
        self.temp_dir = self.temp_dir_obj.name

        self._thread = threading.Thread(target=self._run_generation)
        self._thread.start()

    def _run_generation(self):
        import time
        try:
            # At the start:
            print(f"[{time.strftime('%H:%M:%S')}] [Preview] Starting generation for {len(self.project.slides)} slides")
            start_time = time.time()

            # 1. Generate audio mix
            print(f"[{time.strftime('%H:%M:%S')}] [Preview] Building audio mix...")
            t = time.time()
            audio_path = os.path.join(self.temp_dir, "preview_audio.wav")
            build_audio_mix(self.project, audio_path)
            print(f"[{time.strftime('%H:%M:%S')}] [Preview] Audio mix complete ({time.time()-t:.1f}s)")

            if self._cancel:
                return

            # 2. Render frames (Proxy Resolution 640x360 @ 30fps)
            print(f"[{time.strftime('%H:%M:%S')}] [Preview] Rendering {len(self.project.slides)} slides at 640x360 @ 30fps")
            t = time.time()
            renderer = SlideshowRenderer(self.project, fps=30, resolution=(640, 360))
            video_path = os.path.join(self.temp_dir, "preview_video.mp4")

            # Use ffmpeg-python to create a subprocess for writing frames
            process = (
                ffmpeg
                .input('pipe:', format='rawvideo', pix_fmt='rgb24', s=f'640x360', r=30)
                .output(video_path, pix_fmt='yuv420p', vcodec='libx264', crf=28, preset='ultrafast')
                .overwrite_output()
                .run_async(pipe_stdin=True, quiet=True)
            )

            try:
                for frame_idx, total_frames, frame_data in renderer.render_project():
                    if self._cancel:
                        break

                    process.stdin.write(frame_data.tobytes())

                    if frame_idx % 30 == 0:
                        print(f"[{time.strftime('%H:%M:%S')}] [Preview] Frame {frame_idx}/{total_frames} ({int(frame_idx/max(1,total_frames)*100)}%)")

                    # Update progress
                    progress = int((frame_idx / max(1, total_frames)) * 100)
                    self.progress_updated.emit(progress)
                print(f"[{time.strftime('%H:%M:%S')}] [Preview] Frame rendering complete ({time.time()-t:.1f}s)")
            except (BrokenPipeError, OSError) as e:
                stderr_output = ""
                try:
                    stderr_output = process.stderr.read().decode()
                except Exception:
                    pass
                self.error_occurred.emit(f"FFmpeg encoding failed: {stderr_output or str(e)}")
                return
            finally:
                try:
                    process.stdin.close()
                    process.wait()
                except Exception:
                    pass

            if self._cancel:
                return

            # 3. Mux audio and video
            print(f"[{time.strftime('%H:%M:%S')}] [Preview] Muxing audio and video...")
            t = time.time()
            final_path = os.path.join(self.temp_dir, "preview_final.mp4")
            if os.path.exists(audio_path):
                # Mix them
                try:
                    video_stream = ffmpeg.input(video_path)
                    audio_stream = ffmpeg.input(audio_path)
                    (
                        ffmpeg
                        .output(video_stream, audio_stream, final_path, vcodec='copy', acodec='aac', strict='experimental')
                        .overwrite_output()
                        .run(quiet=True)
                    )
                except ffmpeg.Error as e:
                    print(f"Muxing error: {e.stderr.decode()}")
                    self.error_occurred.emit("Failed to mix preview audio and video.")
                    return
            else:
                # No audio, just copy video
                import shutil
                shutil.copy(video_path, final_path)

            print(f"[{time.strftime('%H:%M:%S')}] [Preview] Mux complete ({time.time()-t:.1f}s)")
            print(f"[{time.strftime('%H:%M:%S')}] [Preview] Total generation time: {time.time()-start_time:.1f}s")

            self.preview_ready.emit(final_path)

        except Exception as e:
            self.error_occurred.emit(str(e))
        finally:
            if self._cancel:
                try:
                    self.temp_dir_obj.cleanup()
                    self.temp_dir_obj = None
                except Exception:
                    pass
