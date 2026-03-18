import numpy as np
from PIL import Image, ImageOps
from typing import Optional, Tuple, List
import os
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


def calculate_smart_zoom(focal_x: float, focal_y: float, face_area: float, max_auto_zoom: float = 1.5) -> float:
    """
    Calculate a zoom level that keeps the face well-framed without the crop
    going off the edge of the image.

    - focal_x, focal_y: normalized face center (0-1)
    - face_area: relative area of face vs image
    - max_auto_zoom: cap on automatic zoom
    """
    # How far is the face from the nearest edge?
    edge_margin_x = min(focal_x, 1.0 - focal_x)
    edge_margin_y = min(focal_y, 1.0 - focal_y)
    edge_margin = min(edge_margin_x, edge_margin_y)

    # Maximum zoom where the face stays centered without clamping
    # At zoom z, visible window is 1/z of the image
    # Edge of visible area is at focal ± 0.5/z
    # To avoid clamping: 0.5/z <= edge_margin → z <= 0.5/edge_margin
    if edge_margin > 0.01:
        max_safe_zoom = 0.5 / edge_margin
    else:
        max_safe_zoom = 1.0

    # Start with a mild zoom based on face size
    # Small face → zoom in more, large face → zoom less
    if face_area > 0.1:
        desired_zoom = 1.0      # Face is already big — don't zoom
    elif face_area > 0.03:
        desired_zoom = 1.2      # Medium face — slight zoom
    else:
        desired_zoom = 1.4      # Small face — zoom in to make it larger

    # Clamp to the safe range
    auto_zoom = min(desired_zoom, max_safe_zoom, max_auto_zoom)
    return max(1.0, auto_zoom)
