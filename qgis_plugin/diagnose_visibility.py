"""Read public WFS data through the actual worker/open/style workflow."""
import os
from pathlib import Path
import sys
import tempfile
from qgis.core import (QgsApplication, QgsProject, QgsRenderContext,
                       QgsExpressionContextUtils, QgsMapSettings,
                       QgsMapRendererParallelJob, QgsRectangle)
from qgis.PyQt.QtCore import QSize

out = Path(tempfile.mkdtemp(prefix="suomenvaylat_visibility_"))
os.environ["QGIS_CUSTOM_CONFIG_PATH"] = str(out)
os.environ["QGIS_AUTH_DB_DIR_PATH"] = str(out)
QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX_PATH", "/usr"), True)
app = QgsApplication([], False)
app.initQgis()
sys.path.insert(0, str(Path(__file__).resolve().parent))
from suomenvaylat_qgis import services  # noqa: E402


def main():
    entries, errors = services.catalog("Väylä")
    print("catalog", len(entries), errors, flush=True)
    for identifier in ("tiestotiedot:nopeusrajoitukset", "tiestotiedot:aidat"):
        mask, crs = services.selection_geometry("Kunta/Kaupunki", ["Hailuoto" if identifier.endswith("nopeusrajoitukset") else "Helsinki"])
        entry = next(item for item in entries if item["id"] == identifier)
        destination = out / (identifier.split(":")[-1] + ".gpkg")
        count, items = services.download_to_files(entry, mask, crs, destination,
                                                 QgsProject.instance().transformContext())
        print(identifier, "download", count, items, flush=True)
        for item in items:
            layer = services.open_downloaded_layer(item, entry)
            print("fields", [(f.name(), f.typeName()) for f in layer.fields()], flush=True)
            print("style", layer.customProperty("suomenvaylat/style_status"), "extent", layer.extent().toString(), flush=True)
            settings = QgsMapSettings()
            settings.setDestinationCrs(crs)
            settings.setLayers([layer])
            extent = QgsRectangle(layer.extent())
            extent.scale(1.2)
            settings.setExtent(extent)
            settings.setOutputSize(QSize(1000, 1000))
            for scale in (10000, settings.scale()):
                context = QgsRenderContext.fromMapSettings(settings)
                context.setRendererScale(scale)
                context.expressionContext().appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
                renderer = layer.renderer()
                renderer.startRender(context, layer.fields())
                hits = []
                for feature in layer.getFeatures():
                    context.expressionContext().setFeature(feature)
                    hits.append(bool(renderer.symbolsForFeature(feature, context)))
                renderer.stopRender(context)
                print("renderer", renderer.type(), "scale", scale, "matched", sum(hits), "of", len(hits), flush=True)
            job = QgsMapRendererParallelJob(settings)
            job.start()
            job.waitForFinished()
            image = job.renderedImage()
            image.save(str(destination.with_suffix(".png")))
            nonwhite = sum(image.pixelColor(x, y).name() != "#ffffff" for x in range(image.width()) for y in range(image.height()))
            print("pixels", nonwhite, "render errors", [e.message for e in job.errors()], flush=True)
            assert nonwhite > 0 and not job.errors(), "The downloaded layer must render on the map"
    print("OUTPUT", out, flush=True)


if __name__ == "__main__":
    try:
        main()
    finally:
        QgsProject.instance().removeAllMapLayers()
        app.exitQgis()
