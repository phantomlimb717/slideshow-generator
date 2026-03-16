import os
import glob
from typing import List, Optional, Tuple
from pathlib import Path
from PIL import Image
import pillow_heif
import ffmpeg

from models.project import SlideItem, MediaType, AudioItem, Project

# Register HEIF opener with Pillow
pillow_heif.register_heif_opener()

def get_video_info(video_path: str) -> dict:
    try:
        probe = ffmpeg.probe(video_path)
        video_stream = next((stream for stream in probe['streams'] if stream['codec_type'] == 'video'), None)
        if video_stream is None:
            return {}

        duration = float(video_stream.get('duration', probe.get('format', {}).get('duration', 0.0)))
        has_audio = any(stream['codec_type'] == 'audio' for stream in probe['streams'])
        return {'duration': duration, 'has_audio': has_audio}
    except Exception as e:
        print(f"Error probing video {video_path}: {e}")
        return {}

def scan_directory_for_media(directory: str) -> List[SlideItem]:
    """Scans a directory for images, videos, and Live Photos."""
    path = Path(directory)

    # Supported extensions
    img_exts = {'.jpg', '.jpeg', '.png', '.tiff', '.bmp', '.webp', '.heic', '.heif'}
    vid_exts = {'.mp4', '.mov', '.avi', '.mkv'}

    # Sort files alphabetically (case-insensitive) for deterministic import order
    all_files = sorted(list(path.iterdir()), key=lambda x: x.name.lower())

    slides = []
    processed_bases = set()

    for file in all_files:
        if not file.is_file():
            continue

        ext = file.suffix.lower()
        base_name = file.stem

        if base_name in processed_bases:
            continue

        is_img = ext in img_exts
        is_vid = ext in vid_exts

        if is_img or is_vid:
            # Check for Live Photo pair
            potential_pair_exts = vid_exts if is_img else img_exts
            pair_file = None

            for p_ext in potential_pair_exts:
                candidate = path / f"{base_name}{p_ext}"
                if candidate.exists() and candidate.is_file():
                    pair_file = candidate
                    break
                # Also check uppercase extensions
                candidate_upper = path / f"{base_name}{p_ext.upper()}"
                if candidate_upper.exists() and candidate_upper.is_file():
                    pair_file = candidate_upper
                    break

            if pair_file:
                # We have a Live Photo
                img_path = file if is_img else pair_file
                vid_path = pair_file if is_img else file

                vid_info = get_video_info(str(vid_path))
                slide = SlideItem(
                    media_path=str(img_path),
                    media_type=MediaType.LIVE_PHOTO,
                    video_path=str(vid_path),
                    duration=vid_info.get('duration', 3.0),
                    include_audio=vid_info.get('has_audio', False)
                )
                slides.append(slide)
                processed_bases.add(base_name)
            else:
                # Standalone media
                if is_img:
                    slide = SlideItem(
                        media_path=str(file),
                        media_type=MediaType.IMAGE,
                        duration=5.0
                    )
                    slides.append(slide)
                elif is_vid:
                    vid_info = get_video_info(str(file))
                    slide = SlideItem(
                        media_path=str(file),
                        media_type=MediaType.VIDEO,
                        duration=vid_info.get('duration', 5.0),
                        include_audio=vid_info.get('has_audio', False)
                    )
                    slides.append(slide)

    return slides

def extract_thumbnail(media_path: str, size: Tuple[int, int] = (160, 90)) -> Optional[Image.Image]:
    """Generates a thumbnail for an image or video."""
    ext = Path(media_path).suffix.lower()

    try:
        if ext in {'.mp4', '.mov', '.avi', '.mkv'}:
            # Extract frame from video using ffmpeg
            out, _ = (
                ffmpeg
                .input(media_path, ss=1.0) # seek 1 second in to avoid black frames
                .filter('scale', size[0], -1)
                .output('pipe:', vframes=1, format='image2', vcodec='mjpeg')
                .run(capture_stdout=True, capture_stderr=True, quiet=True)
            )
            import io
            img = Image.open(io.BytesIO(out))
            img.thumbnail(size)
            return img
        else:
            # Image handling (Pillow handles HEIC via pillow_heif plugin)
            img = Image.open(media_path)
            # Handle orientation EXIF tag if present
            from PIL import ImageOps
            img = ImageOps.exif_transpose(img)

            img.thumbnail(size)
            return img
    except Exception as e:
        print(f"Error extracting thumbnail for {media_path}: {e}")
        return None
