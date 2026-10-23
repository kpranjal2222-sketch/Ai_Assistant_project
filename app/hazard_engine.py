"""
hazard_engine.py -- Unified Multi-Hazard AI Vision Engine for Construction Sites.

Consolidates:
1. Dual-PPE & Fall Inspection (Hardhat, Safety Vest, NO-Hardhat, NO-Safety Vest, Fall-Detected)
   [Reference: snehilsanyal/Construction-Site-Safety-PPE-Detection & Aroomaa]
2. Fire & Smoke Emergency Detection with 3-Tier Escalation
   [Reference: rabahdev/fire-smoke-yolov8n & jensenzhong/Intrinsically-Safe-Fire-Protection]
3. Heavy Machinery Proximity Hazard (Workers inside vehicle blind spots)
   [Reference: Tarpit59/safety-measurements]
"""

from pathlib import Path
from threading import Lock
import math
from typing import Dict, List, Tuple, Any

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from ultralytics import YOLO

ROOT_DIR = Path(__file__).resolve().parent.parent
MODELS_DIR = ROOT_DIR / "models"
PPE_MULTI_PATH = MODELS_DIR / "ppe_multi.pt"
FIRE_SMOKE_PATH = MODELS_DIR / "fire_smoke.pt"
COCO_MODEL_PATH = MODELS_DIR / "yolov8n.pt"

# Global lazy-loaded models & thread locks
_ppe_model = None
_fire_model = None
_coco_model = None
_lock = Lock()

# Color definitions (R, G, B)
COLOR_GOOD = (34, 197, 94)       # Green
COLOR_WARN = (245, 158, 11)      # Amber / Orange
COLOR_CRIT = (239, 68, 68)       # Red
COLOR_FIRE = (255, 69, 0)        # Bright Flame Orange
COLOR_SMOKE = (156, 163, 175)    # Smoke Gray
COLOR_VEHICLE = (59, 130, 246)   # Blue
COLOR_DARK = (24, 24, 27)        # Dark Charcoal


def get_models():
    """Load the YOLO models once and cache in memory."""
    global _ppe_model, _fire_model, _coco_model
    if _ppe_model is None or _fire_model is None or _coco_model is None:
        with _lock:
            if _ppe_model is None and PPE_MULTI_PATH.is_file():
                _ppe_model = YOLO(str(PPE_MULTI_PATH))
            if _fire_model is None and FIRE_SMOKE_PATH.is_file():
                _fire_model = YOLO(str(FIRE_SMOKE_PATH))
            if _coco_model is None and COCO_MODEL_PATH.is_file():
                _coco_model = YOLO(str(COCO_MODEL_PATH))
    return _ppe_model, _fire_model, _coco_model


def _calculate_distance(pt1: Tuple[float, float], pt2: Tuple[float, float]) -> float:
    """Euclidean distance between two 2D points."""
    return math.sqrt((pt1[0] - pt2[0]) ** 2 + (pt1[1] - pt2[1]) ** 2)


def _get_bottom_center(bbox: List[float]) -> Tuple[float, float]:
    """Get the ground-contact point (bottom-center) of a bounding box."""
    x1, y1, x2, y2 = bbox
    return ((x1 + x2) / 2.0, y2)


def detect_multi_hazards(
    image: Image.Image,
    ppe_conf: float = 0.35,
    fire_conf: float = 0.30,
    proximity_threshold: float = 180.0,
    include_machinery: bool = True,
) -> Tuple[Image.Image, Dict[str, Any]]:
    """
    Execute comprehensive multi-hazard inspection on an image.

    Parameters:
    -----------
    image : PIL.Image
        The construction site photo.
    ppe_conf : float
        Confidence threshold for PPE and person detection.
    fire_conf : float
        Confidence threshold for fire and smoke detection.
    proximity_threshold : float
        Pixel distance threshold to flag a worker-machinery struck-by collision risk.
    include_machinery : bool
        Whether to run heavy machinery proximity checks.

    Returns:
    --------
    annotated_image : PIL.Image
        Image with high-visibility HUD overlays and bounding boxes.
    summary : dict
        Structured report of all detected hazards, severity rating, and counts.
    """
    if not isinstance(image, Image.Image):
        image = Image.open(image)
    image = image.convert("RGB")
    width, height = image.size

    ppe_model, fire_model, coco_model = get_models()
    if ppe_model is None or fire_model is None:
        raise RuntimeError("Required AI models are missing from models/ folder.")

    annotated = image.copy()
    draw = ImageDraw.Draw(annotated)

    try:
        font = ImageFont.truetype("arial.ttf", max(13, min(22, width // 42)))
        hud_font = ImageFont.truetype("arial.ttf", max(16, min(26, width // 35)))
    except Exception:
        font = ImageFont.load_default()
        hud_font = ImageFont.load_default()

    violations = []
    active_hazards = []

    # ---------------------------------------------------------
    # 1. DUAL-PPE & FALL INSPECTION
    # ---------------------------------------------------------
    ppe_results = ppe_model.predict(image, conf=ppe_conf, verbose=False)[0]

    helmets_ok = 0
    helmets_missing = 0
    vests_ok = 0
    vests_missing = 0
    falls_detected = 0
    ppe_boxes = []

    for box in ppe_results.boxes:
        cls_id = int(box.cls)
        cls_name = ppe_model.names[cls_id]
        conf = float(box.conf)
        x1, y1, x2, y2 = (int(v) for v in box.xyxy[0])

        is_violation = False
        color = COLOR_GOOD
        label = cls_name

        if cls_name == "Hardhat":
            helmets_ok += 1
            label = f"Helmet OK {conf*100:.0f}%"
            color = COLOR_GOOD
        elif cls_name == "NO-Hardhat":
            helmets_missing += 1
            label = f"NO-Helmet {conf*100:.0f}%"
            color = COLOR_CRIT
            is_violation = True
            violations.append({"type": "NO_HELMET", "bbox": [x1, y1, x2, y2], "conf": conf})
        elif cls_name == "Safety Vest":
            vests_ok += 1
            label = f"Vest OK {conf*100:.0f}%"
            color = COLOR_GOOD
        elif cls_name == "NO-Safety Vest":
            vests_missing += 1
            label = f"NO-Vest {conf*100:.0f}%"
            color = COLOR_CRIT
            is_violation = True
            violations.append({"type": "NO_VEST", "bbox": [x1, y1, x2, y2], "conf": conf})
        elif cls_name == "Fall-Detected":
            falls_detected += 1
            label = f"FALL DETECTED {conf*100:.0f}%"
            color = (220, 38, 38)
            is_violation = True
            active_hazards.append("WORKER FALL DETECTED")
            violations.append({"type": "FALL_DETECTED", "bbox": [x1, y1, x2, y2], "conf": conf})
        elif cls_name in ("Person", "Gloves", "Goggles", "Mask", "NO-Gloves", "NO-Goggles"):
            if "NO-" in cls_name:
                color = COLOR_WARN
                label = f"{cls_name} {conf*100:.0f}%"
            else:
                color = (75, 85, 99)
                label = f"{cls_name} {conf*100:.0f}%"

        # Draw bounding box
        draw.rectangle([x1, y1, x2, y2], outline=color, width=3)

        # Draw label tag
        tb = draw.textbbox((0, 0), label, font=font)
        tw, th = tb[2] - tb[0], tb[3] - tb[1]
        ty = max(0, y1 - th - 6)
        draw.rectangle([x1, ty, x1 + tw + 8, ty + th + 6], fill=color)
        draw.text((x1 + 4, ty + 2), label, fill=(255, 255, 255), font=font)

        ppe_boxes.append({"class": cls_name, "bbox": [x1, y1, x2, y2], "conf": conf, "is_violation": is_violation})

    total_helmet_checks = helmets_ok + helmets_missing
    helmet_compliance = round(helmets_ok / total_helmet_checks * 100, 1) if total_helmet_checks > 0 else None

    total_vest_checks = vests_ok + vests_missing
    vest_compliance = round(vests_ok / total_vest_checks * 100, 1) if total_vest_checks > 0 else None

    # Overall PPE compliance is the average of verified safety items
    overall_ppe_rate = None
    if total_helmet_checks > 0 or total_vest_checks > 0:
        total_items = (helmets_ok + vests_ok)
        total_required = (total_helmet_checks + total_vest_checks)
        overall_ppe_rate = round(total_items / total_required * 100, 1)

    # ---------------------------------------------------------
    # 2. FIRE & SMOKE DISASTER DETECTION (3-Tier Escalation)
    # ---------------------------------------------------------
    fire_results = fire_model.predict(image, conf=fire_conf, verbose=False)[0]

    flames_count = 0
    smoke_count = 0
    fire_boxes = []

    for box in fire_results.boxes:
        cls_id = int(box.cls)
        cls_name = fire_model.names[cls_id]
        conf = float(box.conf)
        x1, y1, x2, y2 = (int(v) for v in box.xyxy[0])

        if cls_name == "fire":
            flames_count += 1
            color = COLOR_FIRE
            tag = f"FLAME / FIRE {conf*100:.0f}%"
        else:
            smoke_count += 1
            color = COLOR_SMOKE
            tag = f"SMOKE PLUME {conf*100:.0f}%"

        # Draw thick fire/smoke outline
        draw.rectangle([x1, y1, x2, y2], outline=color, width=4)
        tb = draw.textbbox((0, 0), tag, font=font)
        tw, th = tb[2] - tb[0], tb[3] - tb[1]
        ty = max(0, y1 - th - 6)
        draw.rectangle([x1, ty, x1 + tw + 8, ty + th + 6], fill=color)
        draw.text((x1 + 4, ty + 2), tag, fill=(255, 255, 255), font=font)

        fire_boxes.append({"type": cls_name, "bbox": [x1, y1, x2, y2], "conf": conf})

    # Determine Fire Escalation Tier
    if flames_count > 0:
        fire_tier = "EMERGENCY"  # Level 2 Active Fire
        fire_alert = f"ACTIVE FIRE DETECTED: {flames_count} flame source(s) identified!"
        active_hazards.append("ACTIVE FIRE IN SECTOR")
    elif smoke_count > 0:
        fire_tier = "WARNING"    # Level 1 Smoke Hazard
        fire_alert = f"SMOKE / SMOLDERING DETECTED: {smoke_count} smoke plume(s) identified."
        active_hazards.append("SMOKE HAZARD DETECTED")
    else:
        fire_tier = "CLEAR"
        fire_alert = "No fire or smoke detected."

    # ---------------------------------------------------------
    # 3. HEAVY MACHINERY PROXIMITY HAZARD (Tarpit59)
    # ---------------------------------------------------------
    machinery_hazards = []
    if include_machinery and coco_model is not None:
        coco_res = coco_model.predict(image, conf=0.35, verbose=False)[0]
        persons = []
        vehicles = []

        for box in coco_res.boxes:
            c_name = coco_model.names[int(box.cls)]
            coords = [float(v) for v in box.xyxy[0]]
            if c_name == "person":
                persons.append(coords)
            elif c_name in ("truck", "car", "bus"):
                vehicles.append({"type": c_name, "bbox": coords})

        # Calculate worker-to-vehicle distances
        for v_idx, v in enumerate(vehicles):
            vx1, vy1, vx2, vy2 = (int(c) for c in v["bbox"])
            v_center = _get_bottom_center(v["bbox"])
            # Draw vehicle perimeter
            draw.rectangle([vx1, vy1, vx2, vy2], outline=COLOR_VEHICLE, width=2)
            draw.text((vx1 + 4, vy1 + 4), f"Equipment ({v['type']})", fill=COLOR_VEHICLE, font=font)

            for p_idx, p_bbox in enumerate(persons):
                p_center = _get_bottom_center(p_bbox)
                dist = _calculate_distance(p_center, v_center)
                if dist <= proximity_threshold:
                    # Struck-by collision danger!
                    machinery_hazards.append({
                        "vehicle": v["type"],
                        "distance_px": round(dist, 1),
                        "person_bbox": [int(c) for c in p_bbox],
                        "vehicle_bbox": [vx1, vy1, vx2, vy2]
                    })
                    # Draw warning line connecting worker and vehicle
                    draw.line([p_center, v_center], fill=COLOR_CRIT, width=3)
                    mid_x, mid_y = (p_center[0] + v_center[0]) / 2, (p_center[1] + v_center[1]) / 2
                    draw.rectangle([mid_x - 45, mid_y - 12, mid_x + 45, mid_y + 12], fill=COLOR_CRIT)
                    draw.text((mid_x - 40, mid_y - 10), "PROXIMITY DANGER", fill=(255, 255, 255), font=font)
                    active_hazards.append(f"WORKER IN {v['type'].upper()} DANGER ZONE")

    # ---------------------------------------------------------
    # 4. OVERALL SEVERITY & TOP HUD BANNER
    # ---------------------------------------------------------
    if flames_count > 0 or falls_detected > 0:
        overall_severity = "CATASTROPHIC"
        banner_color = COLOR_CRIT
        banner_text = f"CRITICAL HAZARD DETECTED: {active_hazards[0] if active_hazards else 'EMERGENCY'}"
    elif len(machinery_hazards) > 0 or helmets_missing > 0 or vests_missing > 0:
        overall_severity = "CRITICAL" if len(machinery_hazards) > 0 else "MODERATE"
        banner_color = COLOR_CRIT if overall_severity == "CRITICAL" else COLOR_WARN
        banner_text = f"SAFETY VIOLATIONS: {len(violations)} PPE issues | {len(machinery_hazards)} Proximity Alerts"
    elif smoke_count > 0:
        overall_severity = "WARNING"
        banner_color = COLOR_WARN
        banner_text = "WARNING: SMOKE OR SMOLDERING DETECTED"
    else:
        overall_severity = "CLEAR"
        banner_color = COLOR_GOOD
        banner_text = "SITE STATUS: ALL CLEAR -- NO ACTIVE VIOLATIONS"

    # Draw Top HUD Banner
    hud_h = 36
    draw.rectangle([0, 0, width, hud_h], fill=COLOR_DARK)
    draw.rectangle([0, 0, 8, hud_h], fill=banner_color)  # Accent left border
    draw.text((16, 7), banner_text, fill=(255, 255, 255), font=hud_font)

    summary = {
        "overall_severity": overall_severity,
        "active_hazards": active_hazards,
        "ppe": {
            "helmets_ok": helmets_ok,
            "helmets_missing": helmets_missing,
            "helmet_compliance": helmet_compliance,
            "vests_ok": vests_ok,
            "vests_missing": vests_missing,
            "vest_compliance": vest_compliance,
            "overall_compliance": overall_ppe_rate,
            "falls_detected": falls_detected,
            "detections": ppe_boxes,
        },
        "fire_smoke": {
            "tier": fire_tier,
            "flames_count": flames_count,
            "smoke_count": smoke_count,
            "alert": fire_alert,
            "detections": fire_boxes,
        },
        "machinery_hazards": machinery_hazards,
        "total_violations": len(violations) + len(machinery_hazards) + (1 if flames_count > 0 else 0) + (1 if falls_detected > 0 else 0),
    }

    return annotated, summary
