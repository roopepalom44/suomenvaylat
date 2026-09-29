"""Build an installable QGIS plugin ZIP without caches or test artifacts."""

from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parent
PLUGIN = ROOT / "suomenvaylat_qgis"
# Hallinnolliset aluejaot jaetaan ArcGIS Pro -työkalun kanssa; repossa on yksi kopio.
ADMIN_GPKG = ROOT.parent / "Toolboxes" / "Resources" / "hallinnolliset_aluejaot.gpkg"
ADMIN_GPKG_ARCNAME = f"{PLUGIN.name}/resources/hallinnolliset_aluejaot.gpkg"
VERSION = next(line.split("=", 1)[1].strip() for line in (PLUGIN / "metadata.txt").read_text(encoding="utf-8").splitlines()
               if line.startswith("version="))
OUTPUT = ROOT / "dist" / f"Suomenvaylat-QGIS-{VERSION}.zip"
OUTPUT.parent.mkdir(exist_ok=True)
ALLOWED = {".py", ".txt", ".png", ".json"}
if not ADMIN_GPKG.is_file():
    raise SystemExit(f"Missing administrative boundaries: {ADMIN_GPKG}")


def write_plugin(archive):
    for path in sorted(PLUGIN.rglob("*")):
        if path.is_file() and path.suffix.lower() in ALLOWED and "__pycache__" not in path.parts:
            archive.write(path, path.relative_to(ROOT))
    archive.write(ADMIN_GPKG, ADMIN_GPKG_ARCNAME)


with ZipFile(OUTPUT, "w", ZIP_DEFLATED, compresslevel=9) as archive:
    write_plugin(archive)
with ZipFile(OUTPUT) as archive:
    names = archive.namelist()
    assert f"{PLUGIN.name}/metadata.txt" in names
    assert f"{PLUGIN.name}/__init__.py" in names
    assert f"{PLUGIN.name}/osm_geometry.py" in names
    assert ADMIN_GPKG_ARCNAME in names
print(OUTPUT)

WINDOWS_OUTPUT = ROOT / "dist" / f"Suomenvaylat-QGIS-{VERSION}-Windows.zip"
with ZipFile(WINDOWS_OUTPUT, "w", ZIP_DEFLATED, compresslevel=9) as archive:
    write_plugin(archive)
    archive.write(ROOT / "install_windows.bat", "install_windows.bat")
    archive.write(ROOT / "install_windows.ps1", "install_windows.ps1")
with ZipFile(WINDOWS_OUTPUT) as archive:
    assert archive.testzip() is None
    assert "install_windows.bat" in archive.namelist()
    assert ADMIN_GPKG_ARCNAME in archive.namelist()
print(WINDOWS_OUTPUT)
