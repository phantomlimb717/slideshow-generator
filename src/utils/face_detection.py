import numpy as np
from PIL import Image, ImageOps
from typing import Optional, Tuple, List
import os
import math
import time

# Lazy-load insightface to avoid slow import on startup
_face_analyzer = None

def _get_analyzer():
    """Lazy-initialize the face analyzer with GPU if available."""
    global _face_analyzer
    if _face_analyzer is None:
        import insightface
        from insightface.app import FaceAnalysis

        print("[FaceDetect] Downloading face detection model (first run only, ~300MB)...")
        # Use buffalo_l model — good balance of speed and accuracy
        _face_analyzer = FaceAnalysis(
            name='buffalo_l',
            providers=['CUDAExecutionProvider', 'CPUExecutionProvider']
        )
        # det_size controls detection resolution — smaller = faster
        _face_analyzer.prepare(ctx_id=0, det_size=(640, 640))
        print(f"[FaceDetect] Initialized with providers: {_face_analyzer.models['detection'].session.get_providers()}")

    return _face_analyzer

def detect_faces_in_image(image_path: str, max_dimension: int = 800) -> List[dict]:
    """
    Detect faces in an image. Returns list of dicts with:
    - 'bbox': (x1, y1, x2, y2) in pixel coordinates of the original image
    - 'center': (cx, cy) normalized 0-1 coordinates
    - 'area': relative area of face bbox vs image (for picking the "main" face)
    - 'embedding': face embedding vector (for Tier 2 matching later)
    - 'edge_touching': True if bbox touches image boundary
    """
    try:
        pil_img = Image.open(image_path)
        pil_img = ImageOps.exif_transpose(pil_img)
        pil_img = pil_img.convert('RGB')

        orig_w, orig_h = pil_img.size

        # Downscale for faster detection
        scale = min(max_dimension / max(orig_w, orig_h), 1.0)
        if scale < 1.0:
            new_w = int(orig_w * scale)
            new_h = int(orig_h * scale)
            pil_img = pil_img.resize((new_w, new_h), Image.LANCZOS)
        else:
            new_w, new_h = orig_w, orig_h

        # insightface expects BGR numpy array
        img_array = np.array(pil_img)[:, :, ::-1]

        analyzer = _get_analyzer()
        faces = analyzer.get(img_array)

        results = []
        edge_threshold = 5  # pixels

        for face in faces:
            # Scale bbox back to original image coordinates
            x1, y1, x2, y2 = face.bbox
            x1 = float(x1 / scale)
            y1 = float(y1 / scale)
            x2 = float(x2 / scale)
            y2 = float(y2 / scale)

            # Normalized center (0-1)
            cx = ((x1 + x2) / 2) / orig_w
            cy = ((y1 + y2) / 2) / orig_h

            # Relative area
            face_area = (x2 - x1) * (y2 - y1)
            image_area = orig_w * orig_h
            area = face_area / image_area

            # Check if face touches image edge
            edge_touching = (
                x1 / scale < edge_threshold or
                y1 / scale < edge_threshold or
                x2 / scale > new_w - edge_threshold or
                y2 / scale > new_h - edge_threshold
            )

            results.append({
                'bbox': (x1, y1, x2, y2),
                'center': (cx, cy),
                'area': area,
                'embedding': face.embedding if hasattr(face, 'embedding') else None,
                'edge_touching': edge_touching
            })

        # Sort by area descending — biggest face first
        results.sort(key=lambda f: f['area'], reverse=True)
        return results

    except Exception as e:
        print(f"[FaceDetect] Error processing {image_path}: {e}")
        return []


def compare_faces(embedding1, embedding2) -> float:
    """
    Compare two face embeddings. Returns cosine similarity (0-1).
    Higher = more similar. Same person typically > 0.5.
    """
    e1 = np.array(embedding1)
    e2 = np.array(embedding2)
    e1 = e1 / np.linalg.norm(e1)
    e2 = e2 / np.linalg.norm(e2)
    return float(np.dot(e1, e2))


def calculate_smart_zoom(focal_x: float, focal_y: float, face_area: float, max_auto_zoom: float = 1.5,
                         is_specific_person: bool = False) -> float:
    """
    Calculate a zoom level that keeps the face well-framed.

    - focal_x, focal_y: normalized face center (0-1)
    - face_area: relative area of face vs image
    - max_auto_zoom: cap on automatic zoom
    - is_specific_person: If true, uses a tighter, more aggressive zoom.
    """
    # Since the renderer clamps automatically, we don't need to force a minimum zoom.
    # The previous logic was fundamentally flawed in its naming and purpose.
    # We actually WANT the crop to be clamped if the user is on the edge, because
    # zooming in 10x just to avoid clamping is ridiculous.
    # What we actually need is just a cap to prevent insane zooming if the face is too close to the edge.
    # So we simply use `max_auto_zoom` as our absolute cap.

    # Start with a desired zoom based on face size
    if is_specific_person:
        # Mathematical zoom targeting a specific face area on screen (~10%)
        # This provides a smooth, proportional zoom instead of rigid thresholds
        target_face_area = 0.10

        # Prevent division by zero if face_area is somehow 0
        safe_face_area = max(face_area, 0.001)

        # Calculate proportional zoom to make face ~10% of screen
        # Since zoom scales both width and height, we use square root of area ratio
        desired_zoom = math.sqrt(target_face_area / safe_face_area)
    else:
        # Standard conservative zoom
        if face_area > 0.1:
            desired_zoom = 1.0      # Face is already big — don't zoom
        elif face_area > 0.03:
            desired_zoom = 1.2      # Medium face — slight zoom
        else:
            desired_zoom = 1.4      # Small face — zoom in to make it larger

    # Ensure max_auto_zoom doesn't exceed 1.25 for any case as requested
    max_auto_zoom = min(max_auto_zoom, 1.25)

    # Clamp to the safe range
    auto_zoom = min(desired_zoom, max_auto_zoom)
    return max(1.0, auto_zoom)
