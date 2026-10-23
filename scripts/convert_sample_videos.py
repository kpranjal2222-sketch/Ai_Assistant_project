"""Create browser-playable H.264 copies of bundled sample clips."""

from pathlib import Path
import tempfile

import cv2

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DIR = ROOT / "sample_videos"
SAMPLE_CLIPS = ("demo_violations.mp4", "demo_compliant.mp4")


def convert_sample(source: Path, output: Path) -> int:
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open sample clip: {source.name}")

    fps = float(capture.get(cv2.CAP_PROP_FPS))
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if fps <= 0 or width <= 0 or height <= 0 or width * height > 50_000_000:
        capture.release()
        raise RuntimeError(f"Unsupported video properties in {source.name}")

    writer = cv2.VideoWriter(
        str(output),
        cv2.VideoWriter_fourcc(*"avc1"),
        fps,
        (width, height),
    )
    if not writer.isOpened():
        capture.release()
        raise RuntimeError("H.264 encoder is unavailable; cannot create browser-playable sample clips.")

    frame_count = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            writer.write(frame)
            frame_count += 1
    finally:
        capture.release()
        writer.release()

    validation = cv2.VideoCapture(str(output))
    try:
        if not validation.isOpened():
            raise RuntimeError(f"Could not verify converted clip: {output.name}")
        codec = int(validation.get(cv2.CAP_PROP_FOURCC))
        codec_name = "".join(chr((codec >> (8 * index)) & 0xFF) for index in range(4)).lower()
        decoded = 0
        while True:
            ok, _ = validation.read()
            if not ok:
                break
            decoded += 1
        if codec_name not in {"avc1", "h264"} or decoded != frame_count:
            raise RuntimeError(
                f"Converted clip validation failed for {output.name}: "
                f"codec={codec_name}, decoded={decoded}, expected={frame_count}"
            )
    finally:
        validation.release()
    return frame_count


def main() -> None:
    for filename in SAMPLE_CLIPS:
        source = SAMPLE_DIR / filename
        output = SAMPLE_DIR / f"{source.stem}_h264.mp4"
        if not source.is_file():
            raise FileNotFoundError(f"Bundled sample clip not found: {source}")
        if output.exists():
            raise FileExistsError(f"Refusing to overwrite existing converted clip: {output}")
        with tempfile.TemporaryDirectory(dir=SAMPLE_DIR, prefix="video-convert-") as temp_dir:
            temp_output = Path(temp_dir) / output.name
            frames = convert_sample(source, temp_output)
            temp_output.replace(output)
        print(f"Created {output.name} ({frames} frames); original {source.name} was preserved.")


if __name__ == "__main__":
    main()
