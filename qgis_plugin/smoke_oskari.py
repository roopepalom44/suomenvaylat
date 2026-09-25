"""Live Traficom Oskari catalog and data check in QGIS's Python environment."""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem, QgsGeometry,
                       QgsProject, QgsRasterLayer, QgsRectangle, QgsVectorLayer)

QgsApplication.setPrefixPath("C:/Program Files/QGIS 3.44.14/apps/qgis-ltr", True)
app = QgsApplication([], False)
app.initQgis()
sys.path.insert(0, str(Path(__file__).resolve().parent))
from suomenvaylat_qgis.services import catalog, download

entries, errors = catalog("Traficom Oskari")
assert len(entries) >= 76, (len(entries), errors)
assert {entry["kind"] for entry in entries} >= {"wfs", "oskari_wfs", "oskari_wms", "oskari_wmts"}
print("catalog", len(entries), {kind: sum(e["kind"] == kind for e in entries)
                              for kind in {e["kind"] for e in entries}}, flush=True)
crs = QgsCoordinateReferenceSystem("EPSG:3067")
with tempfile.TemporaryDirectory(prefix="suomenvaylat_oskari_qgis_") as folder:
    vector_mask = QgsGeometry.fromRect(QgsRectangle(260000, 6600000, 420000, 6700000))
    vector = next(entry for entry in entries if entry["kind"] == "oskari_wfs" and entry["catalog_id"] == "112")
    vector_path = Path(folder) / "oskari.gpkg"
    count = download(vector, vector_mask, crs, vector_path)
    assert count > 0
    saved_vector = QgsVectorLayer(str(vector_path), "oskari", "ogr")
    assert saved_vector.featureCount() == count
    assert saved_vector.crs().authid() == "EPSG:3067"
    assert "ID" in saved_vector.fields().names()
    saved_vector = None
    print("vector", count, flush=True)

    raster_mask = QgsGeometry.fromRect(QgsRectangle(385000, 6670000, 405000, 6690000))
    wms = next(entry for entry in entries if entry["kind"] == "oskari_wms" and entry["layer_name"])
    wms_path = Path(folder) / "wms.tif"
    assert download(wms, raster_mask, crs, wms_path) == 1
    saved_wms = QgsRasterLayer(str(wms_path), "wms")
    assert saved_wms.isValid() and saved_wms.crs().authid() == "EPSG:3067"
    assert saved_wms.width() > 0 and saved_wms.height() > 0
    saved_wms = None
    print("wms", wms_path.stat().st_size, flush=True)

    wmts = next(entry for entry in entries if entry["kind"] == "oskari_wmts" and "Yleiskartat 250k" in entry["layer_name"])
    wmts_path = Path(folder) / "wmts.tif"
    assert download(wmts, raster_mask, crs, wmts_path) == 1
    saved_wmts = QgsRasterLayer(str(wmts_path), "wmts")
    assert saved_wmts.isValid() and saved_wmts.crs().authid() == "EPSG:3067"
    assert saved_wmts.width() > 0 and saved_wmts.height() > 0
    saved_wmts = None
    print("wmts", wmts_path.stat().st_size, flush=True)
    QgsProject.instance().clear()

print("Oskari QGIS smoke passed", flush=True)
