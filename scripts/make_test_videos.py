"""
make_test_videos.py  --  Build two demo clips to test Video Detection, WITHOUT
downloading anything. We synthesise them from the existing sample_images/ so the
test data provenance is fully known and safe.

  * demo_compliant.mp4   -> workers WITH helmets  (should show ~0 violations)
  * demo_violations.mp4  -> workers WITHOUT helmets (should show violations)

Each source photo is turned into a few seconds of video with a slow zoom
("Ken Burns" effect) so it plays like real footage and exercises the frame-by-
frame analysis. After building, we run the real detector over each clip to
confirm it behaves as labelled.

Run from the project root:
    venv/Scripts/python.exe scripts/make_test_videos.py
"""

import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image                      # noqa: E402
from app.detector import detect_ppe        # noqa: E402
from app.video_detector import detect_ppe_video  # noqa: E402

OW, OH = 1280, 720      # output resolution (720p)
FPS = 24


def fit_cover(img, ow, oh):
    """Resize + center-crop a BGR image so it exactly fills ow x oh."""
    h, w = img.shape[:2]
    scale = max(ow / w, oh / h)
    nw, nh = int(round(w * scale)), int(round(h * scale))
    resized = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)
    x0, y0 = (nw - ow) // 2, (nh - oh) // 2
    return resized[y0:y0 + oh, x0:x0 + ow]


def ken_burns(base, seconds, z_start, z_end):
    """Yield frames doing a slow center zoom (z_start -> z_end) on `base`."""
    n = int(seconds * FPS)
    for i in range(n):
        t = i / max(1, n - 1)
        z = z_start + (z_end - z_start) * t
        cw, ch = int(OW / z), int(OH / z)
        x0, y0 = (OW - cw) // 2, (OH - ch) // 2
        crop = base[y0:y0 + ch, x0:x0 + cw]
        yield cv2.resize(crop, (OW, OH), interpolation=cv2.INTER_LINEAR)


def build_video(images, out_path, seconds_each):
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")   # matches video_detector.py
    vw = cv2.VideoWriter(str(out_path), fourcc, FPS, (OW, OH))
    if not vw.isOpened():
        raise RuntimeError("Could not open VideoWriter (codec issue).")
    for idx, p in enumerate(images):
        base = fit_cover(cv2.imread(str(p)), OW, OH)
        # alternate zoom direction so multi-image clips feel less repetitive
        z0, z1 = (1.0, 1.10) if idx % 2 == 0 else (1.10, 1.0)
        for frame in ken_burns(base, seconds_each, z0, z1):
            vw.write(frame)
    vw.release()


def main():
    samples = sorted((ROOT / "sample_images").glob("*.jpg"))
    if not samples:
        print("No sample images found.")
        return

    # Classify each sample by running the real detector.
    print("Classifying sample images (conf=0.35):")
    compliant, violation = [], []
    for p in samples:
        _, s = detect_ppe(Image.open(p).convert("RGB"), conf=0.35)
        tag = "VIOLATION" if s["violations"] > 0 else ("compliant" if s["total"] else "no workers")
        print(f"  {p.name:22s} workers={s['total']} violations={s['violations']} -> {tag}")
        if s["violations"] > 0:
            violation.append(p)
        elif s["total"] > 0:
            compliant.append(p)

    out_dir = ROOT / "sample_videos"
    out_dir.mkdir(exist_ok=True)
    (ROOT / "data" / "videos").mkdir(parents=True, exist_ok=True)

    print("\nBuilding clips...")
    build_video(compliant, out_dir / "demo_compliant.mp4", seconds_each=2.5)
    build_video(violation, out_dir / "demo_violations.mp4", seconds_each=6.0)
    print(f"  {out_dir/'demo_compliant.mp4'}  (from {len(compliant)} compliant photo(s))")
    print(f"  {out_dir/'demo_violations.mp4'} (from {len(violation)} violation photo(s))")

    # Verify: run the real video detector over each clip.
    print("\nVerifying with detect_ppe_video():")
    for name in ("demo_compliant.mp4", "demo_violations.mp4"):
        res = detect_ppe_video(
            out_dir / name,
            ROOT / "data" / "videos" / f"_verify_{name}",
            conf=0.35, sample_every=5,
        )
        print(f"  {name}: workers(peak)={res['max_workers']} "
              f"peak_violations={res['peak_violations']} "
              f"avg_compliance={res['avg_compliance']} duration={res['duration_sec']}s")


if __name__ == "__main__":
    main()
