import uuid
from typing import Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

class MediaType(Enum):
    IMAGE = "IMAGE"
    VIDEO = "VIDEO"
    LIVE_PHOTO = "LIVE_PHOTO"

class EffectPreset(Enum):
    STATIC = "Static"
    ZOOM_IN = "Slow Zoom In"
    ZOOM_OUT = "Slow Zoom Out"
    PAN_LEFT_RIGHT = "Pan Left to Right"
    PAN_RIGHT_LEFT = "Pan Right to Left"
    PAN_UP = "Pan Up"
    PAN_DOWN = "Pan Down"
    ZOOM_IN_PAN = "Zoom In + Pan"

@dataclass
class SlideItem:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    media_path: str = ""
    media_type: MediaType = MediaType.IMAGE

    # For Live Photos
    video_path: Optional[str] = None
    use_video_clip: bool = False

    # Presentation properties
    duration: float = 5.0
    effect_preset: EffectPreset = EffectPreset.STATIC
    start_zoom: float = 1.0  # e.g., 1.0x = full frame, 1.5x = 50% zoomed in
    focal_point: Tuple[float, float] = (0.5, 0.5)  # normalized (x, y) coordinates

    # Video properties
    include_audio: bool = False
    audio_volume: float = 1.0  # 0.0 to 1.0
    trim_in: float = 0.0
    trim_out: Optional[float] = None  # None means go to the end

    # Transition override (None means use project global)
    transition_duration: Optional[float] = None

@dataclass
class AudioItem:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    file_path: str = ""
    duration: float = 0.0

@dataclass
class Project:
    slides: list[SlideItem] = field(default_factory=list)
    audio_tracks: list[AudioItem] = field(default_factory=list)
    global_transition_duration: float = 1.0
    output_resolution: Tuple[int, int] = (1920, 1080)
    target_fps: int = 30
    backing_track_volume: float = 1.0  # 0.0 to 1.0

    def get_total_duration(self) -> float:
        total = 0.0
        for i, slide in enumerate(self.slides):
            total += slide.duration
            if i > 0:
                trans_dur = slide.transition_duration if slide.transition_duration is not None else self.global_transition_duration
                total -= trans_dur
        return max(0.0, total)
