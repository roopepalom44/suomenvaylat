"""Offline QGIS regression for the 0.2.7 fixes (no network needed).

Run with QGIS's Python, e.g. on Linux:
    QT_QPA_PLATFORM=offscreen python3 qgis_plugin/smoke_audit_fixes.py
Set QGIS_PREFIX_PATH if QGIS is not under /usr (Windows: ...\\apps\\qgis-ltr).
"""

import os
import sys
import tempfile
import time
from pathlib import Path

from qgis.core import (
    QgsApplication, QgsCoordinateReferenceSystem, QgsFeature, QgsGeometry, QgsProject,
    QgsVectorLayer,
)

QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX_PATH", "/usr"), True)
app = QgsApplication([], False)
app.initQgis()
sys.path.insert(0, str(Path(__file__).resolve().parent))

import suomenvaylat_qgis.plugin as plugin_module  # noqa: E402
import suomenvaylat_qgis.services as services  # noqa: E402

if not hasattr(services.QgsJsonUtils, "geometryFromGeoJson"):
    # QGIS < 3.36 (esim. Linux-jakelun 3.34) testiajoon; lisäosa vaatii 3.44:n.
    from types import SimpleNamespace
    from osgeo import ogr
    services.QgsJsonUtils = SimpleNamespace(geometryFromGeoJson=lambda text: QgsGeometry.fromWkt(
        ogr.CreateGeometryFromJson(text).ExportToIsoWkt()))

project = QgsProject.instance()
tm35 =QgsCoordinateReferenceSystem("EPSG:3067")


def memory_layer(kind, wkts, name="layer"):
    layer = QgsVectorLayer(f"{kind}?crs=EPSG:3067", name, "memory")
    features = []
    for wkt in wkts:
        feature = QgsFeature()
        feature.setGeometry(QgsGeometry.fromWkt(wkt))
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    layer.updateExtents()
    return layer


# 1. Oma viivarajaus = konveksi peite, valinta rajaa kohteet.
lines = memory_layer("LineString", [
    "LINESTRING(380000 6670000, 390000 6670000, 390000 6680000)",
    "LINESTRING(500000 7000000, 501000 7000000)",
])
mask, crs = services.selection_geometry("Oma aineisto", (), lines)
assert services._geometry_type_value(mask.type()) == 2 and mask.area() > 1e9, mask.area()
first_id = next(lines.getFeatures()).id()
lines.selectByIds([first_id])
selected_mask, _ = services.selection_geometry("Oma aineisto", (), lines)
assert abs(selected_mask.area() - 5e7) < 1, selected_mask.area()
straight = memory_layer("LineString", ["LINESTRING(0 0, 10 0)"])
try:
    services.selection_geometry("Oma aineisto", (), straight)
    raise AssertionError("straight line should fail")
except ValueError:
    pass
print("custom line boundary passed", flush=True)

square_mask = QgsGeometry.fromWkt("POLYGON((385000 6670000, 386000 6670000, 386000 6671000, 385000 6671000, 385000 6670000))")
wgs = QgsCoordinateReferenceSystem("EPSG:4326")

with tempfile.TemporaryDirectory() as folder:
    # 2. OGC-kentät kaikilta sivuilta.
    center = QgsGeometry.fromWkt("POINT(385500 6670500)")
    from qgis.core import QgsCoordinateTransform
    center.transform(QgsCoordinateTransform(tm35, wgs, project))
    lon, lat = center.asPoint().x(), center.asPoint().y()
    pages = [
        {"features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
                       "properties": {"a": 1, "nested": {"k": 1}}}],
         "links": [{"rel": "next", "href": "?page=2"}]},
        {"features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
                       "properties": {"a": 1.5, "late": "x"}}], "links": []},
    ]
    original_request = services._request_json
    services._request_json = lambda url, key="": pages.pop(0)
    added = []
    try:
        count = services.download({"kind": "ogc", "id": "demo", "title": "demo", "endpoint": "https://example.test/"},
                                  square_mask, tm35, Path(folder) / "ogc.gpkg", add_layer=added.append)
    finally:
        services._request_json = original_request
    assert count == 2 and len(added) == 1
    fields = {field.name(): field.typeName().lower() for field in added[0].fields()}
    assert "late" in fields and "nested" in fields, fields
    values = sorted(feature["a"] for feature in added[0].getFeatures())
    assert values == [1.0, 1.5], values
    print("OGC fields from all pages passed", fields, flush=True)

    # 3. OSM: relaatio + sekageometria -> erilliset tasot.
    def ll(x, y):
        point = QgsGeometry.fromWkt(f"POINT({x} {y})")
        point.transform(QgsCoordinateTransform(tm35, wgs, project))
        return {"lon": point.asPoint().x(), "lat": point.asPoint().y()}

    ring = [ll(385100, 6670100), ll(385400, 6670100), ll(385400, 6670400), ll(385100, 6670400), ll(385100, 6670100)]
    elements = [
        {"type": "node", "id": 1, "tags": {"addr:housenumber": "1"}, **ll(385500, 6670500)},
        {"type": "way", "id": 2, "tags": {"building": "yes"}, "geometry": ring},
        {"type": "way", "id": 3, "tags": {"highway": "residential"}, "geometry": ring},
        {"type": "relation", "id": 4, "tags": {"type": "multipolygon", "landuse": "forest"},
         "members": [{"type": "way", "role": "outer", "geometry": ring[:3]},
                     {"type": "way", "role": "outer", "geometry": ring[2:]}]},
    ]
    original_fetch = services._overpass_fetch
    services._overpass_fetch = lambda query: list(elements)
    added = []
    try:
        count = services.download({"kind": "osm", "id": "osm_addresses", "title": "Osoitteet",
                                   "query": "(node({bbox}););out geom;"},
                                  square_mask, tm35, Path(folder) / "osm.gpkg", add_layer=added.append)
    finally:
        services._overpass_fetch = original_fetch
    names = sorted(layer.name() for layer in added)
    assert count == 4 and names == ["Osoitteet (alueet)", "Osoitteet (pisteet)", "Osoitteet (viivat)"], names
    by_name = {layer.name(): layer for layer in added}
    assert by_name["Osoitteet (alueet)"].featureCount() == 2
    assert {f["osm_type"] for f in by_name["Osoitteet (alueet)"].getFeatures()} == {"way", "relation"}
    assert by_name["Osoitteet (viivat)"].featureCount() == 1
    print("OSM relations and geometry split passed", flush=True)

    # 4. WFS-leikkaus: muut kuin Väylä/DigiRoad leikataan rajaukseen.
    long_line = memory_layer("LineString", ["LINESTRING(384000 6670500, 387000 6670500)"], "wfs")
    lengths = {}
    for source in ("Väylä", "Liiteri"):
        added = []
        services._download_vector_layer(
            {"id": f"test:{source}", "title": source, "source": source}, long_line, square_mask, tm35,
            Path(folder) / f"wfs_{len(lengths)}.gpkg", add_layer=added.append,
            clip=source not in services.UNCLIPPED_WFS_SOURCES)
        lengths[source] = next(added[0].getFeatures()).geometry().length()
    assert abs(lengths["Väylä"] - 3000) < 1e-6 and abs(lengths["Liiteri"] - 1000) < 1e-6, lengths
    print("WFS clip policy passed", lengths, flush=True)

# 5. Uudelleenohjaus ei vuoda tunnisteita.
handler = services._SafeRedirectHandler()
request = services.urllib.request.Request("https://a.example/x", headers={"Authorization": "Basic k"})
assert handler.redirect_request(request, None, 302, "Found", {}, "https://b.example/y").get_header("Authorization") is None
print("safe redirect passed", flush=True)

# 6. Tunnistautumisasetus päivitetään eikä monisteta.
assert QgsApplication.authManager().setMasterPassword("smoke-pass", True)
first = plugin_module.store_basic_auth("Suomenväylät — smoke", "key1", "")
second = plugin_module.store_basic_auth("Suomenväylät — smoke", "key2", "")
names = [c.name() for c in QgsApplication.authManager().availableAuthMethodConfigs().values()]
assert first == second and names.count("Suomenväylät — smoke") == 1, names
print("auth config reuse passed", flush=True)

# 7. Dialogin taustatehtävä (oikea QgsTaskManager) ja lisäys pääsäikeessä.
plugin_module.QMessageBox.information = lambda *args: None
plugin_module.QMessageBox.warning = lambda *args: None
plugin_module.QMessageBox.critical = lambda *args: (_ for _ in ()).throw(RuntimeError(str(args)))
plugin_module.catalog = lambda source, key="", password="": ([{
    "source": source, "kind": "wfs", "id": "demo:layer", "title": "Demo",
    "endpoint": "https://example.test/"}], [])
downloaded = []


def fake_download(entry, mask, crs, path, key, progress, add_layer=None):
    progress(1)
    layer = memory_layer("Point", ["POINT(385500 6670500)"], "Demo tulos")
    add_layer(layer)
    downloaded.append(str(path))
    return 1


plugin_module.download = fake_download
plugin_module.selection_geometry = lambda *args: (square_mask, tm35)
dialog = plugin_module.SuomenvaylatDialog()


def wait_for(condition, seconds=20):
    deadline = time.time() + seconds
    while not condition():
        app.processEvents()
        if time.time() > deadline:
            raise AssertionError("timeout")
        time.sleep(0.01)


dialog._load_catalog()
wait_for(lambda: dialog.refresh_button.isEnabled() and dialog.entries)
dialog.layers.item(0).setCheckState(plugin_module.Qt.Checked)
with tempfile.TemporaryDirectory() as folder:
    dialog.output.setText(folder)
    dialog._run_download()
    wait_for(lambda: dialog._download_task is None)
assert len(downloaded) == 1
assert any(layer.name() == "Demo tulos" for layer in project.mapLayers().values())
print("background task download passed", flush=True)
print("ALL QGIS AUDIT SMOKE CHECKS PASSED", flush=True)
