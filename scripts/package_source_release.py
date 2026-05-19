from __future__ import annotations

import sys
import zipfile
from pathlib import Path

from check_release import ReleaseError, check_artifact


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = PROJECT_ROOT / "dist"
DEFAULT_OUTPUT = DIST_DIR / "RunningHeatmap-source.zip"
ARCHIVE_ROOT = "RunningHeatmap-source"

SOURCE_FILES = (
    "LICENSE",
    "NOTICE",
    "THIRD_PARTY_NOTICES.md",
    "README.md",
    "requirements.txt",
    "pyrightconfig.json",
    "RunningHeatmap.spec",
    "app.py",
    "heatmap_core.py",
    "launcher.py",
    "heatmap.ipynb",
    ".gitignore",
    ".streamlit/config.toml",
    "scripts/build_mac.sh",
    "scripts/build_windows.bat",
    "scripts/check_release.py",
    "scripts/package_source_release.py",
    "scripts/run_mac.command",
    "scripts/run_windows.bat",
)


def _write_source_zip(output_path: Path) -> None:
    missing = [path for path in SOURCE_FILES if not (PROJECT_ROOT / path).is_file()]
    if missing:
        missing_list = ", ".join(missing)
        raise SystemExit(f"Missing source release files: {missing_list}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()

    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative_name in SOURCE_FILES:
            source_path = PROJECT_ROOT / relative_name
            archive_name = f"{ARCHIVE_ROOT}/{relative_name}"
            archive.write(source_path, archive_name)


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        print("Usage: python scripts/package_source_release.py [output-zip]", file=sys.stderr)
        return 2

    output_path = Path(argv[0]).resolve() if argv else DEFAULT_OUTPUT
    _write_source_zip(output_path)
    try:
        check_artifact(output_path)
    except ReleaseError as exc:
        print(f"ERROR: built source release did not pass checks: {exc}", file=sys.stderr)
        return 1

    print(f"Built and checked: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
