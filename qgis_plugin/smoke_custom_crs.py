"""Live download checks with a Web Mercator project and custom selection."""

import gc
import sys
import tempfile
from pathlib import Path

from qgis.core import (
    QgsApplication, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsGeometry, QgsProject, QgsRasterLayer, QgsRectangle, QgsVectorLayer,
)

QgsApplication.setPrefixPath("C:/Program Files/QGIS 3.44.14/apps/qgis-ltr", True)
app = QgsApplication([], False)
app.initQgis()
sys.path.insert(0, str(Path(__file__).resolve().parent))

from suomenvaylat_qgis.services import catalog, download, selection_geometry

project = QgsProject.instance()
tm35 = QgsCoordinateReferenceSystem("EPSG:3067")
mercator = QgsCoordinateReferenceSystem("EPSG:3857")
wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
project.setCrs(mercator)
selection = QgsGeometry.fromRect(QgsRectangle(385000, 6670000, 386000, 6671000))
selection.transform(QgsCoordinateTransform(tm35, mercator, project))
helsinki, helsinki_crs = selection_geometry("Kunta/Kaupunki", ["Helsinki"])
helsinki.transform(QgsCoordinateTransform(helsinki_crs, mercator, project))

cases = [
    ("Väylä", "tiestotiedot:aidat", False),
    ("DigiRoad", "digiroad:dr_pysakki", False),
    ("Kapsi", "taustakartta", True),
    ("OpenStreetMap", "osm_bus_stops", False),
]
with tempfile.TemporaryDirectory(prefix="suomenvaylat_mercator_", ignore_cleanup_errors=True) as folder:
    for index, (source, identifier, raster) in enumerate(cases):
        entries, errors = catalog(source)
        assert not errors, errors
        entry = next(item for item in entries if item["id"] == identifier)
        path = Path(folder) / f"case_{index}{'.tif' if raster else '.gpkg'}"
        count = download(entry, helsinki if source == "Väylä" else selection,
                         mercator, path)
        layer = QgsRasterLayer(str(path), source) if raster else QgsVectorLayer(str(path), source, "ogr")
        assert count > 0 and layer.isValid() and layer.crs().authid() == "EPSG:3067"
        point = QgsCoordinateTransform(layer.crs(), wgs84, project).transform(layer.extent().center())
        assert 18 <= point.x() <= 33 and 59 <= point.y() <= 72, (source, point.x(), point.y())
        print(f"PASS {source} / EPSG:3857 mask: lon={point.x():.4f} lat={point.y():.4f}", flush=True)
        project.removeAllMapLayers()
        layer = None
        gc.collect()

print(f"All {len(cases)} custom-CRS download paths passed", flush=True)

# A saved project can deliberately have no CRS even though it already has a basemap.
project.addMapLayer(QgsVectorLayer("Point?crs=EPSG:3857", "existing basemap", "memory"))
project.setCrs(QgsCoordinateReferenceSystem())
assert not project.crs().isValid()
with tempfile.TemporaryDirectory(prefix="suomenvaylat_no_project_crs_", ignore_cleanup_errors=True) as folder:
    entry = next(item for item in catalog("Väylä")[0] if item["id"] == "tiestotiedot:aidat")
    path = Path(folder) / "aidat.gpkg"
    assert download(entry, helsinki, mercator, path) > 0
    assert project.crs().authid() == "EPSG:3067"
    layer = QgsVectorLayer(str(path), "aidat", "ogr")
    assert layer.isValid() and layer.crs().authid() == "EPSG:3067"
    project.removeAllMapLayers()
    layer = None
    gc.collect()
print("Project without CRS adopts the downloaded layer's CRS", flush=True)
