from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from heatmap_core import HeatmapConfig, HeatmapError, generate_heatmap, prepare_uploaded_export


APP_ROOT_DIR = Path(os.environ.get("RUNNING_HEATMAP_DATA_DIR", "."))
APP_DATA_DIR = APP_ROOT_DIR / "app_data"
OUTPUT_DIR = APP_ROOT_DIR / "outputs" / "app"


def _optional_text(value: str) -> str | None:
    value = value.strip()
    return value or None


def _upload_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def _request_generation() -> None:
    st.session_state["heatmap_generate_requested"] = True
    st.session_state["heatmap_generating"] = True


def _normal_status_message(message: str) -> str:
    if message.startswith("Parsing "):
        return "Reading GPS files..."
    if message.startswith("Total matching activities"):
        return "Checking activities..."
    if message.startswith("After date filter"):
        return "Filtering activities..."
    if message.startswith("After removing no-GPS") or message.startswith("Loaded "):
        return "Loading GPS tracks..."
    if message.startswith("Auto-detected home") or message.startswith("Using manual home"):
        return "Finding the map start location..."
    if message.startswith("Home-radius") or message.startswith("After home-radius"):
        return "Selecting activities..."
    if (
        message.startswith("Requested resolution")
        or message.startswith("Effective resolution")
        or message.startswith("Grid:")
        or message.startswith("Grid from")
        or message.startswith("Memory available")
        or message.startswith("Memory mode")
        or message.startswith("Estimated render memory")
    ):
        return "Choosing a safe map detail level..."
    if (
        message.startswith("Raster bounds")
        or message.startswith("Blur:")
        or message.startswith("Built layer")
        or message.startswith("Count grid")
        or message.startswith("HR data")
        or message.startswith("HR missing")
        or message.startswith("Rendering layers")
    ):
        return "Rendering heatmap layers..."
    if message.startswith("Saved:"):
        return "Saving the heatmap..."
    if message.startswith("Using export folder"):
        return "Prepared Strava export."
    return "Generating heatmap..."


st.set_page_config(page_title="Running Heatmap", layout="wide")

if "heatmap_generate_requested" not in st.session_state:
    st.session_state["heatmap_generate_requested"] = False
if "heatmap_generating" not in st.session_state:
    st.session_state["heatmap_generating"] = False
if "dev_mode" not in st.session_state:
    st.session_state["dev_mode"] = False

st.title("Running Heatmap")
st.caption("Upload a Strava export zip. Everything is processed locally on this computer.")

with st.sidebar:
    st.header("Heatmap settings")
    activity_types = st.multiselect(
        "Activity types",
        ["Run", "Ride", "Hike", "Walk", "VirtualRun", "TrailRun"],
        default=["Run"],
        help="Choose which Strava activity types to include.",
    )
    date_from = _optional_text(st.text_input("From date", placeholder="YYYY-MM-DD"))
    date_to = _optional_text(st.text_input("To date", placeholder="YYYY-MM-DD"))

    include_all_starts = st.checkbox("Include all start locations", value=True)
    radius_km = None
    if not include_all_starts:
        radius_km = st.number_input("Max start distance from home (km)", min_value=1.0, value=50.0, step=5.0)

    with st.expander("Advanced"):
        dev_mode_enabled = bool(st.session_state["dev_mode"])
        manual_home = st.checkbox("Set home manually", value=False)
        home_lat = home_lon = None
        if manual_home:
            home_lat = st.number_input("Home latitude", value=51.0, format="%.6f")
            home_lon = st.number_input("Home longitude", value=4.0, format="%.6f")

        track_clip_enabled = st.checkbox("Clip GPS tracks around home", value=False)
        track_clip_radius_km = None
        if track_clip_enabled:
            track_clip_radius_km = st.number_input("Track clip radius (km)", min_value=1.0, value=25.0, step=5.0)

        meters_per_pixel = st.number_input("Requested resolution (m/px)", min_value=1.0, value=3.0, step=1.0)
        memory_mode = "auto"
        memory_safety_fraction = 0.45
        max_grid_pixels = 250_000_000
        if dev_mode_enabled:
            memory_mode_label = st.selectbox("Memory mode", ["Automatic", "Fixed hard cap"], index=0)
            memory_mode = "auto" if memory_mode_label == "Automatic" else "fixed"
            memory_safety_fraction = st.slider(
                "Memory safety fraction",
                min_value=0.10,
                max_value=0.80,
                value=0.45,
                step=0.05,
                help="Only this share of currently available memory may be used for the estimated render peak.",
            )
            max_grid_pixels = st.number_input(
                "Hard memory safety cap (pixels)",
                min_value=25_000_000,
                max_value=400_000_000,
                value=250_000_000,
                step=25_000_000,
            )
        blur_sigma_m = st.number_input("Line softness (m)", min_value=5.0, value=25.0, step=5.0)
        default_visible_layer = st.selectbox(
            "Layer visible at startup",
            [
                "Raw GPS tracks",
                "Frequency (linear)",
                "Frequency (log)",
                "Pace (average)",
                "Heart rate (average)",
                "Gradient (absolute)",
                "Gradient (change)",
            ],
            index=0,
        )
        dev_mode = st.toggle(
            "Developer mode",
            help="Show processing logs and technical generation details.",
            key="dev_mode",
        )

uploaded = st.file_uploader("Strava export zip", type=["zip"])

if not uploaded:
    st.info(
        "Request your data from Strava, then upload the downloaded export zip here. "
        "[How to export your Strava data]"
        "(https://support.strava.com/hc/en-us/articles/216918437-Exporting-your-Data-and-Bulk-Export)"
    )
    st.stop()

if not activity_types:
    st.warning("Choose at least one activity type.")
    st.stop()

st.button(
    "Generate heatmap",
    type="primary",
    disabled=st.session_state["heatmap_generating"],
    on_click=_request_generation,
)
if not st.session_state["heatmap_generate_requested"]:
    st.stop()
st.session_state["heatmap_generate_requested"] = False

zip_bytes = uploaded.getvalue()
upload_id = _upload_digest(zip_bytes)
output_path = OUTPUT_DIR / f"heatmap_{upload_id}_{int(time.time())}.html"
logs: list[str] = []
progress_box = st.empty()
log_box = st.empty() if dev_mode else None


def status(message: str) -> None:
    logs.append(message)
    if log_box is not None:
        log_box.code("\n".join(logs[-25:]))
    else:
        progress_box.info(_normal_status_message(message))


config = HeatmapConfig(
    activity_types=activity_types,
    date_from=date_from,
    date_to=date_to,
    home_lat=home_lat,
    home_lon=home_lon,
    radius_km=radius_km,
    meters_per_pixel=meters_per_pixel,
    max_grid_pixels=int(max_grid_pixels),
    memory_mode=memory_mode,
    memory_safety_fraction=memory_safety_fraction,
    track_clip_radius_km=track_clip_radius_km,
    blur_sigma_m=blur_sigma_m,
    default_visible_layer=default_visible_layer,
)

try:
    with st.spinner("Preparing Strava export..."):
        export_dir = prepare_uploaded_export(zip_bytes, uploaded.name, APP_DATA_DIR)
    status(f"Using export folder: {export_dir}")

    with st.spinner("Generating heatmap. This can take a few minutes..."):
        result = generate_heatmap(
            export_dir=export_dir,
            output_html=output_path,
            config=config,
            cache_dir=APP_DATA_DIR / "cache" / upload_id,
            status=status,
        )
except HeatmapError as exc:
    st.session_state["heatmap_generating"] = False
    st.error(str(exc))
    st.stop()
except MemoryError:
    st.session_state["heatmap_generating"] = False
    st.error(
        "The heatmap still ran out of memory. Try clipping GPS tracks around home, "
        "including fewer activities, or using a coarser resolution."
    )
    st.stop()
except Exception as exc:
    st.session_state["heatmap_generating"] = False
    st.exception(exc)
    st.stop()
else:
    st.session_state["heatmap_generating"] = False
    progress_box.empty()
    st.success("Heatmap generated.")
    result_summary = {
        "activities": result.activity_count,
        "tracks": result.track_count,
        "gps_points": result.total_points,
        "effective_meters_per_pixel": round(result.effective_meters_per_pixel, 2),
        "grid_pixels": result.grid_pixels,
        "home": [round(result.home_lat, 5), round(result.home_lon, 5)],
        "memory": {
            "mode": result.summary.get("memory_mode"),
            "available_gib": result.summary.get("available_memory_gib"),
            "safety_fraction": result.summary.get("memory_safety_fraction"),
            "requested_grid_pixels": result.summary.get("requested_grid_pixels"),
            "active_grid_pixel_cap": result.summary.get("active_grid_pixel_cap"),
            "estimated_peak_memory_gib": result.summary.get("estimated_peak_memory_gib"),
            "requested_meters_per_pixel": result.summary.get("requested_meters_per_pixel"),
            "effective_meters_per_pixel": round(result.effective_meters_per_pixel, 2),
        },
    }
    if dev_mode:
        st.write(result_summary)
    else:
        st.caption(
            f"Processed {result.activity_count} activities and {result.track_count} GPS tracks."
        )

    html = result.output_html.read_text()
    st.download_button(
        "Download heatmap HTML",
        data=html,
        file_name="heatmap.html",
        mime="text/html",
    )

    st.subheader("Preview")
    components.html(html, height=760, scrolling=True)
