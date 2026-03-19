import os
import tempfile
import threading
import subprocess
import time
from PySide6.QtCore import QObject, Signal
from rendering.renderer import SlideshowRenderer
from audio.mixer import build_audio_mix
from models.project import Project
import ffmpeg

def check_nvenc_available() -> bool:
    """Checks if h264_nvenc is available in the current ffmpeg installation."""
    try:
        result = subprocess.run(
            ['ffmpeg', '-encoders'],
            capture_output=True,
            text=True,
            timeout=5
        )
        return 'h264_nvenc' in result.stdout
    except Exception:
        return False

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
        self.temp_dir_obj = tempfile.TemporaryDirectory(prefix="fms_export_")
        self.temp_dir = self.temp_dir_obj.name

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
            has_audio = (
                len(self.project.audio_tracks) > 0 or
                any(s.include_audio for s in self.project.slides)
            )

            # 1. Generate audio mix
            audio_path = os.path.join(self.temp_dir, "export_audio.wav")
            if has_audio:
                print(f"[{time.strftime('%H:%M:%S')}] [Export] Building audio mix...")
                build_audio_mix(self.project, audio_path)
            else:
                print(f"[{time.strftime('%H:%M:%S')}] [Export] Skipping audio mix (no audio tracks/clips)")

            if self._cancel:
                return

            # 2. Render frames
            renderer = SlideshowRenderer(self.project, fps=self.fps, resolution=self.resolution)

            # Use ffmpeg-python to create a subprocess for writing frames
            temp_video = os.path.join(self.temp_dir, "temp_video.mp4")
            stderr_output = []

            use_nvenc = check_nvenc_available()
            if use_nvenc:
                print(f"[{time.strftime('%H:%M:%S')}] [Export] Using NVENC hardware acceleration.")
                output_args = {
                    'pix_fmt': 'yuv420p',
                    'vcodec': 'h264_nvenc',
                    'preset': 'p6',
                    'cq': self.quality,
                    'rc': 'vbr'
                }
            else:
                output_args = {
                    'pix_fmt': 'yuv420p',
                    'vcodec': 'libx264',
                    'preset': 'medium',
                    'crf': self.quality
                }

            process = (
                ffmpeg
                .input('pipe:', format='rawvideo', pix_fmt='rgb24', s=f'{self.resolution[0]}x{self.resolution[1]}', r=self.fps)
                .output(temp_video, **output_args)
                .overwrite_output()
                .run_async(pipe_stdin=True, pipe_stderr=True)
            )

            def read_stderr():
                try:
                    while True:
                        chunk = process.stderr.read(4096)
                        if not chunk:
                            break
                        stderr_output.append(chunk.decode(errors='replace'))
                        if len(stderr_output) > 10:
                            stderr_output.pop(0)
                except Exception:
                    pass

            stderr_thread = threading.Thread(target=read_stderr, daemon=True)
            stderr_thread.start()

            try:
                for frame_idx, total_frames, frame_data in renderer.render_project():
                    if self._cancel:
                        break

                    process.stdin.write(frame_data.tobytes())

                    # Update progress
                    progress = int((frame_idx / max(1, total_frames)) * 100)
                    self.progress_updated.emit(progress)
            except (BrokenPipeError, OSError) as e:
                if 'stderr_thread' in locals() and stderr_thread.is_alive():
                    stderr_thread.join(timeout=1.0)
                err_str = "".join(stderr_output) if 'stderr_output' in locals() else ""
                self.error_occurred.emit(f"FFmpeg encoding failed: {err_str or str(e)}")
                return
            finally:
                print(f"[{time.strftime('%H:%M:%S')}] [Export] Closing FFmpeg stdin...")
                try:
                    process.stdin.close()
                except Exception as e:
                    print(f"[{time.strftime('%H:%M:%S')}] [Export] Error closing stdin: {e}")

                print(f"[{time.strftime('%H:%M:%S')}] [Export] Waiting for FFmpeg to finish...")
                try:
                    return_code = process.wait(timeout=10)
                    print(f"[{time.strftime('%H:%M:%S')}] [Export] FFmpeg exited with code {return_code}")
                    if return_code != 0:
                        if 'stderr_thread' in locals() and stderr_thread.is_alive():
                            stderr_thread.join(timeout=1.0)
                        err_str = "".join(stderr_output) if 'stderr_output' in locals() else ""
                        print(f"[{time.strftime('%H:%M:%S')}] [Export] FFmpeg error: {err_str[-500:]}")
                except subprocess.TimeoutExpired:
                    print(f"[{time.strftime('%H:%M:%S')}] [Export] FFmpeg timed out, killing...")
                    process.kill()
                    process.wait()
                except Exception as e:
                    print(f"[{time.strftime('%H:%M:%S')}] [Export] Error waiting for FFmpeg: {e}")

            if self._cancel:
                return

            # 3. Mux audio and video
            final_path = self.output_path
            actual_has_audio = has_audio and os.path.exists(audio_path) and os.path.getsize(audio_path) > 0
            if actual_has_audio:
                # Mix them
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
            else:
                import shutil
                shutil.copy(temp_video, final_path)

            self.export_complete.emit(self.output_path)

        except Exception as e:
            self.error_occurred.emit(str(e))
        finally:
            try:
                self.temp_dir_obj.cleanup()
            except Exception:
                pass
