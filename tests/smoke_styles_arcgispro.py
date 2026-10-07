"""Run with ArcGIS Pro Python. Uses real provider SLD fixtures, offline."""
import importlib.machinery
import importlib.util
from pathlib import Path
import tempfile
import arcpy

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("style_toolbox", str(ROOT / "Toolboxes/VaylaWFSDownloader.pyt"))
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)
out = Path(tempfile.mkdtemp(prefix="suomenvaylat_styles_arcgis_"))
project = arcpy.mp.ArcGISProject(r"C:\Program Files\ArcGIS\Pro\Resources\ArcToolBox\Services\routingservices\data\Blank.aprx")
map_view = project.createMap("Symboliikka")
for background in map_view.listLayers():
    map_view.removeLayer(background)
gdb = str(out / "styles.gdb")
arcpy.management.CreateFileGDB(str(out), "styles.gdb")
cases = [
    ("vayla_0", "Polyline", "tiestotiedot:aidat", [("rakenteelliset_ominaisuudet_tyyppi", "TEXT")], [("Riista-aidat",), ("Lumiaidat",), ("Muu",)]),
    ("digiroad_0", "Polyline", "dr_ajoneuvokoht_rajoitus", [("kiell_ajon", "LONG")], [(2,), (3,), (7,)]),
    ("liiteri_etaisyysvyohykkeet_0", "Polygon", "etaisyysvyoh_ala_asteet", [("buffer", "LONG")], [(250,), (500,), (1000,)]),
    ("syke_0", "Point", "PS.ProtectedSitesAsetusValtionOmistamienRakennustenSuojelusta", [], [(), (), ()]),
    ("tilastokeskus_0", "Polygon", "tilastointialueet:avi1000k", [], [(), (), ()]),
    ("traficom_rajoitettu_1", "Polygon", "DepthArea_A", [("DRVAL2", "DOUBLE")], [(0,), (0.5,), (2.0,)])
]
messages, warnings = [], []
for index, (fixture, geometry, layer_id, fields, rows) in enumerate(cases):
    path = str(Path(gdb) / fixture)
    arcpy.management.CreateFeatureclass(gdb, fixture, geometry.upper(), spatial_reference=arcpy.SpatialReference(3067))
    for field, kind in fields:
        arcpy.management.AddField(path, field, kind)
    with arcpy.da.InsertCursor(path, [name for name, kind in fields] + ["SHAPE@"]) as cursor:
        for i, row in enumerate(rows):
            x, y = 380000 + i * 1500, 6670000 + index * 1500
            if geometry == "Point":
                shape = arcpy.PointGeometry(arcpy.Point(x, y), arcpy.SpatialReference(3067))
            else:
                coords = [[x,y],[x+1000,y+400]] if geometry == "Polyline" else [[x,y],[x+900,y],[x+900,y+900],[x,y+900],[x,y]]
                points = arcpy.Array([arcpy.Point(*point) for point in coords])
                shape = getattr(arcpy, geometry)(points, arcpy.SpatialReference(3067))
            cursor.insertRow(list(row) + [shape])
    tool = module.VaylaWFSDownloader()
    tool._msg = messages.append
    tool._warn = warnings.append
    data = (ROOT / "tests/data/styles" / (fixture + ".sld")).read_bytes()
    tool._style_fetch = lambda url, data=data: data
    # Liiteri's live field is distance; discover from fixture instead of making
    # a synthetic assertion about its name.
    if fixture.startswith("liiteri"):
        needed = set().union(*(module.service_styles.filter_fields(rule["filter"]) for rule in module.service_styles.arc_rules(module.service_styles.parse_xml(data), geometry)[0]))
        for field in needed - {name for name, kind in fields}:
            arcpy.management.AddField(path, field, "LONG")
            with arcpy.da.UpdateCursor(path, [field]) as cursor:
                for i, row in enumerate(cursor):
                    row[0] = (250, 500, 1000)[i]
                    cursor.updateRow(row)
    tool._runtime_map = map_view
    tool._runtime_map_loaded = True
    tool._prepare_output_style(path, {"kind": "wfs", "id": layer_id, "title": fixture, "endpoint": "https://example.test/ows"})
    assert path in tool._output_layer_files, warnings
    assert tool._add_to_map(path)[0]
    layer = next(item for item in map_view.listLayers() if item.name == fixture)
    assert not warnings, warnings
    renderer = layer.getDefinition("V3").renderer
    assert type(renderer).__name__ == "CIMUniqueValueRenderer", type(renderer).__name__
    assert renderer.groups[0].classes, fixture
    saved = out / "Suomenvaylat_tyylit" / ("styles.gdb_" + fixture + ".lyrx")
    assert saved.is_file(), saved
    reopened = arcpy.mp.LayerFile(str(saved)).listLayers()[0]
    assert type(reopened.getDefinition("V3").renderer).__name__ == "CIMUniqueValueRenderer"
    print(fixture, len(renderer.groups[0].classes), "classes", flush=True)
layout = project.createLayout(220, 140, "MILLIMETER")
frame = layout.createMapFrame(arcpy.Polygon(arcpy.Array([arcpy.Point(5,5), arcpy.Point(215,5), arcpy.Point(215,135), arcpy.Point(5,135)])), map_view)
frame.camera.setExtent(arcpy.Extent(379500, 6669500, 384500, 6679000))
layout.exportToPNG(str(out / "styles.png"), resolution=120)
project.saveACopy(str(out / "styles.aprx"))
# Render scale-dependent rules, rather than only checking persisted CIM.
scale_map = project.createMap("Mittakaavasymboliikka")
for background in scale_map.listLayers():
    scale_map.removeLayer(background)
scale_path = str(Path(gdb) / "scale_test")
arcpy.management.CopyFeatures(str(Path(gdb) / "tilastokeskus_0"), scale_path)
scale_layer = scale_map.addDataFromPath(scale_path)
data = b'<StyledLayerDescriptor xmlns="http://www.opengis.net/sld"><NamedLayer><Name>scale</Name><UserStyle><FeatureTypeStyle><Rule><Name>near</Name><MaxScaleDenominator>20000</MaxScaleDenominator><PolygonSymbolizer><Fill><CssParameter name="fill">#ff0000</CssParameter></Fill></PolygonSymbolizer></Rule><Rule><Name>far</Name><MinScaleDenominator>20000</MinScaleDenominator><PolygonSymbolizer><Fill><CssParameter name="fill">#00ff00</CssParameter></Fill></PolygonSymbolizer></Rule></FeatureTypeStyle></UserStyle></NamedLayer></StyledLayerDescriptor>'
tool._style_client = module.service_styles.StyleClient(lambda url: data)
tool._apply_provider_style(scale_layer, scale_path, {"kind": "wfs", "id": "scale", "endpoint": "https://example.test/ows"})
assert not warnings, warnings
frame.map = scale_map
from PIL import Image  # noqa: E402
for scale, rgb in ((10000, (255, 0, 0)), (50000, (0, 255, 0))):
    frame.camera.setExtent(arcpy.Extent(380000, 6676000, 380900, 6676900))
    frame.camera.scale = scale
    image_path = out / ("scale_" + str(scale) + ".png")
    layout.exportToPNG(str(image_path), resolution=80)
    pixels = Image.open(image_path).convert("RGB")
    assert sum(pixel == rgb for pixel in pixels.getdata()) > 10, (scale, "scale-dependent symbol did not render")
print("ArcGIS scale-dependent rendering passed", flush=True)
print("ArcGIS provider styles passed", out, flush=True)
