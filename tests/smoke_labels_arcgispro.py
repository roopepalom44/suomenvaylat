"""Offline ArcGIS render/persistence regression for the real Väylä speed SLD."""
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET

import arcpy
from PIL import Image, ImageChops

arcpy.env.overwriteOutput = True
ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("labels_toolbox", os.environ.get("SUOMENVAYLAT_LABEL_TOOLBOX") or str(ROOT / "Toolboxes/VaylaWFSDownloader.pyt"))
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)
out = Path(os.environ.get("SUOMENVAYLAT_LABEL_TEST_OUTPUT") or tempfile.mkdtemp(prefix="suomenvaylat_labels_"))
out.mkdir(parents=True, exist_ok=True)
project = arcpy.mp.ArcGISProject(r"C:\Program Files\ArcGIS\Pro\Resources\ArcToolBox\Services\routingservices\data\Blank.aprx")
map_view = project.createMap("Nopeusrajoitusten labelointi")
for background in map_view.listLayers():
    map_view.removeLayer(background)
gdb = str(out / "labels.gdb")
arcpy.management.CreateFileGDB(str(out), "labels.gdb")
path = str(Path(gdb) / "nopeusrajoitukset")
arcpy.management.CreateFeatureclass(gdb, "nopeusrajoitukset", "POLYLINE", spatial_reference=3067)
# Alias resolution must work for both label fields and filter fields.
for name, kind, alias in (("speed", "LONG", "nopeusrajoitus"), ("alkusijainti_tie", "LONG", "alkusijainti_tie"),
                          ("tyyppi", "TEXT", "tyyppi")):
    arcpy.management.AddField(path, name, kind, field_alias=alias)
with arcpy.da.InsertCursor(path, ["speed", "alkusijainti_tie", "tyyppi", "SHAPE@"]) as cursor:
    for i, speed in enumerate((20, 30, 40, 50, 60, 70, 80, 90, 100, 120)):
        x, y = 380000, 6670000 + i * 1000
        shape = arcpy.Polyline(arcpy.Array([arcpy.Point(x, y), arcpy.Point(x + 7000, y + 400)]), arcpy.SpatialReference(3067))
        cursor.insertRow([speed, 1, "Nopeusrajoitus", shape])
tool = module.VaylaWFSDownloader()
messages, warnings = [], []
tool._msg, tool._warn = messages.append, warnings.append
data = (ROOT / "tests/data/styles/vayla_nopeusrajoitukset.sld").read_bytes()
tool._style_fetch = lambda url: data
tool._prepare_output_style(path, {"kind": "wfs", "id": "tiestotiedot:nopeusrajoitukset",
                                  "title": "Nopeusrajoitukset", "source": "Väylä"})
assert not warnings, warnings
saved = tool._output_layer_files[path]
layer = map_view.addLayer(arcpy.mp.LayerFile(saved))[0]
assert layer.showLabels
labels = layer.getDefinition("V3").labelClasses
assert len(labels) == 10, len(labels)
assert all(lc.expressionEngine == "Arcade" for lc in labels)
assert all('$feature["speed"]' in lc.expression for lc in labels)
assert all(lc.minimumScale == 362834 and lc.maximumScale == 0 for lc in labels)
assert all(lc.textSymbol.symbol.callout.pointSymbol.symbolLayers for lc in labels)
layout = project.createLayout(220, 190, "MILLIMETER")
frame = layout.createMapFrame(arcpy.Polygon(arcpy.Array([arcpy.Point(5, 5), arcpy.Point(215, 5),
                                                       arcpy.Point(215, 185), arcpy.Point(5, 185)])), map_view)
frame.camera.setExtent(arcpy.Extent(379000, 6669000, 388000, 6680000))

def render(name):
    image_path = out / (name + ".png")
    layout.exportToPNG(str(image_path), resolution=120)
    return Image.open(image_path).convert("RGB")

for engine in ("CIMStandardGeneralPlacementProperties", "CIMMaplexGeneralPlacementProperties"):
    definition = map_view.getDefinition("V3")
    definition.generalPlacementProperties = arcpy.cim.CreateCIMObjectFromClassName(engine, "V3")
    map_view.setDefinition(definition)
    layer.showLabels = False
    without = render(engine + "_without")
    layer.showLabels = True
    with_labels = render(engine + "_labels")
    changed = sum(pixel != (0, 0, 0) for pixel in ImageChops.difference(without, with_labels).getdata())
    assert changed > 200, (engine, "Labels did not render", changed)
    print(engine, "label pixels:", changed, flush=True)
    frame.camera.scale = 500000
    layer.showLabels = True
    far_labels = render(engine + "_far_labels")
    layer.showLabels = False
    far_without = render(engine + "_far_without")
    assert ImageChops.difference(far_labels, far_without).getbbox() is None, "Scale limit ignored"
    frame.camera.setExtent(arcpy.Extent(379000, 6669000, 388000, 6680000))
layer.showLabels = True
project.saveACopy(str(out / "labels.aprx"))
reopened = arcpy.mp.ArcGISProject(str(out / "labels.aprx")).listMaps("Nopeusrajoitusten labelointi")[0].listLayers()[0]
assert reopened.showLabels and len(reopened.listLabelClasses()) == 10
# Label failures must preserve the successful line style and persisted LYRX.
missing_root = module.service_styles.parse_xml(data)
for element in missing_root.iter():
    if module.service_styles.local(element) == "Label":
        module.service_styles.child(element, "PropertyName").text = "missing_label_field"
missing = ET.tostring(missing_root)
tool._style_client = module.service_styles.StyleClient(lambda url: missing)
fallback_data = path + "_missing"
arcpy.management.CopyFeatures(path, fallback_data)
fallback_layer = arcpy.management.MakeFeatureLayer(fallback_data, "missing_labels").getOutput(0)
fallback_path = tool._apply_provider_style(fallback_layer, fallback_data, {"kind": "wfs", "id": "tiestotiedot:nopeusrajoitukset",
                                                                "endpoint": "https://example.test/ows"})
assert fallback_path and any("Label-kenttää ei tunnistettu" in warning for warning in warnings), warnings
assert fallback_layer.getDefinition("V3").renderer.groups[0].classes
assert not fallback_layer.showLabels
warnings.clear()
only_text = b'<StyledLayerDescriptor><NamedLayer><Name>label_only</Name><UserStyle><FeatureTypeStyle><Rule><TextSymbolizer><Label>Only text</Label></TextSymbolizer></Rule></FeatureTypeStyle></UserStyle></NamedLayer></StyledLayerDescriptor>'
tool._style_client = module.service_styles.StyleClient(lambda url: only_text)
assert tool._apply_provider_style(fallback_layer, fallback_data, {"kind": "wfs", "id": "label_only", "endpoint": "https://example.test/ows"}), warnings
assert not warnings, warnings
assert fallback_layer.showLabels and len(fallback_layer.listLabelClasses()) == 1
assert not fallback_layer.getDefinition("V3").renderer.useDefaultSymbol
arcpy.management.Delete(fallback_layer)
print("ArcGIS label rendering, scale limits, aliases and LYRX/APRX persistence passed:", out, flush=True)
print("\n".join(messages), flush=True)
