"""Review and optionally remove unreferenced generated media past a chosen age."""

import argparse
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
MEDIA_DIRS = {
    "detections": {".jpg", ".jpeg", ".png", ".webp"},
    "evidence": {".jpg", ".jpeg", ".png", ".webp"},
    "uploads": {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov", ".avi", ".mkv", ".webm"},
    "videos": {".mp4", ".mov", ".avi", ".mkv", ".webm"},
}


def _referenced_media(database_path: Path) -> set[str]:
    if not database_path.exists():
        return set()

    connection = sqlite3.connect(database_path)
    try:
        inspection_paths = {
            Path(row[0]).resolve()
            for row in connection.execute(
                "SELECT image_path FROM inspections WHERE image_path IS NOT NULL AND image_path != ''"
            )
        }
        evidence_names = {
            row[0]
            for row in connection.execute(
                "SELECT evidence_file FROM incident_tickets WHERE evidence_file IS NOT NULL AND evidence_file != ''"
            )
        }
    except sqlite3.Error as error:
        raise RuntimeError(f"Could not read media references from {database_path}: {error}") from error
    finally:
        connection.close()

    return {str(path) for path in inspection_paths} | evidence_names


def cleanup_old_media(
    days: int,
    *,
    apply: bool = False,
    data_dir: Path = DATA_DIR,
    now: datetime | None = None,
) -> list[Path]:
    """Return files older than ``days`` and delete them only when ``apply`` is true."""
    if days < 1:
        raise ValueError("Retention age must be at least one day.")

    data_dir = Path(data_dir).resolve()
    references = _referenced_media(data_dir / "records.db")
    cutoff = (now or datetime.now()) - timedelta(days=days)
    cutoff_timestamp = cutoff.timestamp()
    eligible: list[Path] = []

    for directory_name, suffixes in MEDIA_DIRS.items():
        media_dir = data_dir / directory_name
        if not media_dir.exists():
            continue
        if media_dir.is_symlink() or not media_dir.is_dir():
            raise RuntimeError(f"Refusing to inspect a non-directory media path: {media_dir}")

        for path in media_dir.iterdir():
            if path.is_symlink() or not path.is_file() or path.suffix.lower() not in suffixes:
                continue
            resolved = str(path.resolve())
            if resolved in references or path.name in references:
                continue
            if path.stat().st_mtime >= cutoff_timestamp:
                continue
            eligible.append(path)

    eligible.sort()
    if apply:
        for path in eligible:
            path.unlink()
    return eligible


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dry-run by default. Review candidate files before using --apply."
    )
    parser.add_argument("--days", type=int, required=True, help="Keep files newer than this age in days.")
    parser.add_argument("--apply", action="store_true", help="Delete the listed, unreferenced media files.")
    args = parser.parse_args()

    candidates = cleanup_old_media(args.days, apply=args.apply)
    action = "Deleted" if args.apply else "Would delete"
    for path in candidates:
        print(f"{action}: {path.relative_to(DATA_DIR)}")
    print(f"{len(candidates)} file(s) {'deleted' if args.apply else 'eligible'}.")
    if not args.apply:
        print("Dry run only; no files were changed.")


if __name__ == "__main__":
    main()
