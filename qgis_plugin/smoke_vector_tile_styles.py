"""Offline MML vector tile path and actual Mapbox style rendering in QGIS."""
import os
from pathlib import Path
import sys
import tempfile
from qgis.core import (QgsApplication, QgsProject, QgsCoordinateReferenceSystem,
                       QgsMapSettings, QgsMapRendererParallelJob, QgsRectangle)
from qgis.PyQt.QtCore import QSize
from PIL import Image

profile = tempfile.mkdtemp(prefix="suomenvaylat_vector_styles_qgis_")
os.environ["QGIS_CUSTOM_CONFIG_PATH"] = profile
os.environ["QGIS_AUTH_DB_DIR_PATH"] = profile
QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX_PATH", "/usr"), True)
app = QgsApplication([], False)
app.initQgis()
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from vector_tile_fixture import start_server  # noqa: E402
from suomenvaylat_qgis import plugin as module  # noqa: E402

server, base = start_server()
module.MML_TILEJSON["Taustakartta"] = base + "/tilejson.json"
module.mml_tile_style = lambda name: base + "/style.json"
module.store_basic_auth = lambda *args: ""
module.QMessageBox.information = lambda *args: None
errors = []
module.QMessageBox.critical = lambda *args: errors.append(args[-1])
dialog = module.SuomenvaylatDialog()
dialog.background_key.setText("test-key")
try:
    dialog._add_mml_background("Taustakartta")
    assert not errors, errors
    project = QgsProject.instance()
    layers = list(project.mapLayers().values())
    assert len(layers) == 1 and layers[0].isValid()
    settings = QgsMapSettings()
    settings.setLayers(layers)
    settings.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
    settings.setExtent(QgsRectangle(-20000000, -20000000, 20000000, 20000000))
    settings.setOutputSize(QSize(400, 400))
    job = QgsMapRendererParallelJob(settings)
    job.start()
    job.waitForFinished()
    target = Path(profile) / "styled_vector_tile.png"
    assert job.renderedImage().save(str(target))
    assert sum(pixel == (238, 0, 0) for pixel in Image.open(target).convert("RGB").getdata()) > 100
    print("QGIS vector tile provider style passed", profile, flush=True)
finally:
    server.shutdown()
