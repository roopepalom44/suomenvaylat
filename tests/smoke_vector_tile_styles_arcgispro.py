"""Offline MML vector tile style path, with actual MVT rendering in ArcGIS."""
import importlib.machinery
import importlib.util
from pathlib import Path
import tempfile
import arcpy
from PIL import Image
from vector_tile_fixture import start_server

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("vector_style_toolbox", str(ROOT / "Toolboxes/VaylaWFSDownloader.pyt"))
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)
server, base = start_server()
out = Path(tempfile.mkdtemp(prefix="suomenvaylat_vector_styles_arcgis_"))
project = arcpy.mp.ArcGISProject(r"C:\Program Files\ArcGIS\Pro\Resources\ArcToolBox\Services\routingservices\data\Blank.aprx")
view = project.createMap("Vector tile style")
view.spatialReference = arcpy.SpatialReference(3857)
for layer in view.listLayers():
    view.removeLayer(layer)
tool = module.VaylaWFSDownloader()
tool._runtime_map_loaded = True
tool._runtime_map = view
tool._msg = lambda text: None
tool._warn = lambda text: None
module.service_styles.mml_tile_style = lambda name, matrix: base + "/style.json"
try:
    tool._add_mml_vector_tile_layer(base + "/tilejson.json", "Taustakartta", "test-key")
    layout = project.createLayout(100, 100, "MILLIMETER")
    frame = layout.createMapFrame(arcpy.Polygon(arcpy.Array([arcpy.Point(0, 0), arcpy.Point(100, 0), arcpy.Point(100, 100), arcpy.Point(0, 100)])), view)
    frame.camera.setExtent(arcpy.Extent(-20000000, -20000000, 20000000, 20000000))
    target = out / "styled_vector_tile.png"
    layout.exportToPNG(str(target), resolution=80)
    pixels = Image.open(target).convert("RGB")
    assert sum(pixel == (238, 0, 0) for pixel in pixels.getdata()) > 100, "Mapbox style did not render"
    print("ArcGIS vector tile provider style passed", out, flush=True)
finally:
    server.shutdown()
