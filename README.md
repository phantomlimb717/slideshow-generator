# Family Memories Slideshow Generator

A desktop application built with PySide6 that lets users create polished family-style slideshow videos from photos and video clips. The generator features Ken Burns effects, crossfade transitions, and background music mixing, with an intuitive drag-and-drop interface.

## Features

*   **Media Library & Drag-and-Drop:** Easily import photos (including HEIC), Live Photos, and video clips by dragging and dropping files or folders into the application.
*   **Live Photo Support:** Automatically detects iPhone Live Photos (paired `.HEIC` and `.MOV` files) and groups them as a single item. You can toggle between using the still image or the video clip in the slideshow.
*   **Ken Burns Effects:** Apply smooth, ease-in/ease-out panning and zooming effects to your photos. Choose from presets like "Slow Zoom In," "Pan Left to Right," or "Zoom In + Pan."
*   **Focal Point Cropping:** Ensure your subjects are never cropped out. All media is cropped to a perfect 16:9 widescreen ratio based on an adjustable (X, Y) focal point.
*   **Crossfade Transitions:** Smoothly crossfade between adjacent slides. You can set a global default crossfade duration or override it on a per-slide basis.
*   **Audio Mixing:**
    *   Add multiple backing music tracks that play sequentially.
    *   If the slideshow is longer than the music, the tracks loop seamlessly with a crossfade.
    *   Control the global music volume.
    *   For video clips, optionally mix in the original audio alongside the background music at a custom volume level.
*   **Video Trimming:** Set a custom "Trim Start" point for video clips to skip the beginning and play only the best part.
*   **Live Proxy Preview:** View a low-resolution proxy preview of your slideshow directly in the app. The preview auto-generates in the background whenever you make changes to the timeline or inspector properties.
*   **High-Quality Export:** Export the final slideshow to an MP4 file using FFmpeg. Choose from resolution presets (720p, 1080p, 4K), adjust the framerate (24, 30, 60 fps), and fine-tune the overall encoding quality.

## Requirements

*   **Python 3.10+**
*   **FFmpeg & FFprobe:** Must be installed and available in your system's PATH.

## Installation

1.  **Install FFmpeg:**
    *   **macOS:** `brew install ffmpeg`
    *   **Windows:** Download a pre-compiled binary from the official FFmpeg website and add it to your PATH, or use `winget install ffmpeg`.
    *   **Linux (Ubuntu/Debian):** `sudo apt-get install ffmpeg`

2.  **Install Python Dependencies:**
    Clone this repository and install the required packages using pip:

    ```bash
    pip install PySide6 Pillow pillow-heif pydub ffmpeg-python opencv-python numpy
    ```

## Usage

To launch the application, run the main script from the root of the project directory, ensuring the `src` folder is in your Python path:

```bash
export PYTHONPATH=$PYTHONPATH:$(pwd)/src
python src/main.py
```

### Workflow

1.  **Import Media:** Click "Add Folder / Files..." or drag and drop a folder into the Media Library panel on the left.
2.  **Build Timeline:** Drag items from the Media Library into the horizontal Timeline panel. You can drag to reorder slides.
3.  **Adjust Properties:** Select a slide in the Timeline to view its settings in the Inspector panel on the right. Here you can change the Ken Burns effect preset, starting zoom level, focal point, duration, and transition override.
4.  **Add Music:** Click "Add Audio..." to add backing tracks to the Audio Tracks panel. Drag to reorder them. Adjust the global music volume using the slider.
5.  **Preview:** The Live Preview player will automatically generate a low-resolution proxy of your slideshow. Use the Play/Pause button and scrubber to review the timing and effects.
6.  **Export:** Click "Export Slideshow" in the toolbar. Select your desired resolution, framerate, and quality, then choose an output file location.

## Architecture

The application is structured into modular components:

*   **`models/`**: Defines the core data structures (`Project`, `SlideItem`, `AudioItem`) and state management.
*   **`ui/`**: Contains the PySide6 views, custom widgets, drag-and-drop logic, and the main controller logic that ties everything together.
*   **`rendering/`**: Contains the `SlideshowRenderer` which handles image/video decoding, aspect ratio cropping, Ken Burns mathematical easing, and crossfade compositing using OpenCV and NumPy.
*   **`audio/`**: Uses Pydub to pre-assemble the complete backing track mix, handling sequencing, looping, fades, and overlaying video clip audio.
*   **`export/`**: Manages the final high-resolution rendering pipeline, piping frames to FFmpeg subprocesses for encoding and muxing.
*   **`utils/`**: Helper functions for scanning directories and generating thumbnails.
