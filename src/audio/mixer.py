import os
import math
import ffmpeg
from typing import Optional
from pydub import AudioSegment
from models.project import Project, SlideItem, MediaType

def build_audio_mix(project: Project, output_path: str) -> Optional[str]:
    """
    Assembles the complete audio track for the project and exports it to a WAV file.
    Handles backing track sequencing, looping, crossfading, volume adjustments,
    and mixing in original video audio at correct timestamps.
    """
    total_duration_ms = int(project.get_total_duration() * 1000)

    if total_duration_ms <= 0:
        return None

    # Start with silence for the entire duration
    final_mix = AudioSegment.silent(duration=total_duration_ms)

    # 1. Assemble Backing Track
    if project.audio_tracks:
        backing_track = AudioSegment.empty()

        # We need to process each track, apply individual volume, and add 5s fades.
        # Tracks are separated by a 3s gap of silence.
        fade_duration_ms = 5000
        gap_duration_ms = 3000
        silence_gap = AudioSegment.silent(duration=gap_duration_ms)

        processed_tracks = []

        for track in project.audio_tracks:
            try:
                segment = AudioSegment.from_file(track.file_path)

                # Apply individual track volume
                track_vol = max(0.01, track.volume)
                if track_vol != 1.0:
                    segment = segment + (20 * math.log10(track_vol))

                # Apply 5-second fade in and fade out
                # Make sure the segment is at least as long as the fades
                f_in = min(fade_duration_ms, len(segment))
                f_out = min(fade_duration_ms, len(segment))
                segment = segment.fade_in(f_in).fade_out(f_out)

                processed_tracks.append(segment)
            except Exception as e:
                print(f"Error loading audio track {track.file_path}: {e}")

        if processed_tracks:
            # Join tracks with silence gaps
            for i, segment in enumerate(processed_tracks):
                backing_track += segment
                if i < len(processed_tracks) - 1:
                    backing_track += silence_gap

            if len(backing_track) > 0:
                # Adjust global backing track volume
                vol = max(0.01, project.backing_track_volume) # avoid log(0)
                db_change = 20 * math.log10(vol)
                backing_track = backing_track + db_change

                current_backing = backing_track

                # Loop backing track if shorter than video and loop is enabled
                if project.loop_backing_track:
                    # Append until it covers the whole slideshow
                    while len(current_backing) < total_duration_ms:
                        # Append the gap, then the repeated backing track
                        current_backing += silence_gap
                        current_backing += backing_track

                # Trim to exact length
                current_backing = current_backing[:total_duration_ms]

                # Apply a 5-second fade-out to the very end of the slideshow
                fade_out_ms = min(5000, total_duration_ms)
                current_backing = current_backing.fade_out(fade_out_ms)

                # Overlay backing track onto the silent mix
                final_mix = final_mix.overlay(current_backing)

    # 2. Mix in Video Audio
    current_time_ms = 0
    for i, slide in enumerate(project.slides):
        is_video = slide.media_type == MediaType.VIDEO or (slide.media_type == MediaType.LIVE_PHOTO and slide.use_video_clip)
        effective_duration = slide.duration

        if is_video:
            media_path = slide.video_path if slide.media_type == MediaType.LIVE_PHOTO else slide.media_path
            try:
                probe = ffmpeg.probe(media_path)
                actual_clip_duration = None

                if 'format' in probe and 'duration' in probe['format']:
                    actual_clip_duration = float(probe['format']['duration'])
                else:
                    video_stream = next((s for s in probe['streams'] if s['codec_type'] == 'video'), None)
                    if video_stream is not None and 'duration' in video_stream:
                        actual_clip_duration = float(video_stream['duration'])

                if actual_clip_duration is not None:
                    effective_duration = min(slide.duration, max(0.0, actual_clip_duration - slide.trim_in))
            except Exception as e:
                print(f"Error probing video {media_path}: {e}")

        slide_dur_ms = int(effective_duration * 1000)

        if is_video and slide.include_audio:
            media_path = slide.video_path if slide.media_type == MediaType.LIVE_PHOTO else slide.media_path
            try:
                # Load video audio snippet
                trim_in_ms = int(slide.trim_in * 1000)
                video_audio = AudioSegment.from_file(media_path)

                # Trim the required portion
                snippet = video_audio[trim_in_ms : trim_in_ms + slide_dur_ms]

                # Apply volume adjustment
                vol = max(0.01, slide.audio_volume)
                db_change = 20 * math.log10(vol)
                snippet = snippet + db_change

                # Overlay at current time
                final_mix = final_mix.overlay(snippet, position=current_time_ms)

            except Exception as e:
                print(f"Error extracting audio from video {media_path}: {e}")

        # Advance current time, accounting for transition overlap
        current_time_ms += slide_dur_ms
        if i < len(project.slides) - 1:
            next_slide = project.slides[i+1]
            trans_dur = next_slide.transition_duration if next_slide.transition_duration is not None else project.global_transition_duration
            current_time_ms -= int(trans_dur * 1000)

    # Export final mix
    final_mix.export(output_path, format="wav")
    return output_path
