import cv2
import numpy as np
import ffmpeg
import math
import threading
from typing import List, Tuple, Optional, Generator, Iterator
from collections import deque
from PIL import Image, ImageOps
from models.project import Project, SlideItem, EffectPreset, MediaType

def ease_in_out(t: float) -> float:
    """Sine-based ease-in-out for smoother, more visible motion."""
    return 0.5 * (1 - math.cos(math.pi * t))

class SlideshowRenderer:
    def __init__(self, project: Project, fps: int = 30, resolution: Tuple[int, int] = (1920, 1080)):
        self.project = project
        self.fps = fps
        self.resolution = resolution
        self.width, self.height = resolution
        self.target_aspect_ratio = self.width / self.height

    def _get_image_data(self, path: str) -> np.ndarray:
        """Loads an image (including HEIC), applies EXIF orientation, and returns an RGB numpy array."""
        try:
            pil_img = Image.open(path)
            pil_img = ImageOps.exif_transpose(pil_img)
            # Convert to RGB mode (discard alpha if present)
            pil_img = pil_img.convert('RGB')
            # Convert to OpenCV BGR format then to RGB (so it matches video frames)
            img = np.array(pil_img)
            return img
        except Exception as e:
            print(f"Error loading image {path}: {e}")
            # Return black frame on error
            return np.zeros((self.height, self.width, 3), dtype=np.uint8)

    def _get_video_frames(self, path: str, start_time: float, duration: float) -> Generator[np.ndarray, None, None]:
        """Extracts frames from a video clip sequentially."""
        width, height = self.resolution
        req_frames = int(duration * self.fps)
        frames_yielded = 0

        try:
            # First use ffprobe to get native video dimensions and orientation
            probe = ffmpeg.probe(path)
            video_stream = next((s for s in probe['streams'] if s['codec_type'] == 'video'), None)

            if video_stream is not None:
                width = int(video_stream['width'])
                height = int(video_stream['height'])

                # Check for rotation in metadata
                tags = video_stream.get('tags', {})
                rotate = tags.get('rotate', '0')
                side_data = video_stream.get('side_data_list', [])
                for sd in side_data:
                    if 'rotation' in sd:
                        rotate = str(abs(int(sd['rotation'])))

                if rotate in ['90', '270', '-90']:
                    width, height = height, width

            # Create an ffmpeg process to read frames sequentially
            stderr_output = []
            process = (
                ffmpeg
                .input(path, ss=start_time, t=duration + 0.5)
                .output('pipe:', format='rawvideo', pix_fmt='rgb24', r=self.fps)
                .run_async(pipe_stdout=True, pipe_stderr=True)
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

            frame_size = width * height * 3

            while frames_yielded < req_frames:
                in_bytes = process.stdout.read(frame_size)
                if not in_bytes:
                    break

                frame = np.frombuffer(in_bytes, np.uint8).reshape([height, width, 3])
                yield frame
                frames_yielded += 1

        except ffmpeg.Error as e:
            if 'stderr_thread' in locals() and stderr_thread.is_alive():
                stderr_thread.join(timeout=1.0)
            err_str = "".join(stderr_output) if 'stderr_output' in locals() else ""
            print(f"Error decoding video {path}: {err_str or str(e)}")
        except Exception as e:
             if 'stderr_thread' in locals() and stderr_thread.is_alive():
                 stderr_thread.join(timeout=1.0)
             err_str = "".join(stderr_output) if 'stderr_output' in locals() else ""
             print(f"Error decoding video {path}: {e}\nFFmpeg output: {err_str[-500:]}")
        finally:
            if 'process' in locals():
                try:
                    process.stdout.close()
                    process.wait()
                except Exception:
                    pass

        # Pad with black frames if needed
        while frames_yielded < req_frames:
            yield np.zeros((self.resolution[1], self.resolution[0], 3), dtype=np.uint8)
            frames_yielded += 1

    def _crop_to_aspect(self, img: np.ndarray, focal_x: float, focal_y: float) -> np.ndarray:
        """Crops an image to the target 16:9 aspect ratio based on focal point."""
        h, w = img.shape[:2]
        img_aspect = w / h

        if img_aspect > self.target_aspect_ratio:
            # Image is wider than target: crop width
            new_w = int(h * self.target_aspect_ratio)
            # Calculate crop boundaries ensuring we don't go out of bounds
            center_x = int(w * focal_x)
            x1 = max(0, center_x - new_w // 2)
            x2 = x1 + new_w

            if x2 > w:
                x2 = w
                x1 = w - new_w

            return img[:, x1:x2]
        elif img_aspect < self.target_aspect_ratio:
            # Image is taller than target: crop height
            new_h = int(w / self.target_aspect_ratio)
            center_y = int(h * focal_y)
            y1 = max(0, center_y - new_h // 2)
            y2 = y1 + new_h

            if y2 > h:
                y2 = h
                y1 = h - new_h

            return img[y1:y2, :]
        return img

    def _apply_ken_burns(self, img: np.ndarray, progress: float, slide: SlideItem) -> np.ndarray:
        """Applies Ken Burns effect based on preset, starting zoom, and progress (0.0 to 1.0)."""
        h, w = img.shape[:2]

        effect = slide.effect_preset
        start_zoom = slide.start_zoom

        # Determine start and end zoom factors and offsets
        # Default full frame is zoom 1.0. A zoom > 1.0 means we see a smaller portion of the image.

        # Define effect parameters based on preset
        if effect == EffectPreset.STATIC:
            z1, z2 = start_zoom, start_zoom
            ox1, oy1, ox2, oy2 = 0.5, 0.5, 0.5, 0.5
        elif effect == EffectPreset.ZOOM_IN:
            z1 = start_zoom
            z2 = start_zoom * 1.2
            ox1, oy1, ox2, oy2 = 0.5, 0.5, 0.5, 0.5
        elif effect == EffectPreset.ZOOM_OUT:
            z1 = start_zoom * 1.2
            z2 = start_zoom
            ox1, oy1, ox2, oy2 = 0.5, 0.5, 0.5, 0.5
        elif effect == EffectPreset.PAN_LEFT_RIGHT:
            z1, z2 = max(1.5, start_zoom), max(1.5, start_zoom)
            ox1, oy1, ox2, oy2 = 0.33, 0.5, 0.67, 0.5
        elif effect == EffectPreset.PAN_RIGHT_LEFT:
            z1, z2 = max(1.5, start_zoom), max(1.5, start_zoom)
            ox1, oy1, ox2, oy2 = 0.67, 0.5, 0.33, 0.5
        elif effect == EffectPreset.PAN_UP:
            z1, z2 = max(1.5, start_zoom), max(1.5, start_zoom)
            ox1, oy1, ox2, oy2 = 0.5, 0.67, 0.5, 0.33
        elif effect == EffectPreset.PAN_DOWN:
            z1, z2 = max(1.5, start_zoom), max(1.5, start_zoom)
            ox1, oy1, ox2, oy2 = 0.5, 0.33, 0.5, 0.67
        elif effect == EffectPreset.ZOOM_IN_PAN:
            z1 = start_zoom
            z2 = start_zoom * 1.3
            ox1, oy1, ox2, oy2 = 0.35, 0.35, 0.65, 0.65
        else:
            z1, z2 = start_zoom, start_zoom
            ox1, oy1, ox2, oy2 = 0.5, 0.5, 0.5, 0.5

        # Interpolate using ease function
        eased_p = ease_in_out(progress)
        current_zoom = z1 + (z2 - z1) * eased_p
        cx = ox1 + (ox2 - ox1) * eased_p
        cy = oy1 + (oy2 - oy1) * eased_p

        # Calculate crop window with floating point precision to avoid jitter
        crop_w = w / current_zoom
        crop_h = h / current_zoom

        x1 = w * cx - crop_w / 2.0
        y1 = h * cy - crop_h / 2.0

        # Clamp bounds
        x1 = max(0.0, min(x1, w - crop_w))
        y1 = max(0.0, min(y1, h - crop_h))

        # Use an affine transformation for sub-pixel interpolation instead of integer array slicing
        dst_w, dst_h = self.resolution
        scale_x = dst_w / crop_w
        scale_y = dst_h / crop_h
        tx = -x1 * scale_x
        ty = -y1 * scale_y

        M = np.array([
            [scale_x, 0, tx],
            [0, scale_y, ty]
        ], dtype=np.float32)

        # Warp the image directly to the target resolution using Lanczos interpolation
        resized = cv2.warpAffine(img, M, (dst_w, dst_h), flags=cv2.INTER_LANCZOS4)
        return resized

    def generate_slide_frames(self, slide: SlideItem, fade_in_duration: float = 0.0, fade_out_duration: float = 0.0) -> Generator[np.ndarray, None, None]:
        import time
        """Generates all fully rendered frames for a single slide sequentially."""
        print(f"[{time.strftime('%H:%M:%S')}] [Renderer] Processing slide: {slide.media_path} ({slide.media_type.value}, {slide.duration}s, effect={slide.effect_preset.value})")

        num_frames = int(slide.duration * self.fps)
        total_animation_duration = fade_in_duration + slide.duration + fade_out_duration

        media_path = slide.video_path if (slide.media_type == MediaType.LIVE_PHOTO and slide.use_video_clip) else slide.media_path

        is_video = slide.media_type == MediaType.VIDEO or (slide.media_type == MediaType.LIVE_PHOTO and slide.use_video_clip)

        if is_video:
            # Video handling
            trim_in = slide.trim_in
            frame_gen = self._get_video_frames(media_path, trim_in, slide.duration)

            yielded_frames = 0
            for i, frame in enumerate(frame_gen):
                if i % 30 == 0:
                    print(f"[{time.strftime('%H:%M:%S')}] [Renderer] Extracting video frame {i}/{num_frames} from {media_path}")

                if i >= num_frames:
                    break
                # Apply crop to aspect and ken burns (even to video if desired, though usually static)
                # For videos, we might just crop to aspect to fit the screen
                cropped = self._crop_to_aspect(frame, slide.focal_point[0], slide.focal_point[1])
                elapsed = fade_in_duration + (i / self.fps)
                progress = elapsed / total_animation_duration if total_animation_duration > 0 else 0.0
                final_frame = self._apply_ken_burns(cropped, progress, slide)
                yield final_frame
                yielded_frames += 1

            # Do NOT pad if short - cap the duration instead
        else:
            # Image handling
            raw_img = self._get_image_data(media_path)
            print(f"[{time.strftime('%H:%M:%S')}] [Renderer] Image loaded: {raw_img.shape}")
            cropped = self._crop_to_aspect(raw_img, slide.focal_point[0], slide.focal_point[1])

            for i in range(num_frames):
                elapsed = fade_in_duration + (i / self.fps)
                progress = elapsed / total_animation_duration if total_animation_duration > 0 else 0.0
                final_frame = self._apply_ken_burns(cropped, progress, slide)
                yield final_frame

    def render_project(self) -> Generator[Tuple[int, int, np.ndarray], None, None]:
        """
        Yields frames sequentially.
        Yields: (current_frame_index, total_frames, frame_data)
        """
        if not self.project.slides:
            return

        total_duration = self.project.get_total_duration()
        total_frames = int(total_duration * self.fps)

        current_frame_idx = 0

        # Buffer for the previous slide's trailing frames (for crossfade)
        prev_slide_tail = deque()

        for i, slide in enumerate(self.project.slides):
            # Calculate incoming transition duration for this slide
            fade_in = 0.0
            if i > 0:
                fade_in = slide.transition_duration if slide.transition_duration is not None else self.project.global_transition_duration

            # Calculate outgoing transition duration for this slide
            fade_out = 0.0
            if i < len(self.project.slides) - 1:
                next_slide = self.project.slides[i + 1]
                fade_out = next_slide.transition_duration if next_slide.transition_duration is not None else self.project.global_transition_duration

            slide_frame_gen = self.generate_slide_frames(slide, fade_in_duration=fade_in, fade_out_duration=fade_out)

            # Determine incoming transition frames count
            trans_frames_count = 0
            if i > 0:
                trans_dur = slide.transition_duration if slide.transition_duration is not None else self.project.global_transition_duration
                trans_frames_count = int(trans_dur * self.fps)

            # Make sure we don't request more transition frames than exist in the tail
            trans_frames_count = min(trans_frames_count, len(prev_slide_tail))

            # Determine outgoing transition frames count
            next_trans_frames_count = 0
            if i < len(self.project.slides) - 1:
                next_slide = self.project.slides[i+1]
                next_trans_dur = next_slide.transition_duration if next_slide.transition_duration is not None else self.project.global_transition_duration
                next_trans_frames_count = int(next_trans_dur * self.fps)

            # To avoid loading all frames, we still need to stream.
            # However, we don't know the exact length of the stream if it's a short video.
            # We can use a rolling buffer approach if needed, or pre-calculate duration.
            # But the requirement is to keep memory low, so we should NOT use `list(slide_frame_gen)`.

            # We will use a queue/deque for outgoing tail, and we just iterate through frame_gen.
            # Since we must apply crossfade to the *first* trans_frames_count frames, we can do that directly.

            frames_yielded_this_slide = 0
            # Buffer for frames that might be part of the *next* transition
            # If we don't know the end, we just keep the last `next_trans_frames_count` frames
            new_tail = deque(maxlen=next_trans_frames_count)

            for frame in slide_frame_gen:
                if frames_yielded_this_slide < trans_frames_count:
                    # We are in the crossfade region with the previous slide
                    alpha = (frames_yielded_this_slide + 1) / (trans_frames_count + 1)
                    frame1 = prev_slide_tail[frames_yielded_this_slide]
                    frame2 = frame
                    blended = cv2.addWeighted(frame1, 1 - alpha, frame2, alpha, 0)
                    yield current_frame_idx, total_frames, blended
                    current_frame_idx += 1
                else:
                    # We are past the incoming crossfade.
                    # This frame might be middle frame or outgoing tail.
                    # We only yield frames that are pushed out of the tail deque.
                    if next_trans_frames_count == 0:
                        # No outgoing transition, yield immediately
                        yield current_frame_idx, total_frames, frame
                        current_frame_idx += 1
                    else:
                        # Push to tail deque. If it's full, the oldest frame is pushed out and we yield it.
                        if len(new_tail) == next_trans_frames_count:
                            oldest_frame = new_tail[0]
                            yield current_frame_idx, total_frames, oldest_frame
                            current_frame_idx += 1
                        new_tail.append(frame)

                frames_yielded_this_slide += 1

            # If this is the last slide, we must also yield whatever is left in the tail,
            # because there will be no next slide to crossfade with.
            if i == len(self.project.slides) - 1:
                for frame in new_tail:
                    yield current_frame_idx, total_frames, frame
                    current_frame_idx += 1
            else:
                # If the slide was shorter than expected (e.g. short video) and we didn't fill the tail,
                # we must yield out frames until the tail has exactly next_trans_frames_count frames.
                # However, new_tail is a deque with maxlen=next_trans_frames_count.
                # So if it's shorter, it means the entire non-crossfade portion of the slide
                # is now in the tail. The next slide will crossfade with whatever is in the tail.
                # But wait, next slide assumes prev_slide_tail has `next_trans_frames_count` items.
                # Let's make sure it's correct. We just update prev_slide_tail to be a list
                # of whatever actually ended up in new_tail.
                pass

            # Update the tail for the next slide
            prev_slide_tail = list(new_tail)
