"""Offline style import, persistence and rendering with QGIS's Python."""
import os
from pathlib import Path
import sys
import tempfile
from qgis.core import (QgsApplication, QgsFeature, QgsGeometry, QgsProject,
                       QgsVectorLayer, QgsVectorFileWriter, QgsCoordinateTransformContext,
                       QgsMapSettings, QgsMapRendererParallelJob, QgsRectangle)
from qgis.PyQt.QtCore import QSize

profile = tempfile.mkdtemp(prefix="suomenvaylat_styles_qgis_")
os.environ["QGIS_CUSTOM_CONFIG_PATH"] = profile
os.environ["QGIS_AUTH_DB_DIR_PATH"] = profile
QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX_PATH", "/usr"), True)
app = QgsApplication([], False)
app.initQgis()
sys.path.insert(0, str(Path(__file__).resolve().parent))
from suomenvaylat_qgis import services  # noqa: E402
from suomenvaylat_qgis import service_styles as ss  # noqa: E402

root = Path(__file__).resolve().parents[1]
out = Path(profile)
project = QgsProject.instance()
cases = [
    ("vayla_0", "LineString", "tiestotiedot:aidat", "&field=rakenteelliset_ominaisuudet_tyyppi:string", [["Riista-aidat"], ["Lumiaidat"], ["Muu"]]),
    ("digiroad_0", "LineString", "dr_ajoneuvokoht_rajoitus", "&field=kiell_ajon:integer", [[2], [3], [7]]),
    ("liiteri_etaisyysvyohykkeet_0", "Polygon", "etaisyysvyoh_ala_asteet", "&field=distance_m:integer", [[250], [500], [1000]]),
    ("syke_0", "Point", "PS.ProtectedSitesAsetusValtionOmistamienRakennustenSuojelusta", "", [[], [], []]),
    ("tilastokeskus_0", "Polygon", "tilastointialueet:avi1000k", "", [[], [], []]),
    ("traficom_rajoitettu_1", "Polygon", "DepthArea_A", "&field=DRVAL2:double", [[0], [0.5], [2]])
]
for index, (fixture, geometry, layer_id, fields, rows) in enumerate(cases):
    source = QgsVectorLayer(geometry + "?crs=EPSG:3067" + fields, fixture, "memory")
    for i, row in enumerate(rows):
        x, y = 380000 + i * 1500, 6670000 + index * 1500
        wkt = f"POINT({x} {y})" if geometry == "Point" else (f"LINESTRING({x} {y},{x+1000} {y+400})" if geometry == "LineString" else f"POLYGON(({x} {y},{x+900} {y},{x+900} {y+900},{x} {y+900},{x} {y}))")
        feature = QgsFeature(source.fields())
        feature.setAttributes(row)
        feature.setGeometry(QgsGeometry.fromWkt(wkt))
        source.dataProvider().addFeatures([feature])
    destination = out / (fixture + ".gpkg")
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = fixture
    result = QgsVectorFileWriter.writeAsVectorFormatV3(source, str(destination), QgsCoordinateTransformContext(), options)
    assert result[0] == QgsVectorFileWriter.NoError, result
    layer = QgsVectorLayer(str(destination), fixture, "ogr")
    data = (root / "tests/data/styles" / (fixture + ".sld")).read_bytes()
    services._style_fetch = lambda url, data=data: data
    services._apply_provider_style(layer, {"kind": "wfs", "id": layer_id, "endpoint": "https://example.test/ows"}, destination)
    assert layer.customProperty("suomenvaylat/style_status") == "Rajapinnan symboliikka", layer.customProperty("suomenvaylat/style_status")
    assert layer.renderer().type() in ("RuleRenderer", "singleSymbol"), layer.renderer().type()
    assert destination.with_suffix(".sld").is_file()
    assert destination.with_suffix(".qml").is_file()
    reopened = QgsVectorLayer(str(destination), fixture, "ogr")
    assert reopened.renderer().type() == layer.renderer().type(), reopened.renderer().type()
    project.addMapLayer(layer)
    print(fixture, "import and GeoPackage style persistence passed", flush=True)
settings = QgsMapSettings()
settings.setLayers(list(project.mapLayers().values()))
settings.setDestinationCrs(next(iter(project.mapLayers().values())).crs())
settings.setExtent(QgsRectangle(379500, 6669500, 384500, 6679000))
settings.setOutputSize(QSize(900, 900))
job = QgsMapRendererParallelJob(settings)
job.start()
job.waitForFinished()
assert job.renderedImage().save(str(out / "styles.png"))
print("QGIS provider styles passed", out, flush=True)
project.removeAllMapLayers()
# A server-local icon must not prevent saving the provider SLD or its dataset.
layer = QgsVectorLayer("Point?crs=EPSG:3067", "unavailable icon", "memory")
data = b'<StyledLayerDescriptor xmlns="http://www.opengis.net/sld" xmlns:xlink="http://www.w3.org/1999/xlink"><NamedLayer><Name>icons</Name><UserStyle><FeatureTypeStyle><Rule><PointSymbolizer><Graphic><ExternalGraphic><OnlineResource xlink:href="file:/server/icon.png"/><Format>image/png</Format></ExternalGraphic></Graphic></PointSymbolizer></Rule></FeatureTypeStyle></UserStyle></NamedLayer></StyledLayerDescriptor>'
services._style_fetch = lambda url: data
destination = out / "unavailable_icon.gpkg"
services._apply_provider_style(layer, {"kind": "wfs", "id": "icons", "endpoint": "https://example.test/ows"}, destination)
assert destination.with_suffix(".sld").is_file()
assert layer.customProperty("suomenvaylat/style_status") != "Rajapinnan symboliikka"
assert layer.renderer().type() == "singleSymbol"
print("Unavailable provider icon preserves SLD and default renderer", flush=True)
# exitQgis can destroy still referenced Python wrappers during interpreter
# shutdown on Windows; the isolated process owns all these objects.
