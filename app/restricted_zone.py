"""Person detection and restricted-zone analysis for site images."""

import math
from pathlib import Path
from threading import Lock

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from ultralytics import YOLO

PERSON_MODEL_PATH = Path(__file__).resolve().parent.parent / "models" / "yolov8n.pt"
PERSON_CLASS_NAME = "person"
DEFAULT_CONFIDENCE = 0.35

_person_model = None
_person_class_id = None
_model_lock = Lock()


def get_person_model():
    """Load the local COCO person detector once; never fetch weights at runtime."""
    global _person_model, _person_class_id
    if _person_model is None:
        with _model_lock:
            if _person_model is None:
                if not PERSON_MODEL_PATH.is_file():
                    raise FileNotFoundError(
                        "The person-detection model is unavailable. "
                        "Expected models/yolov8n.pt in the project."
                    )
                model = YOLO(str(PERSON_MODEL_PATH))
                names = model.names
                matches = [
                    int(class_id)
                    for class_id, name in names.items()
                    if str(name).strip().lower() == PERSON_CLASS_NAME
                ]
                if len(matches) != 1:
                    raise RuntimeError(
                        "The configured person-detection model must expose exactly one "
                        "'person' class."
                    )
                _person_model = model
                _person_class_id = matches[0]
    return _person_model, _person_class_id


def validate_polygon(points, width, height):
    """Validate and normalize a simple polygon in original-image pixel coordinates."""
    if not isinstance(points, list) or not 3 <= len(points) <= 100:
        raise ValueError("The restricted zone must contain between 3 and 100 points.")
    if width <= 0 or height <= 0:
        raise ValueError("The image dimensions are invalid.")

    polygon = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError("Each polygon point must contain an x and y coordinate.")
        x, y = point
        if (
            isinstance(x, bool)
            or isinstance(y, bool)
            or not isinstance(x, (int, float))
            or not isinstance(y, (int, float))
        ):
            raise ValueError("Polygon coordinates must be finite numbers.")
        try:
            x, y = float(x), float(y)
        except (OverflowError, ValueError):
            raise ValueError("Polygon coordinates must be finite numbers.")
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError("Polygon coordinates must be finite numbers.")
        if not 0 <= x <= width or not 0 <= y <= height:
            raise ValueError("Polygon points must lie within the uploaded image.")
        polygon.append((x, y))

    if len(set(polygon)) != len(polygon):
        raise ValueError("The restricted-zone polygon must not repeat points.")
    area = abs(
        sum(
            polygon[i][0] * polygon[(i + 1) % len(polygon)][1]
            - polygon[(i + 1) % len(polygon)][0] * polygon[i][1]
            for i in range(len(polygon))
        )
    ) / 2
    if area < 0.5:
        raise ValueError("The restricted-zone polygon must enclose an area.")
    if _has_self_intersection(polygon):
        raise ValueError("The restricted-zone polygon must not cross itself.")
    return polygon


def _orientation(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a, b, p):
    return (
        min(a[0], b[0]) <= p[0] <= max(a[0], b[0])
        and min(a[1], b[1]) <= p[1] <= max(a[1], b[1])
        and abs(_orientation(a, b, p)) < 1e-9
    )


def _segments_intersect(a, b, c, d):
    o1, o2 = _orientation(a, b, c), _orientation(a, b, d)
    o3, o4 = _orientation(c, d, a), _orientation(c, d, b)
    if o1 * o2 < 0 and o3 * o4 < 0:
        return True
    return (
        (abs(o1) < 1e-9 and _on_segment(a, b, c))
        or (abs(o2) < 1e-9 and _on_segment(a, b, d))
        or (abs(o3) < 1e-9 and _on_segment(c, d, a))
        or (abs(o4) < 1e-9 and _on_segment(c, d, b))
    )


def _has_self_intersection(polygon):
    count = len(polygon)
    for i in range(count):
        a, b = polygon[i], polygon[(i + 1) % count]
        for j in range(i + 1, count):
            if j == i or j == (i + 1) % count or (j + 1) % count == i:
                continue
            c, d = polygon[j], polygon[(j + 1) % count]
            if _segments_intersect(a, b, c, d):
                return True
    return False


def point_in_polygon(point, polygon):
    """Return true for points inside or on the boundary of a zone polygon."""
    contour = np.asarray(polygon, dtype="float32")
    return cv2.pointPolygonTest(contour, (float(point[0]), float(point[1])), False) >= 0


def classify_person_boxes(boxes, polygon, width, height):
    """Classify boxes by whether their clipped bottom-center touches the zone."""
    people = []
    for box in boxes:
        x1, y1, x2, y2 = (float(value) for value in box["bbox"])
        confidence = float(box["confidence"])
        if not all(math.isfinite(value) for value in (x1, y1, x2, y2, confidence)):
            raise ValueError("Person detection returned invalid box coordinates.")
        if not 0 <= confidence <= 1:
            raise ValueError("Person detection returned invalid confidence.")
        x1 = min(max(x1, 0.0), width - 1.0)
        x2 = min(max(x2, 0.0), width - 1.0)
        y1 = min(max(y1, 0.0), height - 1.0)
        y2 = min(max(y2, 0.0), height - 1.0)
        if x2 <= x1 or y2 <= y1:
            continue
        ground_point = ((x1 + x2) / 2, y2)
        inside = point_in_polygon(ground_point, polygon)
        people.append(
            {
                "bbox": [round(x1, 2), round(y1, 2), round(x2, 2), round(y2, 2)],
                "ground_point": [round(ground_point[0], 2), round(ground_point[1], 2)],
                "confidence": round(confidence, 6),
                "inside_zone": inside,
                "zone_violation": inside,
            }
        )
    return people


def _draw_annotations(image, polygon, people):
    annotated = image.copy()
    overlay = Image.new("RGBA", annotated.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.polygon(polygon, fill=(245, 135, 31, 48))
    annotated = Image.alpha_composite(annotated.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(annotated)
    try:
        font = ImageFont.truetype("arial.ttf", max(14, min(22, annotated.width // 36)))
    except OSError:
        font = ImageFont.load_default()

    line_width = max(2, annotated.width // 400)
    closed_polygon = polygon + [polygon[0]]
    draw.line(closed_polygon, fill=(245, 135, 31), width=line_width * 2, joint="curve")

    violations = sum(person["zone_violation"] for person in people)
    for index, person in enumerate(people, start=1):
        color = (220, 75, 62) if person["zone_violation"] else (63, 157, 91)
        x1, y1, x2, y2 = person["bbox"]
        draw.rectangle((x1, y1, x2, y2), outline=color, width=line_width)
        label = f"Person {index} {person['confidence'] * 100:.0f}%"
        bounds = draw.textbbox((0, 0), label, font=font)
        label_width = bounds[2] - bounds[0]
        label_height = bounds[3] - bounds[1]
        tag_y = max(0, y1 - label_height - 8)
        draw.rectangle(
            (x1, tag_y, x1 + label_width + 10, tag_y + label_height + 6),
            fill=color,
        )
        draw.text((x1 + 5, tag_y + 2), label, fill=(255, 255, 255), font=font)

    headline = f"ZONE VIOLATIONS: {violations}"
    headline_bounds = draw.textbbox((0, 0), headline, font=font)
    headline_width = headline_bounds[2] - headline_bounds[0]
    headline_height = headline_bounds[3] - headline_bounds[1]
    draw.rectangle(
        (8, 8, 20 + headline_width, 20 + headline_height),
        fill=(30, 27, 22),
    )
    draw.text((14, 13), headline, fill=(255, 255, 255), font=font)
    return annotated


def detect_restricted_zone(image, polygon, conf=DEFAULT_CONFIDENCE):
    """Detect people and mark a violation when the box bottom-center is in-zone."""
    if not isinstance(image, Image.Image):
        raise ValueError("A decoded image is required.")
    image = image.convert("RGB")
    width, height = image.size
    polygon = validate_polygon(polygon, width, height)
    if (
        isinstance(conf, bool)
        or not isinstance(conf, (int, float))
        or not math.isfinite(conf)
        or not 0 <= conf <= 1
    ):
        raise ValueError("Confidence must be between 0 and 1.")

    model, person_class_id = get_person_model()
    result = model.predict(
        image,
        conf=float(conf),
        classes=[person_class_id],
        device="cpu",
        imgsz=640,
        max_det=100,
        iou=0.5,
        verbose=False,
    )[0]
    boxes = []
    for detection in result.boxes:
        boxes.append(
            {
                "bbox": [float(value) for value in detection.xyxy[0].tolist()],
                "confidence": float(detection.conf[0]),
            }
        )
    people = classify_person_boxes(boxes, polygon, width, height)
    inside = sum(person["inside_zone"] for person in people)
    summary = {
        "total_persons": len(people),
        "inside_zone": inside,
        "outside_zone": len(people) - inside,
        "violations": inside,
        "polygon": [[round(x, 2), round(y, 2)] for x, y in polygon],
        "persons": people,
        "message": (
            "No people detected; zone compliance could not be assessed."
            if not people
            else (
                f"{inside} person(s) inside the restricted zone."
                if inside
                else "People detected, but none entered the restricted zone."
            )
        ),
    }
    return _draw_annotations(image, polygon, people), summary
