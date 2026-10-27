"""Offline import, render and persistence regression for Väylä speed labels."""
import os
from pathlib import Path
import sys
import tempfile

from qgis.core import (Qgis, QgsApplication, QgsCoordinateTransformContext,
                       QgsFeature, QgsGeometry, QgsMapRendererParallelJob,
                       QgsMapSettings, QgsRectangle, QgsVectorFileWriter, QgsVectorLayer)
from qgis.PyQt.QtCore import QSize

out = Path(tempfile.mkdtemp(prefix="suomenvaylat_labels_qgis_"))
os.environ["QGIS_CUSTOM_CONFIG_PATH"] = str(out)
os.environ["QGIS_AUTH_DB_DIR_PATH"] = str(out)
QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX_PATH", "/usr"), True)
app = QgsApplication([], False)
app.initQgis()
sys.path.insert(0, os.environ.get("SUOMENVAYLAT_QGIS_PLUGIN_ROOT") or str(Path(__file__).resolve().parent))
from suomenvaylat_qgis import services  # noqa: E402

def main():
    root = Path(__file__).resolve().parents[1]
    source = QgsVectorLayer("LineString?crs=EPSG:3067&field=nopeusrajoitus:integer&field=alkusijainti_tie:integer&field=tyyppi:string",
                            "Nopeusrajoitukset", "memory")
    for i, speed in enumerate((20, 30, 40, 50, 60, 70, 80, 90, 100, 120)):
        y = 6670000 + i * 1000
        feature = QgsFeature(source.fields())
        feature.setAttributes([speed, 1, "Nopeusrajoitus"])
        feature.setGeometry(QgsGeometry.fromWkt(f"LINESTRING(380000 {y},387000 {y + 400})"))
        assert source.dataProvider().addFeatures([feature])[0]
    destination = out / "nopeusrajoitukset.gpkg"
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = "nopeusrajoitukset"
    result = QgsVectorFileWriter.writeAsVectorFormatV3(source, str(destination), QgsCoordinateTransformContext(), options)
    assert result[0] == QgsVectorFileWriter.NoError, result
    layer = QgsVectorLayer(str(destination), "Nopeusrajoitukset", "ogr")
    data = (root / "tests/data/styles/vayla_nopeusrajoitukset.sld").read_bytes()
    services._style_fetch = lambda url: data
    services._apply_provider_style(layer, {"kind": "wfs", "id": "tiestotiedot:nopeusrajoitukset",
                                          "endpoint": "https://example.test/ows"}, destination)
    assert layer.customProperty("suomenvaylat/style_status") == "Rajapinnan symboliikka", layer.customProperty("suomenvaylat/style_status")
    assert layer.labelsEnabled(), "Provider speed labels were not imported"
    print("QGIS", Qgis.QGIS_VERSION, "labeling", layer.labeling().type(), flush=True)
    assert len(layer.labeling().subProviders()) == 10
    for rule in layer.labeling().rootRule().children():
        assert rule.minimumScale() == 362834 and rule.maximumScale() == 0
        text_format = rule.settings().format()
        background = text_format.background()
        assert background.enabled() and background.type() == background.ShapeCircle
    reopened = QgsVectorLayer(str(destination), "Reopened", "ogr")
    assert reopened.isValid() and reopened.labelsEnabled(), "GeoPackage lost provider labels"
    assert reopened.labeling().type() == layer.labeling().type()
    qml = QgsVectorLayer("LineString?crs=EPSG:3067", "QML", "memory")
    _, ok = qml.loadNamedStyle(str(destination.with_suffix(".qml")))
    assert ok and qml.labelsEnabled(), "QML lost provider labels"
    settings = QgsMapSettings()
    settings.setLayers([reopened])
    settings.setDestinationCrs(layer.crs())
    settings.setExtent(QgsRectangle(379000, 6669000, 388000, 6680000))
    settings.setOutputSize(QSize(1000, 1000))

    def render(name):
        job = QgsMapRendererParallelJob(settings)
        job.start()
        job.waitForFinished()
        image = job.renderedImage()
        assert image.save(str(out / (name + ".png")))
        results = job.takeLabelingResults()
        return image, {label.labelText for label in results.allLabels()}

    reopened.setLabelsEnabled(False)
    without, labels = render("without_labels")
    assert not labels
    reopened.setLabelsEnabled(True)
    with_labels, labels = render("labels")
    assert labels == {str(speed) for speed in (20, 30, 40, 50, 60, 70, 80, 90, 100, 120)}
    changed = sum(without.pixel(x, y) != with_labels.pixel(x, y) for x in range(without.width()) for y in range(without.height()))
    assert changed > 200, ("Labels did not render", changed)
    far = QgsRectangle(settings.extent())
    far.scale(500000 / settings.scale())
    settings.setExtent(far)
    far_labels, labels = render("far_labels")
    assert not labels, "Labels exceeded the provider scale limit"
    reopened.setLabelsEnabled(False)
    far_without, _ = render("far_without")
    assert far_labels == far_without, "Scale limit ignored"
    print("Label pixels:", changed, flush=True)
    print("QGIS provider labels, scale limits, GeoPackage/QML persistence and rendering passed:", out, flush=True)


if __name__ == "__main__":
    import gc
    try:
        main()
    finally:
        # Format/background wrappers refer to their native parent objects.
        # Destroy all test-owned objects before releasing QGIS providers.
        gc.collect()
        app.exitQgis()
