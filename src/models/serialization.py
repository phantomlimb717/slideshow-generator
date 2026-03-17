import json
import os
from typing import Dict, Any

from models.project import Project, SlideItem, AudioItem, MediaType, EffectPreset

def slide_item_to_dict(slide: SlideItem) -> Dict[str, Any]:
    return {
        "media_path": slide.media_path,
        "media_type": slide.media_type.value,
        "video_path": slide.video_path,
        "duration": slide.duration,
        "transition_duration": slide.transition_duration,
        "effect_preset": slide.effect_preset.value,
        "start_zoom": slide.start_zoom,
        "focal_point": slide.focal_point,
        "use_video_clip": slide.use_video_clip,
        "trim_in": slide.trim_in,
        "include_audio": slide.include_audio,
        "audio_volume": slide.audio_volume
    }

def dict_to_slide_item(data: Dict[str, Any]) -> SlideItem:
    return SlideItem(
        media_path=data["media_path"],
        media_type=MediaType(data["media_type"]),
        video_path=data.get("video_path"),
        duration=data["duration"],
        transition_duration=data.get("transition_duration"),
        effect_preset=EffectPreset(data["effect_preset"]),
        start_zoom=data.get("start_zoom", 1.0),
        focal_point=tuple(data.get("focal_point", (0.5, 0.5))),
        use_video_clip=data.get("use_video_clip", False),
        trim_in=data.get("trim_in", 0.0),
        include_audio=data.get("include_audio", False),
        audio_volume=data.get("audio_volume", 1.0)
    )

def audio_item_to_dict(audio: AudioItem) -> Dict[str, Any]:
    return {
        "file_path": audio.file_path,
        "duration": audio.duration
    }

def dict_to_audio_item(data: Dict[str, Any]) -> AudioItem:
    return AudioItem(
        file_path=data["file_path"],
        duration=data.get("duration", 0.0)
    )

def project_to_dict(project: Project) -> Dict[str, Any]:
    return {
        "slides": [slide_item_to_dict(s) for s in project.slides],
        "audio_tracks": [audio_item_to_dict(a) for a in project.audio_tracks],
        "global_transition_duration": project.global_transition_duration,
        "backing_track_volume": project.backing_track_volume,
        "output_resolution": project.output_resolution,
        "target_fps": project.target_fps
    }

def dict_to_project(data: Dict[str, Any]) -> Project:
    project = Project()
    project.slides = [dict_to_slide_item(s) for s in data.get("slides", [])]
    project.audio_tracks = [dict_to_audio_item(a) for a in data.get("audio_tracks", [])]
    project.global_transition_duration = data.get("global_transition_duration", 1.0)
    project.backing_track_volume = data.get("backing_track_volume", 1.0)
    project.output_resolution = tuple(data.get("output_resolution", (1920, 1080)))
    project.target_fps = data.get("target_fps", 30)
    return project

def save_project(project: Project, file_path: str):
    """Serializes the Project to JSON and writes to file_path."""
    project_dict = project_to_dict(project)
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(project_dict, f, indent=4)

def load_project(file_path: str) -> tuple[Project, list[str]]:
    """
    Reads JSON from file_path, returns a Project and a list of missing files.
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    project = dict_to_project(data)

    # Validate paths
    missing_files = []

    for slide in project.slides:
        if not os.path.exists(slide.media_path):
            missing_files.append(slide.media_path)
        if slide.video_path and not os.path.exists(slide.video_path):
            missing_files.append(slide.video_path)

    for audio in project.audio_tracks:
        if not os.path.exists(audio.file_path):
            missing_files.append(audio.file_path)

    # Remove duplicates from missing list while preserving order
    seen = set()
    unique_missing = []
    for f in missing_files:
        if f not in seen:
            unique_missing.append(f)
            seen.add(f)

    return project, unique_missing