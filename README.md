# Running Heatmap

Create a local interactive heatmap web UI from a Strava data export. The app runs on your own computer: Strava files are not uploaded to a server.

Based on [moresamwilson/running-heatmap](https://github.com/moresamwilson/running-heatmap) and optimized for general use on Windows and Mac.

The generated map includes:

| Layer | Shows |
|---|---|
| Raw GPS tracks | Thin original GPS lines |
| Frequency (linear) | How often each path was used |
| Frequency (log) | Frequency with one-off routes easier to see |
| Pace (average) | Average pace per route pixel |
| Heart rate (average) | Average HR; gray means GPS exists but HR is missing |
| Gradient (absolute) | Steepness |
| Gradient (change) | Descending vs ascending |

## For Most Users

### macOS

Double-click:

```text
scripts/run_mac.command
```

### Windows

Double-click:

```text
scripts/run_windows.bat
```

The first launch installs the local Python dependencies and may take a few minutes. After that, the app opens in your browser at `localhost`.

## Using The App

1. Request your Strava export: **Settings -> My Account -> Download or Delete Your Account -> Download Request**.
2. Download the Strava export zip.
3. Open the Running Heatmap app.
4. Upload the Strava export zip.
5. Click **Generate heatmap**.
6. Preview the map and download the generated `heatmap.html`.

Generated maps are saved locally under:

```text
outputs/app/
```

Uploaded and extracted exports are stored locally under:

```text
app_data/
```

## Developer Workflow

Use `python3` in place of `python` on systems that do not provide a `python` command.

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

Run the local web UI:

```bash
python -m streamlit run app.py
```

Run the VS Code debug notebook:

```text
heatmap.ipynb
```

The notebook uses the same `heatmap_core.py` module as the app, so debugging and app generation follow the same code path.

## Build A Shareable App

Builds must be created on the target operating system:

| Platform | Build command | Output |
|---|---|---|
| macOS | `scripts/build_mac.sh` | `dist/RunningHeatmap-mac.zip` |
| Windows | `scripts\build_windows.bat` | `dist\RunningHeatmap-windows.zip` |

The packaged app opens a local browser window and stores generated files in the user's local application data folder. When the browser tab is closed, the local app server shuts down automatically after a short grace period. Signed installers, notarization, and app-store style distribution are not included yet.

The VS Code and notebook workflow still works through `app.py`, `launcher.py`, and `heatmap.ipynb`.

Create a clean source release:

```bash
python scripts/package_source_release.py
```

Check a source or app release before publishing:

```bash
python scripts/check_release.py dist/RunningHeatmap-mac.zip
```

## Release, Privacy, and Platform Limits

- The app uses user-downloaded Strava bulk export zips only. It does not use Strava OAuth credentials or the Strava API, so Strava API rate limits do not apply in the current form.
- Running Heatmap is not affiliated with, sponsored by, or endorsed by Strava.
- Uploaded exports and generated maps can contain sensitive location history. Do not include `strava_export/`, `app_data/`, `cache/`, `outputs/`, or personal export zips in public releases.
- Generated HTML uses external CARTO basemap tiles through Folium. Review CARTO's terms before larger public or commercial distribution.
- The app is designed for local use: Strava files are processed on the user's own computer and are not uploaded to a hosted server by this project.
