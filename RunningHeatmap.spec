# -*- mode: python ; coding: utf-8 -*-

import sys
from PyInstaller.utils.hooks import collect_data_files, copy_metadata


mac_icon = "assets/running_heatmap_icon.icns"
win_icon = "assets/running_heatmap_icon.ico"
exe_icon = win_icon if sys.platform.startswith("win") else None
bundle_icon = mac_icon if sys.platform == "darwin" else None

datas = [
    ("app.py", "."),
    ("heatmap_core.py", "."),
    (".streamlit/config.toml", ".streamlit"),
    ("LICENSE", "legal"),
    ("NOTICE", "legal"),
    ("THIRD_PARTY_NOTICES.md", "legal"),
    ("README.md", "legal"),
]
datas += collect_data_files("streamlit")
datas += copy_metadata("streamlit")

block_cipher = None


a = Analysis(
    ["launcher.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "heatmap_core",
        "fitparse",
        "folium",
        "folium.raster_layers",
        "branca",
        "matplotlib.colors",
        "numpy",
        "pandas",
        "PIL.Image",
        "psutil",
        "pyproj",
        "scipy.ndimage",
        "streamlit.web.cli",
        "streamlit.runtime.scriptrunner.magic_funcs",
        "streamlit.runtime.caching",
        "scipy._lib.array_api_compat.numpy.fft",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="RunningHeatmap",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=exe_icon,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="RunningHeatmap",
)

app = BUNDLE(
    coll,
    name="RunningHeatmap.app",
    icon=bundle_icon,
    bundle_identifier="local.runningheatmap.app",
)
