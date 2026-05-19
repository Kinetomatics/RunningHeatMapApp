# Third-Party Notices

Running Heatmap depends on third-party open source Python packages. Direct
runtime and packaging dependencies are listed in `requirements.txt`:

| Package | Version |
|---|---|
| numpy | 2.4.4 |
| pandas | 3.0.2 |
| fitparse | 1.2.0 |
| folium | 0.20.0 |
| pyproj | 3.7.2 |
| scipy | 1.17.1 |
| Pillow | 12.2.0 |
| matplotlib | 3.10.9 |
| psutil | Resolved at install/build time |
| streamlit | Resolved at install/build time |
| pyinstaller | Resolved at install/build time |

Binary builds are produced with PyInstaller and include installed package
metadata and license files from bundled dependencies where those packages
provide them. Review the bundled `.dist-info` metadata in a built app for the
exact resolved versions and license texts.
