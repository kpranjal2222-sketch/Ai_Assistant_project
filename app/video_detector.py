"""
video_detector.py  --  Run PPE detection across a VIDEO, not just one photo.

The key idea: a video is just a fast sequence of images ("frames"). So we reuse
the SAME detect_ppe() function from detector.py on individual frames -- no new AI
model, no retraining. This keeps one source of truth for the detection logic.

Because the model runs on the CPU (a few tenths of a second per frame), we don't
analyse every single frame. We analyse every Nth frame (sample_every) and reuse
that annotated result on the frames in between. The video still plays smoothly,
but processing is several times faster.

Main function:
    detect_ppe_video(in_path, out_path, conf=0.35, sample_every=5) -> summary dict
"""

from pathlib import Path
import logging
import math

import cv2
import numpy as np
from PIL import Image

from .detector import detect_ppe

logger = logging.getLogger(__name__)


def detect_ppe_video(in_path, out_path, conf=0.35, sample_every=5, progress=None):
    """
    Analyse a video for helmet compliance and write an annotated copy.

    Parameters
    ----------
    in_path : str | Path
        Path to the source video clip (mp4, mov, avi, ...).
    out_path : str | Path
        Where to write the annotated .mp4.
    conf : float
        Confidence threshold passed straight through to detect_ppe.
    sample_every : int
        Run the AI on every Nth frame (>=1). Between those frames we redraw the
        most recent annotated frame, so playback stays smooth while we do a
        fraction of the work. 5 is a good balance on CPU.
    progress : callable | None
        Optional callback progress(done_frames, total_frames) for a progress bar.

    Returns
    -------
    summary : dict
        {
          "frames_total": int,     # frames in the clip
          "frames_analysed": int,  # how many the AI actually ran on
          "max_workers": int,      # most people seen in any single analysed frame
          "peak_violations": int,  # most helmet-violations in any single frame
          "avg_compliance": float | None,  # mean compliance across analysed frames
          "fps": float,
          "duration_sec": float,
        }
    """
    in_path, out_path = str(in_path), str(out_path)
    sample_every = int(sample_every)
    if not 1 <= sample_every <= 30:
        raise ValueError("Frame sampling interval must be between 1 and 30.")

    cap = cv2.VideoCapture(in_path)
    if not cap.isOpened():
        raise ValueError("Could not open the uploaded video.")

    writer = None
    output_path = Path(out_path)
    succeeded = False
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS))
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        frames_total = max(0, int(cap.get(cv2.CAP_PROP_FRAME_COUNT)))
        if (not math.isfinite(fps) or fps <= 0 or fps > 120 or
                width <= 0 or height <= 0 or width * height > 50_000_000):
            raise ValueError("The video has invalid or unsupported dimensions or frame rate.")
        if frames_total and frames_total / fps > 300:
            raise ValueError("Videos must be 5 minutes or shorter.")

        # H.264 in MP4 is supported by browsers; mp4v (MPEG-4 Part 2) is not.
        fourcc = cv2.VideoWriter_fourcc(*"avc1")
        writer = cv2.VideoWriter(out_path, fourcc, fps, (width, height))
        if not writer.isOpened():
            raise RuntimeError("Could not open the video writer (codec issue).")

        frames_analysed = 0
        max_workers = 0
        peak_violations = 0
        compliance_values = []
        last_annotated_bgr = None  # reused on skipped frames

        idx = 0
        while True:
            ok, frame_bgr = cap.read()
            if not ok:
                break
            if idx / fps >= 300:
                raise ValueError("Videos must be 5 minutes or shorter.")

            if idx % sample_every == 0:
                # OpenCV frames are BGR numpy arrays; detect_ppe wants a PIL RGB image.
                frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
                pil_img = Image.fromarray(frame_rgb)
                annotated_pil, summary = detect_ppe(pil_img, conf=conf)

                # Back to a BGR array for the writer.
                last_annotated_bgr = cv2.cvtColor(np.array(annotated_pil), cv2.COLOR_RGB2BGR)

                frames_analysed += 1
                max_workers = max(max_workers, summary["total"])
                peak_violations = max(peak_violations, summary["violations"])
                if summary["compliance"] is not None:
                    compliance_values.append(summary["compliance"])

                if progress is not None:
                    progress(idx + 1, frames_total or idx + 1)

            # Write the freshest annotated frame (or the raw frame if nothing yet).
            writer.write(last_annotated_bgr if last_annotated_bgr is not None else frame_bgr)
            idx += 1

        if idx == 0 or frames_analysed == 0:
            raise ValueError("The uploaded video contains no analysable frames.")

        avg_compliance = (
            round(sum(compliance_values) / len(compliance_values), 1)
            if compliance_values else None
        )

        result = {
            "frames_total": idx,
            "frames_analysed": frames_analysed,
            "max_workers": max_workers,
            "peak_violations": peak_violations,
            "avg_compliance": avg_compliance,
            "fps": round(fps, 2),
            "duration_sec": round(idx / fps, 1),
        }

        writer.release()
        writer = None
        rendered = cv2.VideoCapture(out_path)
        try:
            output_ok, _ = rendered.read() if rendered.isOpened() else (False, None)
            output_frames = int(rendered.get(cv2.CAP_PROP_FRAME_COUNT))
        finally:
            rendered.release()
        if not output_ok or output_frames != idx:
            raise RuntimeError("The annotated video could not be decoded completely.")

        succeeded = True
        return result
    finally:
        cap.release()
        if writer is not None:
            writer.release()
        if not succeeded:
            try:
                output_path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not remove an incomplete annotated video: %s", output_path)


# ---------------------------------------------------------------------------
# Quick self-test: build a tiny clip from the sample images, then analyse it.
#   python -m app.video_detector
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import glob

    samples = sorted(glob.glob("sample_images/*.jpg"))
    if not samples:
        print("No sample images found to build a test clip.")
        raise SystemExit

    tmp_in = "data/videos/_selftest_input.mp4"
    tmp_out = "data/videos/_selftest_annotated.mp4"
    Path("data/videos").mkdir(parents=True, exist_ok=True)

    # Build a small test clip: hold each sample image for ~1 second at 10 fps.
    first = cv2.imread(samples[0])
    h, w = first.shape[:2]
    vw = cv2.VideoWriter(tmp_in, cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (w, h))
    for p in samples:
        img = cv2.imread(p)
        img = cv2.resize(img, (w, h))
        for _ in range(10):
            vw.write(img)
    vw.release()
    print(f"Built test clip: {tmp_in}")

    result = detect_ppe_video(tmp_in, tmp_out, conf=0.35, sample_every=5)
    print("Annotated video:", tmp_out)
    for k, v in result.items():
        print(f"  {k}: {v}")
