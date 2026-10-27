"""Offline regression: real worker/write/open/SLD flow must draw its features."""
from collections import Counter
import os
from pathlib import Path
import sys
import tempfile

from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem, QgsGeometry,
                       QgsMapRendererParallelJob, QgsMapSettings, QgsProject,
                       QgsRectangle, QgsRenderContext, QgsVectorLayer,
                       QgsExpressionContextUtils)
from qgis.PyQt.QtCore import QSize

out = Path(tempfile.mkdtemp(prefix="suomenvaylat_style_visibility_"))
os.environ["QGIS_CUSTOM_CONFIG_PATH"] = str(out)
os.environ["QGIS_AUTH_DB_DIR_PATH"] = str(out)
QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX_PATH", "/usr"), True)
app = QgsApplication([], False)
app.initQgis()
sys.path.insert(0, os.environ.get("SUOMENVAYLAT_QGIS_PLUGIN_ROOT") or str(Path(__file__).resolve().parent))
from suomenvaylat_qgis import services  # noqa: E402


def sld(name, rules):
    return (f'<StyledLayerDescriptor xmlns="http://www.opengis.net/sld" xmlns:ogc="http://www.opengis.net/ogc">'
            f'<NamedLayer><Name>{name}</Name><UserStyle><FeatureTypeStyle>{rules}'
            '</FeatureTypeStyle></UserStyle></NamedLayer></StyledLayerDescriptor>').encode()


def line_rule(field="kind", match="A", color="#ff0000", prefix=""):
    test = (f'<ogc:Filter><ogc:PropertyIsEqualTo><ogc:PropertyName>{field}</ogc:PropertyName>'
            f'<ogc:Literal>{match}</ogc:Literal></ogc:PropertyIsEqualTo></ogc:Filter>')
    return (f'<Rule>{prefix}{test}<LineSymbolizer><Stroke><CssParameter name="stroke">{color}</CssParameter>'
            '<CssParameter name="stroke-width">3</CssParameter></Stroke></LineSymbolizer></Rule>')


def render(layer, name):
    settings = QgsMapSettings()
    settings.setLayers([layer])
    settings.setDestinationCrs(layer.crs())
    settings.setExtent(QgsRectangle(385000, 6670000, 386000, 6671000))
    settings.setOutputSize(QSize(400, 400))
    job = QgsMapRendererParallelJob(settings)
    job.start()
    job.waitForFinished()
    assert not job.errors(), [error.message for error in job.errors()]
    image = job.renderedImage()
    assert image.save(str(out / (name + ".png")))
    return Counter(image.pixel(x, y) & 0xffffff for x in range(image.width()) for y in range(image.height()))


def main():
    crs = QgsCoordinateReferenceSystem("EPSG:3067")
    mask = QgsGeometry.fromRect(QgsRectangle(385000, 6670000, 386000, 6671000))
    original_download, original_fetch = services.download, services._style_fetch
    try:
        for name, rules, point, failure in (
            ("unmatched", line_rule(), False, False),
            ("all_unmatched", line_rule(match="not_in_data"), False, False),
            ("missing_field", line_rule(field="missing_attribute"), False, True),
            ("native_else", line_rule() + '<Rule><ElseFilter/><LineSymbolizer><Stroke>'
             '<CssParameter name="stroke">#0000ff</CssParameter><CssParameter name="stroke-width">3</CssParameter>'
             '</Stroke></LineSymbolizer></Rule>', False, False),
            ("scale_limited", line_rule(prefix='<MaxScaleDenominator>1000</MaxScaleDenominator>'), False, False),
            ("broken_icon", '<Rule><PointSymbolizer><Graphic><ExternalGraphic>'
             '<OnlineResource xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="file:/server/missing.png"/>'
             '<Format>image/png</Format></ExternalGraphic><Size>20</Size></Graphic></PointSymbolizer></Rule>', True, True),
        ):
            features = []
            for index, value in enumerate(("A", "other", None)):
                y = 6670200 + index * 300
                geometry = {"type": "Point", "coordinates": [385500, y]} if point else {
                    "type": "LineString", "coordinates": [[385100, y], [385900, y]]}
                features.append({"type": "Feature", "geometry": geometry, "properties": {"kind": value}})
            data = sld(name, rules)
            services._style_fetch = lambda url, data=data: data

            def download(entry, selected, selected_crs, destination, key="", progress=None, add_layer=None, files_only=False):
                return services._write_pages(entry, [features], crs, selected, selected_crs, destination,
                                             progress, add_layer)

            services.download = download
            destination = out / (name + ".gpkg")
            entry = {"kind": "wfs", "source": "Väylä", "id": name, "title": name, "endpoint": "https://example.test/ows"}
            count, items = services.download_to_files(entry, mask, crs, destination, QgsProject.instance().transformContext())
            assert count == 3
            layer = services.open_downloaded_layer(items[0], entry)
            assert layer.featureCount() == 3
            status = layer.customProperty("suomenvaylat/style_status")
            assert (status != "Rajapinnan symboliikka") == failure, (name, status)
            if name == "broken_icon":
                assert items[0]["style_preparation"]["error"]
                assert destination.with_suffix(".sld").is_file(), "Keep the original provider SLD"
            colors = render(layer, name)
            if name == "all_unmatched":
                baseline = QgsVectorLayer(str(destination), "Unmodified QGIS SLD import", "ogr")
                _, ok = baseline.loadSldStyle(str(destination.with_suffix(".sld")))
                assert ok
                before = render(baseline, name + "_before")
                assert sum(count for color, count in before.items() if color != 0xffffff) == 0
            assert sum(count for color, count in colors.items() if color != 0xffffff) > 30, (name, "empty map")
            gray = sum(count for color, count in colors.items()
                       if 0 < (color & 255) < 200 and (color & 255) == ((color >> 8) & 255) == ((color >> 16) & 255))
            if name == "native_else":
                assert gray == 0, (name, gray)
            else:
                assert gray > 30, (name, gray)
            if name in ("unmatched", "native_else"):
                assert colors[0xff0000] > 100, "Known classes must retain the provider color"
            if name == "native_else":
                assert colors[0x0000ff] > 100, "Provider ELSE must retain its color without fallback overlay"
            if name == "scale_limited":
                settings = QgsMapSettings()
                context = QgsRenderContext.fromMapSettings(settings)
                context.setRendererScale(500)
                context.expressionContext().appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
                renderer = layer.renderer()
                renderer.startRender(context, layer.fields())
                feature = next(layer.getFeatures())
                context.expressionContext().setFeature(feature)
                assert any(symbol.color().name() == "#ff0000" for symbol in renderer.symbolsForFeature(feature, context))
                renderer.stopRender(context)
            reopened = QgsVectorLayer(str(destination), name + " reopened", "ogr")
            assert reopened.isValid() and reopened.featureCount() == 3
            assert render(reopened, name + "_reopened") == colors, (name, "GeoPackage lost the visible style")
            qml = QgsVectorLayer(str(destination), name + " QML", "ogr")
            _, ok = qml.loadNamedStyle(str(destination.with_suffix(".qml")))
            assert ok and render(qml, name + "_qml") == colors, (name, "QML lost the visible style")
            print(name, "visible symbols and GeoPackage/QML persistence passed", flush=True)
    finally:
        services.download, services._style_fetch = original_download, original_fetch
    print("QGIS style visibility passed:", out, flush=True)


if __name__ == "__main__":
    import gc
    try:
        main()
    finally:
        QgsProject.instance().removeAllMapLayers()
        gc.collect()
        app.exitQgis()
