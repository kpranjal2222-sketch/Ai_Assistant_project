"""
detector.py  --  The AI "brain" of the platform.

this module wraps the YOLO hard-hat detection model. It keeps AI logic separate
from the FastAPI and browser interface.

Main function: detect_ppe(image) -> (annotated_image, summary_dict)
"""

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from ultralytics import YOLO

# ---------------------------------------------------------------------------
# Where the trained model file lives. We build the path relative to THIS file
# so it works no matter which folder the app is launched from.
# ---------------------------------------------------------------------------
MODEL_PATH = Path(__file__).resolve().parent.parent / "models" / "ppe_model.pt"

# The model's raw class names are "Hardhat" and "NO-Hardhat". We translate
# those into friendly labels, a colour, and whether each one is a violation.
# Colours are (R, G, B). Green = good/compliant, Red = violation.
CLASS_INFO = {
    "Hardhat":    {"label": "Helmet OK",   "color": (34, 197, 94),  "violation": False},
    "NO-Hardhat": {"label": "NO Helmet",   "color": (239, 68, 68),  "violation": True},
}

# ---------------------------------------------------------------------------
# Load the model ONCE and reuse it. Loading is slow (a few seconds), so we
# cache it in a module-level variable instead of reloading on every photo.
# ---------------------------------------------------------------------------
_model = None


def get_model():
    """Load the YOLO model the first time it's needed, then reuse it."""
    global _model
    if _model is None:
        _model = YOLO(str(MODEL_PATH))
    return _model


def detect_ppe(image, conf=0.35):
    """
    Run PPE detection on a single image.

    Parameters
    ----------
    image : PIL.Image or file path or numpy array
        The construction-site photo to analyse.
    conf : float
        Confidence threshold (0-1). Detections below this are ignored.
        0.35 was chosen by testing values 0.25-0.55 on real construction
        photos: below 0.35 the model over-detects, above it starts missing
        real helmets. 0.35 gave the most accurate counts.

    Returns
    -------
    annotated : PIL.Image
        A copy of the image with coloured boxes + labels drawn on it.
    summary : dict
        {
          "total": int,          # total detections (people-with-gear)
          "helmet_ok": int,      # how many are wearing a helmet
          "violations": int,     # how many are missing a helmet
          "compliance": float,   # % wearing helmets (0-100), or None if nobody found
          "boxes": [ {label, violation, confidence}, ... ]
        }
    """
    model = get_model()

    # Make sure we have a PIL image in RGB form to draw on.
    if not isinstance(image, Image.Image):
        image = Image.open(image)
    image = image.convert("RGB")

    # Run the model. verbose=False keeps the console quiet.
    results = model.predict(image, conf=conf, verbose=False)[0]

    annotated = image.copy()
    draw = ImageDraw.Draw(annotated)

    # Try to load a normal font; fall back to a basic one if unavailable.
    try:
        font = ImageFont.truetype("arial.ttf", 20)
    except Exception:
        font = ImageFont.load_default()

    helmet_ok = 0
    violations = 0
    boxes_out = []

    for box in results.boxes:
        raw_name = model.names[int(box.cls)]
        info = CLASS_INFO.get(raw_name)
        if info is None:
            raise RuntimeError(
                f"The PPE model returned unsupported class {raw_name!r}; "
                "refusing to treat it as helmet-compliant."
            )
        confidence = float(box.conf)

        # Box coordinates come back as [x1, y1, x2, y2] (top-left, bottom-right).
        x1, y1, x2, y2 = (int(v) for v in box.xyxy[0])

        # Draw the rectangle.
        draw.rectangle([x1, y1, x2, y2], outline=info["color"], width=3)

        # Draw a filled label tag above the box.
        tag = f'{info["label"]} {confidence*100:.0f}%'
        tb = draw.textbbox((0, 0), tag, font=font)
        tw, th = tb[2] - tb[0], tb[3] - tb[1]
        ty = max(0, y1 - th - 6)
        draw.rectangle([x1, ty, x1 + tw + 8, ty + th + 6], fill=info["color"])
        draw.text((x1 + 4, ty + 2), tag, fill=(255, 255, 255), font=font)

        if info["violation"]:
            violations += 1
        else:
            helmet_ok += 1

        boxes_out.append(
            {"label": info["label"], "violation": info["violation"], "confidence": confidence}
        )

    total = helmet_ok + violations
    compliance = round(helmet_ok / total * 100, 1) if total > 0 else None

    summary = {
        "total": total,
        "helmet_ok": helmet_ok,
        "violations": violations,
        "compliance": compliance,
        "boxes": boxes_out,
    }
    return annotated, summary


# ---------------------------------------------------------------------------
# Quick self-test: run this file directly to check detection on the samples.
#   python app/detector.py
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import glob

    for path in sorted(glob.glob("sample_images/site_*.jpg")):
        _, s = detect_ppe(path)
        print(f"{path}: {s['helmet_ok']} helmet-OK, {s['violations']} violation(s), "
              f"compliance={s['compliance']}%")
