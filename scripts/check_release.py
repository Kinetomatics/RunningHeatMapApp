from __future__ import annotations

import fnmatch
import sys
import zipfile
from pathlib import Path


REQUIRED_DOCS = ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md", "README.md")
FORBIDDEN_DIRS_ANYWHERE = {"app_data", "strava_export"}
FORBIDDEN_ROOT_DIRS = {"cache", "outputs"}
FORBIDDEN_FILES = {"activities.csv", "profile.csv", "privacy_zones.csv"}
FORBIDDEN_GLOBS = ("*.fit", "*.fit.gz", "export_*.zip")


class ReleaseError(Exception):
    pass


def _normalize_name(name: str) -> str:
    return name.replace("\\", "/").lstrip("./")


def _archive_names(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        return [_normalize_name(info.filename) for info in archive.infolist() if not info.is_dir()]


def _directory_names(path: Path) -> list[str]:
    return [_normalize_name(str(item.relative_to(path))) for item in path.rglob("*") if item.is_file()]


def _entry_names(path: Path) -> list[str]:
    if path.is_dir():
        names = _directory_names(path)
        return [name for name in names if not name.startswith("__MACOSX/")]
    if zipfile.is_zipfile(path):
        names = _archive_names(path)
        return [name for name in names if not name.startswith("__MACOSX/")]
    raise ReleaseError(f"{path} is not a zip archive or directory.")


def _single_root(names: list[str]) -> str | None:
    roots = {name.split("/", 1)[0] for name in names if name}
    if len(roots) == 1:
        return next(iter(roots))
    return None


def _strip_single_root(names: list[str]) -> set[str]:
    root = _single_root(names)
    if root is None:
        return set(names)
    prefix = f"{root}/"
    return {name.removeprefix(prefix) for name in names if name.startswith(prefix)}


def _is_app_artifact(path: Path, names: list[str]) -> bool:
    normalized_path = _normalize_name(str(path))
    if normalized_path.endswith(".app"):
        return True
    return any(
        ".app/" in name
        or "/_internal/" in name
        or name.endswith(".exe")
        or name.endswith("/RunningHeatmap")
        for name in names
    )


def _forbidden_match(name: str, root_relative_name: str) -> str | None:
    parts = [part for part in name.split("/") if part]
    lower_parts = {part.lower() for part in parts}
    blocked_dirs = FORBIDDEN_DIRS_ANYWHERE.intersection(lower_parts)
    if blocked_dirs:
        return f"forbidden directory component: {sorted(blocked_dirs)[0]}"

    root_parts = [part for part in root_relative_name.split("/") if part]
    if root_parts and root_parts[0].lower() in FORBIDDEN_ROOT_DIRS:
        return f"forbidden release-root directory: {root_parts[0]}"

    basename = parts[-1].lower() if parts else ""
    if basename in FORBIDDEN_FILES:
        return f"forbidden file name: {basename}"
    for pattern in FORBIDDEN_GLOBS:
        if fnmatch.fnmatch(basename, pattern):
            return f"forbidden file pattern: {pattern}"
    return None


def _check_forbidden_entries(names: list[str]) -> list[str]:
    failures: list[str] = []
    root = _single_root(names)
    prefix = f"{root}/" if root else ""
    for name in names:
        root_relative_name = name.removeprefix(prefix) if prefix and name.startswith(prefix) else name
        reason = _forbidden_match(name, root_relative_name)
        if reason:
            failures.append(f"{name} ({reason})")
    return failures


def _has_suffix(names: list[str], suffix: str) -> bool:
    suffix = _normalize_name(suffix)
    return any(name == suffix or name.endswith(f"/{suffix}") for name in names)


def _missing_source_docs(names: list[str]) -> list[str]:
    stripped = _strip_single_root(names)
    return [doc for doc in REQUIRED_DOCS if doc not in stripped]


def _missing_app_docs(names: list[str]) -> list[str]:
    return [f"legal/{doc}" for doc in REQUIRED_DOCS if not _has_suffix(names, f"legal/{doc}")]


def check_artifact(path: Path) -> None:
    path = path.resolve()
    if not path.exists():
        raise ReleaseError(f"{path} does not exist.")

    names = _entry_names(path)
    forbidden = _check_forbidden_entries(names)
    if forbidden:
        details = "\n  ".join(forbidden[:25])
        extra = "" if len(forbidden) <= 25 else f"\n  ... and {len(forbidden) - 25} more"
        raise ReleaseError(f"{path} contains private or generated data:\n  {details}{extra}")

    if _is_app_artifact(path, names):
        missing = _missing_app_docs(names)
        if missing:
            raise ReleaseError(f"{path} is missing packaged legal docs: {', '.join(missing)}")
    else:
        missing = _missing_source_docs(names)
        if missing:
            raise ReleaseError(f"{path} is missing source legal docs: {', '.join(missing)}")


def main(argv: list[str]) -> int:
    if not argv:
        print("Usage: python scripts/check_release.py <release-zip-or-directory> [...]", file=sys.stderr)
        return 2

    failed = False
    for arg in argv:
        path = Path(arg)
        try:
            check_artifact(path)
        except ReleaseError as exc:
            failed = True
            print(f"ERROR: {exc}", file=sys.stderr)
        else:
            print(f"OK: {path}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
