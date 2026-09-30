"""Live CRS and Finland-location checks for each public download source."""

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
crs = QgsCoordinateReferenceSystem("EPSG:3067")
wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
small = QgsGeometry.fromRect(QgsRectangle(385000, 6670000, 386000, 6671000))
city, _ = selection_geometry("Kunta/Kaupunki", ["Helsinki"])
coast = QgsGeometry.fromRect(QgsRectangle(260000, 6600000, 420000, 6700000))
cases = [
    ("Väylä", "tiestotiedot:aidat", [small, city]),
    ("DigiRoad", "digiroad:dr_pysakki", [small, city]),
    ("Liiteri", "liiteri_taajamat:taajamat24", [small, city]),
    ("Syke", "inspire_ps:PS.ProtectedSitesLaillaRakennusperinnonSuojelemisestaSuojeltuKohde", [small, city]),
    ("Tilastokeskus", "vaestoruutu:vaki2024_1km", [small, city]),
    ("Karttapaikka", "gn:NamedPlace", [small, city]),
    ("Kapsi", "taustakartta", [small]),
    ("Kapsi", "peruskartta", [small]),
    ("Kapsi", "ortokuva", [small]),
    ("OpenStreetMap", "osm_bus_stops", [small, city]),
    ("Traficom Oskari", "112", [coast]),
    ("Traficom Oskari", "3", [small]),
    ("Traficom Oskari", "44", [small]),
    ("Traficom Oskari", "avoin:tuotejako_kaikki", [small, city]),
]
catalogs = {}
failures = []

with tempfile.TemporaryDirectory(prefix="suomenvaylat_spatial_", ignore_cleanup_errors=True) as folder:
    for index, (source, identifier, masks) in enumerate(cases):
        label = f"{source} / {identifier}"
        try:
            if source not in catalogs:
                catalogs[source], errors = catalog(source)
                if errors:
                    print(f"catalog warnings {source}: {errors}", flush=True)
            entry = next(item for item in catalogs[source] if item["id"] == identifier)
            raster = entry["kind"] in {"kapsi_wms", "oskari_wms", "oskari_wmts"}
            path = Path(folder) / f"source_{index}{'.tif' if raster else '.gpkg'}"
            for mask in masks:
                try:
                    count = download(entry, mask, crs, path)
                    break
                except RuntimeError as exc:
                    if "Rajauksesta ei löytynyt" not in str(exc) or mask is masks[-1]:
                        raise
            layer = QgsRasterLayer(str(path), label) if raster else QgsVectorLayer(str(path), label, "ogr")
            assert layer.isValid(), "saved layer is invalid"
            assert layer.crs().authid() == "EPSG:3067", layer.crs().authid()
            center = layer.extent().center()
            geographic = QgsCoordinateTransform(layer.crs(), wgs84, project).transform(center)
            assert 18 <= geographic.x() <= 33 and 59 <= geographic.y() <= 72, (
                geographic.x(), geographic.y())
            print(f"PASS {label}: {count}; lon={geographic.x():.4f} lat={geographic.y():.4f}", flush=True)
        except Exception as exc:
            failures.append((label, str(exc)))
            print(f"FAIL {label}: {exc}", flush=True)
        finally:
            project.removeAllMapLayers()
            layer = None
            gc.collect()

if failures:
    raise RuntimeError(f"{len(failures)} spatial source checks failed: {failures}")
print(f"All {len(cases)} public spatial checks passed", flush=True)
