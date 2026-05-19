from __future__ import annotations

import base64
import gzip
import hashlib
import json
import math
import shutil
import warnings
import zipfile
from dataclasses import dataclass, field
from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, cast

import fitparse
import folium
from folium.raster_layers import ImageOverlay
import matplotlib.colors as mcolors
import numpy as np
import pandas as pd
import psutil
from PIL import Image
from pyproj import Transformer
from scipy.ndimage import gaussian_filter

warnings.filterwarnings("ignore")

StatusCallback = Callable[[str], None]
ESTIMATED_RENDER_BYTES_PER_PIXEL = 128
FALLBACK_AVAILABLE_MEMORY_BYTES = 4 * 1024**3


class HeatmapError(Exception):
    """User-facing heatmap generation error."""


def fit_message_data(message: Any) -> dict[str, Any]:
    fields = getattr(message, "fields", None)
    if fields is not None:
        return {field.name: field.value for field in fields}
    return dict(message.get_values())


def detect_available_memory_bytes() -> int | None:
    try:
        return int(psutil.virtual_memory().available)
    except Exception:
        return None


def estimate_render_memory_bytes(grid_pixels: int) -> int:
    return int(grid_pixels * ESTIMATED_RENDER_BYTES_PER_PIXEL)


def bytes_to_gib(byte_count: int | float) -> float:
    return float(byte_count) / 1024**3


def memory_safe_pixel_cap(
    max_grid_pixels: int,
    memory_mode: str = "auto",
    memory_safety_fraction: float = 0.45,
    available_memory_bytes: int | None = None,
) -> dict[str, object]:
    hard_cap = max(1, int(max_grid_pixels))
    if memory_mode == "fixed":
        return {
            "memory_mode": memory_mode,
            "available_memory_bytes": available_memory_bytes,
            "memory_safety_fraction": memory_safety_fraction,
            "auto_grid_pixel_cap": None,
            "grid_pixel_cap": hard_cap,
            "estimated_bytes_per_pixel": ESTIMATED_RENDER_BYTES_PER_PIXEL,
            "estimated_peak_memory_bytes": estimate_render_memory_bytes(hard_cap),
            "used_fallback_memory": False,
        }
    if memory_mode != "auto":
        raise HeatmapError("Memory mode must be 'auto' or 'fixed'.")
    if not 0 < memory_safety_fraction <= 0.9:
        raise HeatmapError("Memory safety fraction must be greater than 0 and at most 0.9.")

    detected_memory = available_memory_bytes if available_memory_bytes is not None else detect_available_memory_bytes()
    used_fallback = detected_memory is None
    safe_available = detected_memory if detected_memory is not None else FALLBACK_AVAILABLE_MEMORY_BYTES
    auto_cap = max(1, int((safe_available * memory_safety_fraction) / ESTIMATED_RENDER_BYTES_PER_PIXEL))
    pixel_cap = max(1, min(hard_cap, auto_cap))
    return {
        "memory_mode": memory_mode,
        "available_memory_bytes": int(safe_available),
        "memory_safety_fraction": memory_safety_fraction,
        "auto_grid_pixel_cap": auto_cap,
        "grid_pixel_cap": pixel_cap,
        "estimated_bytes_per_pixel": ESTIMATED_RENDER_BYTES_PER_PIXEL,
        "estimated_peak_memory_bytes": estimate_render_memory_bytes(pixel_cap),
        "used_fallback_memory": used_fallback,
    }


@dataclass
class HeatmapConfig:
    activity_types: list[str] = field(default_factory=lambda: ["Run"])
    date_from: str | None = None
    date_to: str | None = None
    home_lat: float | None = None
    home_lon: float | None = None
    radius_km: float | None = None
    gps_spread_min_m: float = 200
    meters_per_pixel: float = 3
    max_grid_pixels: int = 250_000_000
    memory_mode: str = "auto"
    memory_safety_fraction: float = 0.45
    padding_m: float = 500
    track_clip_radius_km: float | None = None
    blur_sigma_m: float = 25
    map_opacity: float = 0.85
    default_visible_layer: str = "Raw GPS tracks"
    count_linear_visibility_floor: float = 0.06
    count_log_visibility_floor: float = 0.08
    count_log_gamma: float = 0.65
    speed_min_ms: float | None = None
    speed_max_ms: float | None = None
    hr_min_bpm: float | None = None
    hr_max_bpm: float | None = None
    hr_visibility_floor: float = 0.12
    hr_missing_color: tuple[float, float, float] = (0.65, 0.65, 0.65)
    hr_missing_opacity: float = 0.45
    auto_range_pct: float = 15


@dataclass
class HeatmapResult:
    output_html: Path
    export_dir: Path
    activity_count: int
    track_count: int
    total_points: int
    home_lat: float
    home_lon: float
    effective_meters_per_pixel: float
    grid_width: int
    grid_height: int
    grid_pixels: int
    summary: dict[str, object]


def _emit(status: StatusCallback | None, message: str) -> None:
    if status:
        status(message)
    else:
        print(message)


def safe_extract_zip(zip_path: Path, destination: Path) -> Path:
    """Extract a Strava zip safely and return the export root."""
    zip_path = Path(zip_path)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    dest_resolved = destination.resolve()

    with zipfile.ZipFile(zip_path) as zf:
        members = zf.infolist()
        if not members:
            raise HeatmapError("The uploaded zip is empty.")
        for member in members:
            target = (destination / member.filename).resolve()
            if target != dest_resolved and dest_resolved not in target.parents:
                raise HeatmapError("The zip contains unsafe paths and was not extracted.")
        zf.extractall(destination)

    return find_export_root(destination)


def prepare_uploaded_export(zip_bytes: bytes, filename: str, app_data_dir: Path = Path("app_data")) -> Path:
    """Persist and extract an uploaded Strava zip into a deterministic local workspace."""
    digest = hashlib.sha256(zip_bytes).hexdigest()[:16]
    upload_dir = app_data_dir / "uploads"
    export_dir = app_data_dir / "exports" / digest
    upload_dir.mkdir(parents=True, exist_ok=True)
    zip_path = upload_dir / f"{digest}_{Path(filename).name}"
    zip_path.write_bytes(zip_bytes)

    if export_dir.exists():
        try:
            return find_export_root(export_dir)
        except HeatmapError:
            shutil.rmtree(export_dir)

    export_dir.mkdir(parents=True, exist_ok=True)
    return safe_extract_zip(zip_path, export_dir)


def find_export_root(path: Path) -> Path:
    """Find the directory that contains activities.csv."""
    path = Path(path)
    if (path / "activities.csv").exists():
        return path
    matches = list(path.rglob("activities.csv"))
    if not matches:
        raise HeatmapError("Could not find activities.csv in the Strava export.")
    return matches[0].parent


def validate_export(export_dir: Path) -> Path:
    export_dir = find_export_root(export_dir)
    activities_csv = export_dir / "activities.csv"
    activities_dir = export_dir / "activities"
    if not activities_csv.exists():
        raise HeatmapError("The export is missing activities.csv.")
    if not activities_dir.exists() or not any(activities_dir.glob("*.fit.gz")):
        raise HeatmapError("The export is missing activities/*.fit.gz files.")
    return export_dir


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return radius * 2 * math.asin(math.sqrt(a))


def get_gps_start(filepath: Path) -> tuple[float | None, float | None, float | None]:
    lats: list[float] = []
    lons: list[float] = []
    try:
        with gzip.open(filepath, "rb") as f:
            for msg in fitparse.FitFile(f).get_messages("record"):
                data = fit_message_data(msg)
                if data.get("position_lat") is not None and data.get("position_long") is not None:
                    lats.append(data["position_lat"] * (180 / 2**31))
                    lons.append(data["position_long"] * (180 / 2**31))
    except Exception:
        pass
    if not lats:
        return None, None, None
    mid_lat = (min(lats) + max(lats)) / 2
    spread_m = max(
        (max(lats) - min(lats)) * 111_000,
        (max(lons) - min(lons)) * 111_000 * math.cos(math.radians(mid_lat)),
    )
    return lats[0], lons[0], spread_m


def detect_home(runs_with_gps: pd.DataFrame) -> tuple[float, float, int]:
    cell_lats: dict[tuple[float, float], list[float]] = {}
    cell_lons: dict[tuple[float, float], list[float]] = {}
    for lat, lon in zip(runs_with_gps["start_lat"], runs_with_gps["start_lon"]):
        cell = (round(lat, 2), round(lon, 2))
        cell_lats.setdefault(cell, []).append(lat)
        cell_lons.setdefault(cell, []).append(lon)
    if not cell_lats:
        raise HeatmapError("No GPS activities remain after filtering.")
    best_cell = max(cell_lats, key=lambda c: len(cell_lats[c]))
    home_lat = sum(cell_lats[best_cell]) / len(cell_lats[best_cell])
    home_lon = sum(cell_lons[best_cell]) / len(cell_lons[best_cell])
    return home_lat, home_lon, len(cell_lats[best_cell])


def load_fit_track_full(filepath: Path) -> list[list[float | int | None]]:
    points: list[list[float | int | None]] = []
    try:
        with gzip.open(filepath, "rb") as f:
            for msg in fitparse.FitFile(f).get_messages("record"):
                data = fit_message_data(msg)
                if data.get("position_lat") is None or data.get("position_long") is None:
                    continue
                lat = data["position_lat"] * (180 / 2**31)
                lon = data["position_long"] * (180 / 2**31)
                speed = data.get("enhanced_speed") if data.get("enhanced_speed") is not None else data.get("speed")
                hr = data.get("heart_rate")
                alt = (
                    data.get("enhanced_altitude")
                    if data.get("enhanced_altitude") is not None
                    else data.get("altitude")
                )
                points.append([lat, lon, speed, hr, alt])
    except Exception as exc:
        raise HeatmapError(f"Could not parse {filepath.name}: {exc}") from exc
    return points


def _read_json(path: Path, fallback):
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return fallback


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def load_filtered_runs(
    export_dir: Path,
    config: HeatmapConfig,
    cache_dir: Path,
    status: StatusCallback | None = None,
) -> tuple[pd.DataFrame, float, float]:
    activities_csv = export_dir / "activities.csv"
    dataframe: pd.DataFrame = pd.read_csv(activities_csv)
    if "Activity Type" not in dataframe.columns or "Filename" not in dataframe.columns:
        raise HeatmapError("activities.csv does not look like a Strava activities export.")

    dataframe["Activity Date"] = pd.to_datetime(dataframe["Activity Date"], format="mixed", dayfirst=True)
    activity_mask = dataframe["Activity Type"].isin(config.activity_types)
    runs = cast(pd.DataFrame, dataframe.loc[activity_mask].copy())
    _emit(status, f"Total matching activities in export: {len(runs)}")
    if runs.empty:
        raise HeatmapError("No activities match the selected activity types.")

    date_from = pd.Timestamp(config.date_from) if config.date_from else pd.Timestamp.min
    date_to = pd.Timestamp(config.date_to) if config.date_to else pd.Timestamp(date.today())
    date_mask = runs["Activity Date"].between(date_from, date_to)
    runs = cast(pd.DataFrame, runs.loc[date_mask].copy())
    _emit(status, f"After date filter ({date_from.date()} - {date_to.date()}): {len(runs)}")
    if runs.empty:
        raise HeatmapError("No activities remain after the selected date filter.")

    gps_cache_path = cache_dir / "gps_start_cache.json"
    gps_cache = _read_json(gps_cache_path, {})
    rows: list[dict[str, Any]] = []
    for _, row in runs.iterrows():
        filename = str(row["Filename"])
        if filename in gps_cache:
            lat, lon, spread = gps_cache[filename]
        else:
            lat, lon, spread = get_gps_start(export_dir / filename)
            gps_cache[filename] = [lat, lon, spread]
        rows.append({**row, "start_lat": lat, "start_lon": lon, "gps_spread_m": spread})
    _write_json(gps_cache_path, gps_cache)

    runs = pd.DataFrame(rows)
    gps_mask = runs["start_lat"].notna() & (runs["gps_spread_m"] >= config.gps_spread_min_m)
    runs = cast(pd.DataFrame, runs.loc[gps_mask].copy())
    _emit(status, f"After removing no-GPS / indoor: {len(runs)}")
    if runs.empty:
        raise HeatmapError("No GPS activities remain after removing indoor/no-GPS activities.")

    if config.home_lat is None or config.home_lon is None:
        home_lat, home_lon, n_home_starts = detect_home(runs)
        _emit(
            status,
            f"Auto-detected home: {home_lat:.4f}, {home_lon:.4f} "
            f"({n_home_starts} of {len(runs)} activities started there)",
        )
    else:
        home_lat, home_lon = config.home_lat, config.home_lon
        _emit(status, f"Using manual home: {home_lat}, {home_lon}")

    runs["dist_from_home_km"] = runs.apply(
        lambda row: haversine_km(home_lat, home_lon, row["start_lat"], row["start_lon"]),
        axis=1,
    )
    if config.radius_km is not None:
        radius_mask = runs["dist_from_home_km"] <= config.radius_km
        runs = cast(pd.DataFrame, runs.loc[radius_mask].copy())
        _emit(status, f"After home-radius filter (<={config.radius_km} km): {len(runs)}")
        if runs.empty:
            raise HeatmapError("No activities remain after the home-radius filter.")
    else:
        _emit(status, f"Home-radius filter disabled: {len(runs)} activities")

    return runs, float(home_lat), float(home_lon)


def load_tracks(
    export_dir: Path,
    runs: pd.DataFrame,
    cache_dir: Path,
    status: StatusCallback | None = None,
) -> list[tuple[str, list[list[float | int | None]]]]:
    track_cache_path = cache_dir / "track_cache.json"
    track_cache = _read_json(track_cache_path, {})
    stale = [key for key, value in track_cache.items() if value and len(value[0]) < 5]
    for key in stale:
        del track_cache[key]
    if stale:
        _emit(status, f"Cleared {len(stale)} stale cache entries.")

    tracks: list[tuple[str, list[list[float | int | None]]]] = []
    for _, row in runs.iterrows():
        filename = str(row["Filename"])
        activity_date = pd.Timestamp(cast(Any, row["Activity Date"])).date()
        label = f"{activity_date} {row['Activity Name']}"
        if filename in track_cache:
            points = track_cache[filename]
        else:
            _emit(status, f"Parsing {filename} ...")
            points = load_fit_track_full(export_dir / filename)
            track_cache[filename] = points
        if points:
            tracks.append((label, points))

    _write_json(track_cache_path, track_cache)
    if not tracks:
        raise HeatmapError("No GPS tracks could be loaded from the selected activities.")

    total_points = sum(len(points) for _, points in tracks)
    all_speeds = [p[2] for _, points in tracks for p in points if p[2] is not None]
    all_hrs = [p[3] for _, points in tracks for p in points if p[3] is not None]
    all_alts = [p[4] for _, points in tracks for p in points if p[4] is not None]
    _emit(status, f"Loaded {len(tracks)} tracks, {total_points:,} GPS points")
    if all_speeds:
        _emit(
            status,
            f"Speed: {np.percentile(all_speeds, 5):.2f}-{np.percentile(all_speeds, 95):.2f} m/s "
            f"(5th-95th pct), median {np.median(all_speeds):.2f} m/s",
        )
    if all_hrs:
        _emit(
            status,
            f"HR: {np.percentile(all_hrs, 5):.0f}-{np.percentile(all_hrs, 95):.0f} bpm "
            f"(5th-95th pct), median {np.median(all_hrs):.0f} bpm",
        )
    if all_alts:
        _emit(status, f"Altitude data: {len(all_alts):,} points")
    return tracks


def build_cmap(name: str, nodes: list[tuple[float, tuple[float, float, float, float]]]):
    positions = [node[0] for node in nodes]
    cdict = {}
    for channel_index, channel_name in enumerate(("red", "green", "blue", "alpha")):
        values = [node[1][channel_index] for node in nodes]
        cdict[channel_name] = [(positions[i], values[i], values[i]) for i in range(len(positions))]
    return mcolors.LinearSegmentedColormap(name, cdict, N=512)


def build_colormaps():
    cmap_count = build_cmap(
        "count",
        [
            (0.00, (0.00, 0.00, 0.00, 0.00)),
            (0.01, (0.40, 0.10, 0.00, 0.55)),
            (0.20, (0.99, 0.30, 0.01, 0.80)),
            (0.50, (1.00, 0.65, 0.00, 0.92)),
            (0.80, (1.00, 0.92, 0.20, 0.97)),
            (1.00, (1.00, 1.00, 0.80, 1.00)),
        ],
    )
    cmap_speed_rgb = build_cmap(
        "speed",
        [
            (0.00, (0.00, 0.10, 0.40, 1.00)),
            (0.35, (0.05, 0.30, 0.80, 1.00)),
            (0.65, (0.20, 0.55, 1.00, 1.00)),
            (0.85, (0.55, 0.75, 1.00, 1.00)),
            (1.00, (0.85, 0.92, 1.00, 1.00)),
        ],
    )
    cmap_hr_rgb = build_cmap(
        "hr",
        [
            (0.00, (0.40, 0.05, 0.05, 1.00)),
            (0.35, (0.70, 0.12, 0.12, 1.00)),
            (0.65, (0.92, 0.28, 0.28, 1.00)),
            (0.85, (1.00, 0.65, 0.65, 1.00)),
            (1.00, (1.00, 0.90, 0.90, 1.00)),
        ],
    )
    cmap_elev_rgb = build_cmap(
        "elev",
        [
            (0.00, (0.12, 0.80, 0.22, 1.00)),
            (0.25, (0.06, 0.52, 0.16, 1.00)),
            (0.45, (0.06, 0.20, 0.10, 1.00)),
            (0.50, (0.18, 0.18, 0.18, 1.00)),
            (0.55, (0.22, 0.08, 0.30, 1.00)),
            (0.75, (0.52, 0.06, 0.75, 1.00)),
            (1.00, (0.82, 0.22, 1.00, 1.00)),
        ],
    )
    return cmap_count, cmap_speed_rgb, cmap_hr_rgb, cmap_elev_rgb


def presence_alpha(sample_count_grid: np.ndarray, blur_sigma: float, pct: float = 10) -> np.ndarray:
    binary = (sample_count_grid > 0).astype(np.float32)
    if not binary.any():
        return np.zeros_like(sample_count_grid, dtype=np.float32)
    blurred = gaussian_filter(binary, sigma=blur_sigma)
    sat = np.percentile(blurred[binary > 0], pct)
    return np.clip(blurred / sat, 0, 1) if sat > 0 else blurred


def _to_uri(rgba_u8: np.ndarray) -> str:
    buffer = BytesIO()
    Image.fromarray(rgba_u8, mode="RGBA").save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def _count_uri(norm: np.ndarray, cmap_count, visibility_floor: float = 0.0, gamma: float = 1.0) -> str:
    norm = np.clip(norm, 0, 1)
    norm = np.power(norm, gamma)
    if visibility_floor > 0:
        norm = np.where(norm > 0, visibility_floor + (1 - visibility_floor) * norm, 0)
    return _to_uri((cmap_count(norm) * 255).clip(0, 255).astype(np.uint8))


def _rgba_uri(rgb_norm: np.ndarray, alpha_norm: np.ndarray, cmap_rgb) -> str:
    arr = cmap_rgb(rgb_norm).copy()
    arr[:, :, 3] = alpha_norm
    return _to_uri((arr * 255).clip(0, 255).astype(np.uint8))


def _hr_uri(
    hr_rgb_norm: np.ndarray,
    hr_alpha_norm: np.ndarray,
    missing_alpha_norm: np.ndarray,
    cmap_hr_rgb,
    missing_color: tuple[float, float, float],
) -> str:
    arr = cmap_hr_rgb(hr_rgb_norm).copy()
    arr[:, :, 3] = hr_alpha_norm
    missing = missing_alpha_norm > 0
    arr[missing, 0] = missing_color[0]
    arr[missing, 1] = missing_color[1]
    arr[missing, 2] = missing_color[2]
    arr[missing, 3] = missing_alpha_norm[missing]
    return _to_uri((arr * 255).clip(0, 255).astype(np.uint8))


def _white_uri(alpha_norm: np.ndarray) -> str:
    height, width = alpha_norm.shape
    arr = np.zeros((height, width, 4), dtype=np.uint8)
    arr[:, :, :3] = 255
    arr[:, :, 3] = (alpha_norm * 255).clip(0, 255).astype(np.uint8)
    return _to_uri(arr)


def _cmap_to_css(cmap, n: int = 14) -> str:
    stops = []
    for index in range(n):
        t = index / (n - 1)
        r, g, b, a = cmap(t)
        stops.append(f"rgba({int(r * 255)},{int(g * 255)},{int(b * 255)},{a:.2f})")
    return f"linear-gradient(to right, {', '.join(stops)})"


def _pace_str(ms: float) -> str:
    secs = 1000 / ms
    return f"{int(secs // 60)}:{int(secs % 60):02d}/km"


def generate_heatmap(
    export_dir: Path,
    output_html: Path,
    config: HeatmapConfig | None = None,
    cache_dir: Path | None = None,
    status: StatusCallback | None = None,
) -> HeatmapResult:
    config = config or HeatmapConfig()
    export_dir = validate_export(Path(export_dir))
    output_html = Path(output_html)
    output_html.parent.mkdir(parents=True, exist_ok=True)
    cache_dir = Path(cache_dir) if cache_dir else export_dir / ".heatmap_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    runs, home_lat, home_lon = load_filtered_runs(export_dir, config, cache_dir, status)
    tracks = load_tracks(export_dir, runs, cache_dir, status)
    if not tracks:
        raise HeatmapError("No tracks loaded.")

    to_wm = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    from_wm = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
    utm_zone = int((home_lon + 180) / 6) + 1
    utm_base = 32700 if home_lat < 0 else 32600
    utm_crs = f"EPSG:{utm_base + utm_zone}"
    to_utm = Transformer.from_crs("EPSG:4326", utm_crs, always_xy=True)
    _emit(status, f"Rasterising in EPSG:3857; clip check via {utm_crs}")

    home_x_utm, home_y_utm = to_utm.transform(home_lon, home_lat)
    clip_m = config.track_clip_radius_km * 1000 if config.track_clip_radius_km is not None else None

    if clip_m is not None:
        clipped_wm_xs: list[float] = []
        clipped_wm_ys: list[float] = []
        for _, points in tracks:
            lats_a = np.array([p[0] for p in points])
            lons_a = np.array([p[1] for p in points])
            xs_utm, ys_utm = to_utm.transform(lons_a, lats_a)
            mask = ((xs_utm - home_x_utm) ** 2 + (ys_utm - home_y_utm) ** 2) <= clip_m**2
            if mask.any():
                xs_wm_c, ys_wm_c = to_wm.transform(lons_a[mask], lats_a[mask])
                clipped_wm_xs.extend(xs_wm_c.tolist())
                clipped_wm_ys.extend(ys_wm_c.tolist())
        if not clipped_wm_xs:
            raise HeatmapError("No GPS points remain after track clipping.")
        x_min_wm = min(clipped_wm_xs) - config.padding_m
        x_max_wm = max(clipped_wm_xs) + config.padding_m
        y_min_wm = min(clipped_wm_ys) - config.padding_m
        y_max_wm = max(clipped_wm_ys) + config.padding_m
        _emit(status, f"Grid from clipped GPS extents (clip radius: {config.track_clip_radius_km} km)")
    else:
        all_lats = np.array([p[0] for _, points in tracks for p in points])
        all_lons = np.array([p[1] for _, points in tracks for p in points])
        xs_wm_all, ys_wm_all = to_wm.transform(all_lons, all_lats)
        x_min_wm = xs_wm_all.min() - config.padding_m
        x_max_wm = xs_wm_all.max() + config.padding_m
        y_min_wm = ys_wm_all.min() - config.padding_m
        y_max_wm = ys_wm_all.max() + config.padding_m
        _emit(status, "Grid from GPS extents (no clip radius set)")

    requested_mpp = config.meters_per_pixel
    grid_width_m = x_max_wm - x_min_wm
    grid_height_m = y_max_wm - y_min_wm

    def grid_shape(mpp: float) -> tuple[int, int]:
        return int(grid_width_m / mpp) + 1, int(grid_height_m / mpp) + 1

    effective_mpp = config.meters_per_pixel
    grid_w, grid_h = grid_shape(effective_mpp)
    requested_w, requested_h = grid_shape(requested_mpp)
    requested_pixels = requested_w * requested_h
    memory_plan = memory_safe_pixel_cap(
        config.max_grid_pixels,
        memory_mode=config.memory_mode,
        memory_safety_fraction=config.memory_safety_fraction,
    )
    grid_pixel_cap = cast(int, memory_plan["grid_pixel_cap"])
    available_memory_bytes = cast(int | None, memory_plan["available_memory_bytes"])
    auto_grid_pixel_cap = cast(int | None, memory_plan["auto_grid_pixel_cap"])

    if config.memory_mode == "auto":
        fallback_note = " fallback" if memory_plan["used_fallback_memory"] else ""
        _emit(
            status,
            f"Memory available{fallback_note}: {bytes_to_gib(available_memory_bytes or 0):.1f} GiB; "
            f"auto grid cap: {auto_grid_pixel_cap:,} px; hard cap: {config.max_grid_pixels:,} px; "
            f"active cap: {grid_pixel_cap:,} px",
        )
    else:
        _emit(status, f"Memory mode fixed: using hard grid cap {grid_pixel_cap:,} px")

    if grid_w * grid_h > grid_pixel_cap:
        effective_mpp = math.sqrt((grid_width_m * grid_height_m) / grid_pixel_cap)
        effective_mpp = math.ceil(effective_mpp * 10) / 10
        grid_w, grid_h = grid_shape(effective_mpp)
        while grid_w * grid_h > grid_pixel_cap:
            effective_mpp = math.ceil((effective_mpp + 0.1) * 10) / 10
            grid_w, grid_h = grid_shape(effective_mpp)
        _emit(
            status,
            f"Requested resolution {requested_mpp:g} Mercator-m/px would create "
            f"{requested_w * requested_h:,} pixels; using {effective_mpp:g} Mercator-m/px "
            f"to stay under {grid_pixel_cap:,} pixels",
        )
    else:
        _emit(status, f"Using requested resolution: {effective_mpp:g} Mercator-m/px")
    _emit(status, f"Grid: {grid_w} x {grid_h} px at {effective_mpp:g} Mercator-m/px")
    _emit(
        status,
        f"Estimated render memory: requested {bytes_to_gib(estimate_render_memory_bytes(requested_pixels)):.1f} GiB; "
        f"effective {bytes_to_gib(estimate_render_memory_bytes(grid_w * grid_h)):.1f} GiB",
    )

    try:
        count_grid = np.zeros((grid_h, grid_w), dtype=np.float32)
        speed_sum = np.zeros((grid_h, grid_w), dtype=np.float32)
        speed_n = np.zeros((grid_h, grid_w), dtype=np.float32)
        hr_sum = np.zeros((grid_h, grid_w), dtype=np.float32)
        hr_n = np.zeros((grid_h, grid_w), dtype=np.float32)
        grad_sum = np.zeros((grid_h, grid_w), dtype=np.float32)
        grad_n = np.zeros((grid_h, grid_w), dtype=np.float32)
        elev_sum = np.zeros((grid_h, grid_w), dtype=np.float32)
        elev_n = np.zeros((grid_h, grid_w), dtype=np.float32)
    except MemoryError as exc:
        raise HeatmapError(
            "There was not enough memory to prepare the heatmap grid. "
            "Try clipping GPS tracks around home, including fewer activities, or using a coarser resolution."
        ) from exc

    def paint_segment(x1, y1, x2, y2, speed_val, hr_val, grad_val, elev_val):
        dx, dy = x2 - x1, y2 - y1
        n_steps = max(int(max(abs(dx), abs(dy))) + 1, 1)
        height, width = speed_sum.shape
        for step in range(n_steps + 1):
            t = step / n_steps
            xi = int(round(x1 + t * dx))
            yi = int(round(y1 + t * dy))
            if not (0 <= xi < width and 0 <= yi < height):
                continue
            if speed_val is not None:
                speed_sum[yi, xi] += speed_val
                speed_n[yi, xi] += 1
            if hr_val is not None:
                hr_sum[yi, xi] += hr_val
                hr_n[yi, xi] += 1
            if grad_val is not None:
                grad_sum[yi, xi] += grad_val
                grad_n[yi, xi] += 1
            if elev_val is not None:
                elev_sum[yi, xi] += elev_val
                elev_n[yi, xi] += 1

    def paint_point_values(x, y, speed_val, hr_val):
        xi = int(round(x))
        yi = int(round(y))
        if not (0 <= xi < grid_w and 0 <= yi < grid_h):
            return
        if speed_val is not None:
            speed_sum[yi, xi] += speed_val
            speed_n[yi, xi] += 1
        if hr_val is not None:
            hr_sum[yi, xi] += hr_val
            hr_n[yi, xi] += 1

    clipped_tracks: list[tuple[str, list[list[float | int | None]]]] = []
    for label, points in tracks:
        lats_a = np.array([p[0] for p in points])
        lons_a = np.array([p[1] for p in points])
        xs_utm, ys_utm = to_utm.transform(lons_a, lats_a)
        xs_wm, ys_wm = to_wm.transform(lons_a, lats_a)

        if clip_m is not None:
            mask = ((xs_utm - home_x_utm) ** 2 + (ys_utm - home_y_utm) ** 2) <= clip_m**2
            if not mask.any():
                continue
            points = [points[i] for i in range(len(points)) if mask[i]]
            xs_utm = xs_utm[mask]
            ys_utm = ys_utm[mask]
            xs_wm = xs_wm[mask]
            ys_wm = ys_wm[mask]
        clipped_tracks.append((label, points))

        px = (xs_wm - x_min_wm) / effective_mpp
        py = (y_max_wm - ys_wm) / effective_mpp

        for i in range(len(points)):
            xi = int(round(px[i]))
            yi = int(round(py[i]))
            if 0 <= xi < grid_w and 0 <= yi < grid_h:
                count_grid[yi, xi] += 1

        if len(points) == 1:
            paint_point_values(px[0], py[0], points[0][2], points[0][3])

        for i in range(len(points) - 1):
            s0, s1 = points[i][2], points[i + 1][2]
            h0, h1 = points[i][3], points[i + 1][3]
            a0, a1 = points[i][4], points[i + 1][4]
            seg_speed = (s0 + s1) / 2 if s0 is not None and s1 is not None else (s0 if s0 is not None else s1)
            seg_hr = (h0 + h1) / 2 if h0 is not None and h1 is not None else (h0 if h0 is not None else h1)
            if a0 is not None and a1 is not None:
                d_dist = math.sqrt((xs_utm[i + 1] - xs_utm[i]) ** 2 + (ys_utm[i + 1] - ys_utm[i]) ** 2)
                if d_dist >= 0.5:
                    seg_grad = abs(a1 - a0) / d_dist
                    seg_elev = a1 - a0
                else:
                    seg_grad = seg_elev = None
            else:
                seg_grad = seg_elev = None
            paint_segment(px[i], py[i], px[i + 1], py[i + 1], seg_speed, seg_hr, seg_grad, seg_elev)

    _emit(status, f"Count grid max GPS pts/px: {count_grid.max():.0f}, non-zero: {(count_grid > 0).sum():,}")
    _emit(status, f"HR data: {(hr_n > 0).sum():,} pixels")

    sigma = max(config.blur_sigma_m / effective_mpp, 0.5)
    _emit(status, f"Blur: sigma={sigma:.1f} px ~= {sigma * effective_mpp:.0f} Mercator-m")

    b_count = gaussian_filter(count_grid, sigma=sigma)
    count_norm = b_count / b_count.max()
    count_log_norm = np.log1p(b_count) / np.log1p(b_count.max())

    b_speed_sum = gaussian_filter(speed_sum, sigma=sigma)
    b_speed_n = gaussian_filter(speed_n, sigma=sigma)
    mean_speed = np.where(b_speed_n > 0, b_speed_sum / b_speed_n, 0)
    visited_speeds = mean_speed[b_speed_n > 0.01]
    if len(visited_speeds):
        s_lo = config.speed_min_ms if config.speed_min_ms is not None else np.percentile(visited_speeds, config.auto_range_pct)
        s_hi = config.speed_max_ms if config.speed_max_ms is not None else np.percentile(visited_speeds, 100 - config.auto_range_pct)
        speed_norm = np.clip((mean_speed - s_lo) / (s_hi - s_lo), 0, 1)
        speed_norm = np.where(b_speed_n > 0, speed_norm, 0)
        sw = gaussian_filter(speed_norm * (b_speed_n > 0.01).astype(float), sigma=sigma)
        sn = gaussian_filter((b_speed_n > 0.01).astype(float), sigma=sigma)
        speed_norm = np.where(sn > 0, sw / sn, 0)
    else:
        s_lo, s_hi = 1.0, 5.0
        speed_norm = np.zeros_like(mean_speed)

    b_hr_sum = gaussian_filter(hr_sum, sigma=sigma)
    b_hr_n = gaussian_filter(hr_n, sigma=sigma)
    mean_hr = np.where(b_hr_n > 0, b_hr_sum / b_hr_n, 0)
    visited_hrs = mean_hr[hr_n > 0]
    if len(visited_hrs):
        hr_lo = config.hr_min_bpm if config.hr_min_bpm is not None else np.percentile(visited_hrs, config.auto_range_pct)
        hr_hi = config.hr_max_bpm if config.hr_max_bpm is not None else np.percentile(visited_hrs, 100 - config.auto_range_pct)
        hr_norm = np.clip((mean_hr - hr_lo) / (hr_hi - hr_lo), 0, 1)
        hr_norm = np.where(b_hr_n > 0, hr_norm, 0)
        hw = gaussian_filter(hr_norm * (hr_n > 0).astype(float), sigma=sigma)
        hn = gaussian_filter((hr_n > 0).astype(float), sigma=sigma)
        hr_norm = np.where(hn > 0, hw / hn, 0)
        hr_norm = np.where(hn > 0, config.hr_visibility_floor + (1 - config.hr_visibility_floor) * hr_norm, 0)
    else:
        hr_lo, hr_hi = 100, 180
        hr_norm = np.zeros_like(mean_hr)

    b_grad_sum = gaussian_filter(grad_sum, sigma=sigma)
    b_grad_n = gaussian_filter(grad_n, sigma=sigma)
    mean_grad = np.where(b_grad_n > 0, b_grad_sum / b_grad_n, 0)
    visited_grads = mean_grad[b_grad_n > 0.01]
    n_grad_px = (grad_n > 0).sum()
    if n_grad_px and len(visited_grads):
        g_lo = np.percentile(visited_grads, config.auto_range_pct)
        g_hi = np.percentile(visited_grads, 100 - config.auto_range_pct)
        grad_norm = np.clip((mean_grad - g_lo) / (g_hi - g_lo), 0, 1)
        grad_norm = np.where(b_grad_n > 0, grad_norm, 0)
    else:
        grad_norm = np.zeros_like(mean_grad)
        g_lo = g_hi = 0.0

    b_elev_sum = gaussian_filter(elev_sum, sigma=sigma)
    b_elev_n = gaussian_filter(elev_n, sigma=sigma)
    mean_elev = np.where(b_elev_n > 0, b_elev_sum / b_elev_n, 0)
    n_elev_px = (elev_n > 0).sum()
    if n_elev_px:
        visited_elevs = mean_elev[b_elev_n > 0.01]
        e_abs_hi = max(
            abs(np.percentile(visited_elevs, config.auto_range_pct)),
            abs(np.percentile(visited_elevs, 100 - config.auto_range_pct)),
        )
        elev_norm = np.clip(mean_elev / e_abs_hi, -1, 1)
        elev_norm = np.where(b_elev_n > 0, elev_norm, 0)
        ew = gaussian_filter(elev_norm * (b_elev_n > 0.01).astype(float), sigma=sigma)
        en = gaussian_filter((b_elev_n > 0.01).astype(float), sigma=sigma)
        elev_norm = np.where(en > 0, ew / en, 0)
    else:
        elev_norm = np.zeros_like(mean_elev)

    alpha_speed = presence_alpha(speed_n, sigma)
    alpha_hr = presence_alpha(hr_n, sigma)
    route_presence = presence_alpha(count_grid, sigma)
    alpha_hr_missing = np.where((route_presence > 0) & (alpha_hr <= 0), route_presence * config.hr_missing_opacity, 0)
    _emit(status, f"HR missing: {(alpha_hr_missing > 0).sum():,} pixels shown as unknown")
    presence_grad = presence_alpha(grad_n, sigma) if n_grad_px else np.zeros_like(grad_norm)
    alpha_grad = presence_grad * (0.15 + 0.85 * grad_norm)
    alpha_elev = presence_alpha(elev_n, sigma) if n_elev_px else np.zeros_like(elev_norm)

    cmap_count, cmap_speed_rgb, cmap_hr_rgb, cmap_elev_rgb = build_colormaps()
    lon_nw, lat_nw = from_wm.transform(x_min_wm, y_max_wm)
    lon_se, lat_se = from_wm.transform(x_max_wm, y_min_wm)
    bounds = [[lat_se, lon_nw], [lat_nw, lon_se]]
    centre = [home_lat, home_lon]

    _emit(status, "Rendering layers...")
    layers = [
        (
            "Frequency (linear)",
            _count_uri(count_norm, cmap_count, config.count_linear_visibility_floor),
            config.default_visible_layer == "Frequency (linear)",
        ),
        (
            "Frequency (log)",
            _count_uri(count_log_norm, cmap_count, config.count_log_visibility_floor, config.count_log_gamma),
            config.default_visible_layer == "Frequency (log)",
        ),
        ("Pace (average)", _rgba_uri(speed_norm, alpha_speed, cmap_speed_rgb), config.default_visible_layer == "Pace (average)"),
        (
            "Heart rate (average)",
            _hr_uri(hr_norm, alpha_hr, alpha_hr_missing, cmap_hr_rgb, config.hr_missing_color),
            config.default_visible_layer == "Heart rate (average)",
        ),
        ("Gradient (absolute)", _white_uri(alpha_grad), config.default_visible_layer == "Gradient (absolute)"),
        (
            "Gradient (change)",
            _rgba_uri((elev_norm + 1) / 2, alpha_elev, cmap_elev_rgb),
            config.default_visible_layer == "Gradient (change)",
        ),
    ]

    def legend_row(row_id, title, grad_css, label_lo, label_hi, visible=False):
        display = "block" if visible else "none"
        return f"""
    <div id="{row_id}" style="display:{display}">
      <div style="font-weight:600;margin-bottom:3px;color:#eee">{title}</div>
      <div style="height:10px;border-radius:3px;background:{grad_css};
                  border:1px solid rgba(255,255,255,0.08)"></div>
      <div style="display:flex;justify-content:space-between;
                  margin-top:3px;color:#aaa;font-size:11px">
        <span>{label_lo}</span><span>{label_hi}</span>
      </div>
    </div>"""

    freq_css = _cmap_to_css(cmap_count)
    pace_css = _cmap_to_css(cmap_speed_rgb)
    hr_css = _cmap_to_css(cmap_hr_rgb)
    heatmap_layer_names = {name for name, _, _ in layers}
    legend_display = "block" if config.default_visible_layer in heatmap_layer_names else "none"
    legend_html = f"""
<div id="heatmap-legend" style="
    display:{legend_display};
    position:fixed; bottom:28px; right:10px; z-index:9999;
    background:rgba(15,15,15,0.88);
    padding:13px 16px 14px; border-radius:9px;
    color:#ddd; font-family:sans-serif; font-size:12px;
    min-width:210px; line-height:1.4;
    border:1px solid rgba(255,255,255,0.10);
    box-shadow:0 2px 8px rgba(0,0,0,0.6);
">
  {legend_row("legend-frequency", "Frequency (linear)", freq_css, "1 pass", f"{int(count_grid.max())} passes", visible=config.default_visible_layer == "Frequency (linear)")}
  {legend_row("legend-frequency-log", "Frequency (log)", freq_css, "1 pass", f"{int(count_grid.max())} passes (log scale)", visible=config.default_visible_layer == "Frequency (log)")}
  {legend_row("legend-pace-avg", "Pace (average)", pace_css, _pace_str(s_lo), _pace_str(s_hi))}
  {legend_row("legend-heart-rate-avg", "Heart rate (average)", hr_css, f"{hr_lo:.0f} bpm; gray = unknown", f"{hr_hi:.0f} bpm")}
  {legend_row("legend-gradient", "Gradient (absolute)", "linear-gradient(to right, rgba(0,0,0,0), rgba(255,255,255,1))", f"{g_lo*100:.1f}%", f"{g_hi*100:.1f}% grade")}
  {legend_row("legend-elev-change", "Gradient (change)", _cmap_to_css(cmap_elev_rgb), "descending", "ascending")}
</div>
"""
    layer_control_css = """
<style>
  .leaflet-control-layers {
    background: rgba(15,15,15,0.88) !important;
    border: 1px solid rgba(255,255,255,0.10) !important;
    border-radius: 9px !important;
    box-shadow: 0 2px 8px rgba(0,0,0,0.6) !important;
    color: #ddd !important;
    font-family: sans-serif !important;
    font-size: 12px !important;
  }
  .leaflet-control-layers-expanded { padding: 11px 14px 13px !important; }
  .leaflet-control-layers label {
    color: #eee !important;
    font-weight: 600 !important;
    display: flex !important;
    align-items: center !important;
    gap: 6px !important;
    margin: 4px 0 !important;
  }
  .leaflet-control-layers-separator {
    border-color: rgba(255,255,255,0.12) !important;
    margin: 6px 0 !important;
  }
  .leaflet-control-layers-toggle {
    background-color: rgba(15,15,15,0.88) !important;
    border-radius: 9px !important;
    border: 1px solid rgba(255,255,255,0.10) !important;
  }
</style>
"""
    exclusive_js = """
<script>
(function() {
    var exclusiveNames = [
        "Frequency (linear)", "Frequency (log)",
        "Pace (average)", "Heart rate (average)",
        "Gradient (absolute)", "Gradient (change)"
    ];
    var legendIds = {
        "Frequency (linear)":   "legend-frequency",
        "Frequency (log)":      "legend-frequency-log",
        "Pace (average)":       "legend-pace-avg",
        "Heart rate (average)": "legend-heart-rate-avg",
        "Gradient (absolute)":  "legend-gradient",
        "Gradient (change)":    "legend-elev-change"
    };
    function showLegend(activeName) {
        var legend = document.getElementById('heatmap-legend');
        if (legend) legend.style.display = exclusiveNames.includes(activeName) ? 'block' : 'none';
        Object.keys(legendIds).forEach(function(name) {
            var el = document.getElementById(legendIds[name]);
            if (el) el.style.display = (name === activeName) ? "block" : "none";
        });
    }
    function setup() {
        var mapObj = null, overlays = null;
        for (var k in window) {
            try {
                if (!mapObj && window[k] instanceof L.Map) mapObj = window[k];
                if (!overlays && window[k] && window[k].overlays && window[k].base_layers)
                    overlays = window[k].overlays;
            } catch(e) {}
        }
        if (!mapObj || !overlays) { setTimeout(setup, 100); return; }
        mapObj.on('overlayadd', function(e) {
            if (!exclusiveNames.includes(e.name)) return;
            exclusiveNames.forEach(function(name) {
                if (name !== e.name && overlays[name] && mapObj.hasLayer(overlays[name]))
                    mapObj.removeLayer(overlays[name]);
            });
            showLegend(e.name);
        });
        mapObj.on('overlayremove', function(e) {
            if (!exclusiveNames.includes(e.name)) return;
            var anyHeatmapVisible = exclusiveNames.some(function(name) {
                return overlays[name] && mapObj.hasLayer(overlays[name]);
            });
            if (!anyHeatmapVisible) showLegend(null);
        });
    }
    document.addEventListener('DOMContentLoaded', setup);
})();
</script>
"""
    m = folium.Map(location=centre, zoom_start=14, tiles=None, control_scale=True)
    folium.TileLayer("CartoDB.DarkMatterNoLabels", name="Basemap", control=False, show=True).add_to(m)
    track_group = folium.FeatureGroup(name="Raw GPS tracks", show=config.default_visible_layer == "Raw GPS tracks")
    for label, points in clipped_tracks:
        folium.PolyLine(
            locations=[(p[0], p[1]) for p in points],
            color="#fc4c02",
            weight=1,
            opacity=0.4,
            tooltip=label,
        ).add_to(track_group)
    track_group.add_to(m)

    for name, uri, visible in layers:
        feature_group = folium.FeatureGroup(name=name, show=visible)
        ImageOverlay(
            image=uri,
            bounds=bounds,
            opacity=config.map_opacity,
            interactive=False,
            cross_origin=False,
            zindex=1,
        ).add_to(feature_group)
        feature_group.add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)
    root = cast(Any, m.get_root())
    root.html.add_child(folium.Element(layer_control_css))
    root.html.add_child(folium.Element(legend_html))
    root.html.add_child(folium.Element(exclusive_js))
    m.save(output_html)
    _emit(status, f"Saved: {output_html}")

    return HeatmapResult(
        output_html=output_html,
        export_dir=export_dir,
        activity_count=len(runs),
        track_count=len(tracks),
        total_points=sum(len(points) for _, points in tracks),
        home_lat=home_lat,
        home_lon=home_lon,
        effective_meters_per_pixel=effective_mpp,
        grid_width=grid_w,
        grid_height=grid_h,
        grid_pixels=grid_w * grid_h,
        summary={
            "hr_pixels": int((hr_n > 0).sum()),
            "missing_hr_pixels": int((alpha_hr_missing > 0).sum()),
            "count_max": int(count_grid.max()),
            "speed_range_ms": [float(s_lo), float(s_hi)],
            "hr_range_bpm": [float(hr_lo), float(hr_hi)],
            "memory_mode": config.memory_mode,
            "available_memory_gib": round(bytes_to_gib(available_memory_bytes or 0), 2)
            if available_memory_bytes is not None
            else None,
            "memory_safety_fraction": config.memory_safety_fraction,
            "auto_grid_pixel_cap": auto_grid_pixel_cap,
            "active_grid_pixel_cap": grid_pixel_cap,
            "estimated_bytes_per_pixel": ESTIMATED_RENDER_BYTES_PER_PIXEL,
            "estimated_peak_memory_gib": round(bytes_to_gib(estimate_render_memory_bytes(grid_w * grid_h)), 2),
            "requested_grid_pixels": requested_pixels,
            "requested_meters_per_pixel": requested_mpp,
            "used_fallback_memory": bool(memory_plan["used_fallback_memory"]),
        },
    )


def default_output_path(base_dir: Path = Path("outputs/app")) -> Path:
    base_dir.mkdir(parents=True, exist_ok=True)
    existing = len(list(base_dir.glob("heatmap_*.html"))) + 1
    return base_dir / f"heatmap_{existing:03d}.html"
