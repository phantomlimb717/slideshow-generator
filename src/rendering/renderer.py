import cv2
import numpy as np
import ffmpeg
from typing import List, Tuple, Optional, Generator
from PIL import Image, ImageOps
from models.project import Project, SlideItem, EffectPreset, MediaType

def ease_in_out(t: float) -> float:
    """Cubic ease-in-out function for smooth animations."""
    if t < 0.5:
        return 4 * t * t * t
    else:
        f = ((2 * t) - 2)
        return 0.5 * f * f * f + 1

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

    def _get_video_frames(self, path: str, start_time: float, duration: float) -> List[np.ndarray]:
        """Extracts frames from a video clip."""
        frames = []
        try:
            # First use ffprobe to get native video dimensions and orientation
            probe = ffmpeg.probe(path)
            video_stream = next((s for s in probe['streams'] if s['codec_type'] == 'video'), None)
            width = int(video_stream['width'])
            height = int(video_stream['height'])

            # Check for rotation in metadata (ffmpeg automatically rotates video, so we must swap dimensions if rotated 90 or 270 degrees)
            tags = video_stream.get('tags', {})
            rotate = tags.get('rotate', '0')
            side_data = video_stream.get('side_data_list', [])
            for sd in side_data:
                if 'rotation' in sd:
                    rotate = str(abs(int(sd['rotation'])))

            if rotate in ['90', '270', '-90']:
                width, height = height, width

            # We add a buffer to ensure we get enough frames
            out, _ = (
                ffmpeg
                .input(path, ss=start_time, t=duration + 0.5)
                .output('pipe:', format='rawvideo', pix_fmt='rgb24', r=self.fps)
                .run(capture_stdout=True, capture_stderr=True)
            )

            # Reconstruct numpy array
            video = np.frombuffer(out, np.uint8).reshape([-1, height, width, 3])

            # Limit to required frames
            num_frames = int(duration * self.fps)
            frames = list(video[:num_frames])

        except ffmpeg.Error as e:
            print(f"Error decoding video {path}: {e.stderr.decode()}")
        except Exception as e:
             print(f"Error decoding video {path}: {e}")

        # Pad with black frames if needed
        req_frames = int(duration * self.fps)
        while len(frames) < req_frames:
            frames.append(np.zeros((self.resolution[1], self.resolution[0], 3), dtype=np.uint8))

        return frames

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
            z1, z2 = max(1.1, start_zoom), max(1.1, start_zoom)
            ox1, oy1, ox2, oy2 = 0.4, 0.5, 0.6, 0.5
        elif effect == EffectPreset.PAN_RIGHT_LEFT:
            z1, z2 = max(1.1, start_zoom), max(1.1, start_zoom)
            ox1, oy1, ox2, oy2 = 0.6, 0.5, 0.4, 0.5
        elif effect == EffectPreset.PAN_UP:
            z1, z2 = max(1.1, start_zoom), max(1.1, start_zoom)
            ox1, oy1, ox2, oy2 = 0.5, 0.6, 0.5, 0.4
        elif effect == EffectPreset.PAN_DOWN:
            z1, z2 = max(1.1, start_zoom), max(1.1, start_zoom)
            ox1, oy1, ox2, oy2 = 0.5, 0.4, 0.5, 0.6
        elif effect == EffectPreset.ZOOM_IN_PAN:
            z1 = start_zoom
            z2 = start_zoom * 1.3
            ox1, oy1, ox2, oy2 = 0.4, 0.4, 0.6, 0.6
        else:
            z1, z2 = start_zoom, start_zoom
            ox1, oy1, ox2, oy2 = 0.5, 0.5, 0.5, 0.5

        # Interpolate using ease function
        eased_p = ease_in_out(progress)
        current_zoom = z1 + (z2 - z1) * eased_p
        cx = ox1 + (ox2 - ox1) * eased_p
        cy = oy1 + (oy2 - oy1) * eased_p

        # Calculate crop window
        crop_w = int(w / current_zoom)
        crop_h = int(h / current_zoom)

        x1 = int(w * cx - crop_w / 2)
        y1 = int(h * cy - crop_h / 2)

        # Clamp bounds
        x1 = max(0, min(x1, w - crop_w))
        y1 = max(0, min(y1, h - crop_h))
        x2 = x1 + crop_w
        y2 = y1 + crop_h

        cropped = img[y1:y2, x1:x2]

        # Resize to target resolution
        resized = cv2.resize(cropped, self.resolution, interpolation=cv2.INTER_LANCZOS4)
        return resized

    def generate_slide_frames(self, slide: SlideItem) -> List[np.ndarray]:
        """Generates all fully rendered frames for a single slide."""
        num_frames = int(slide.duration * self.fps)
        frames = []

        media_path = slide.video_path if (slide.media_type == MediaType.LIVE_PHOTO and slide.use_video_clip) else slide.media_path

        is_video = slide.media_type == MediaType.VIDEO or (slide.media_type == MediaType.LIVE_PHOTO and slide.use_video_clip)

        if is_video:
            # Video handling
            trim_in = slide.trim_in
            raw_frames = self._get_video_frames(media_path, trim_in, slide.duration)

            for i, frame in enumerate(raw_frames):
                if i >= num_frames:
                    break
                # Apply crop to aspect and ken burns (even to video if desired, though usually static)
                # For videos, we might just crop to aspect to fit the screen
                cropped = self._crop_to_aspect(frame, slide.focal_point[0], slide.focal_point[1])
                progress = i / max(1, num_frames - 1)
                final_frame = self._apply_ken_burns(cropped, progress, slide)
                frames.append(final_frame)

            # Do NOT pad if short - cap the duration instead
            if len(frames) < num_frames:
                # Update duration to actual length so timing doesn't get messed up later
                slide.duration = len(frames) / self.fps
        else:
            # Image handling
            raw_img = self._get_image_data(media_path)
            cropped = self._crop_to_aspect(raw_img, slide.focal_point[0], slide.focal_point[1])

            for i in range(num_frames):
                progress = i / max(1, num_frames - 1)
                final_frame = self._apply_ken_burns(cropped, progress, slide)
                frames.append(final_frame)

        return frames

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
        prev_slide_tail = []

        for i, slide in enumerate(self.project.slides):
            slide_frames = self.generate_slide_frames(slide)

            if i > 0:
                trans_dur = slide.transition_duration if slide.transition_duration is not None else self.project.global_transition_duration
                trans_frames_count = int(trans_dur * self.fps)

                # Ensure we don't fade longer than the slide itself
                trans_frames_count = min(trans_frames_count, len(slide_frames), len(prev_slide_tail))

                # Do crossfade
                for j in range(trans_frames_count):
                    alpha = (j + 1) / (trans_frames_count + 1)

                    frame1 = prev_slide_tail[j]
                    frame2 = slide_frames[j]

                    # Blend
                    blended = cv2.addWeighted(frame1, 1 - alpha, frame2, alpha, 0)
                    yield current_frame_idx, total_frames, blended
                    current_frame_idx += 1
            else:
                # First slide, no crossfade in
                trans_frames_count = 0

            # Prepare tail for next slide transition
            if i < len(self.project.slides) - 1:
                next_slide = self.project.slides[i+1]
                next_trans_dur = next_slide.transition_duration if next_slide.transition_duration is not None else self.project.global_transition_duration
                next_trans_frames_count = int(next_trans_dur * self.fps)

                # Cannot crossfade longer than this slide's *remaining* length
                remaining_frames = len(slide_frames) - trans_frames_count
                next_trans_frames_count = min(next_trans_frames_count, remaining_frames)

                # The remainder of the current slide before the next crossfade starts
                output_frames = slide_frames[trans_frames_count:-next_trans_frames_count] if next_trans_frames_count > 0 else slide_frames[trans_frames_count:]

                # Yield the middle portion of the slide
                for frame in output_frames:
                    yield current_frame_idx, total_frames, frame
                    current_frame_idx += 1

                # Save the tail for the next crossfade
                prev_slide_tail = slide_frames[-next_trans_frames_count:] if next_trans_frames_count > 0 else []
            else:
                # Last slide, output everything remaining
                for frame in slide_frames[trans_frames_count:]:
                    yield current_frame_idx, total_frames, frame
                    current_frame_idx += 1
