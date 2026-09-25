"""Run a live Oskari-to-File-GDB check with ArcGIS Pro's Python environment.

Invoke with:
    propy.bat tests/smoke_traficom_oskari_arcgispro.py
"""

import importlib.machinery
import importlib.util
import json
import os
import pathlib
import shutil
import tempfile
from collections import Counter

import arcpy


ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLBOX_PATH = ROOT / "Toolboxes" / "VaylaWFSDownloader.pyt"
TEST_BBOX = (260000, 6600000, 420000, 6700000)


def load_toolbox():
    loader = importlib.machinery.SourceFileLoader(
        "vayla_oskari_arcgispro_smoke", str(TOOLBOX_PATH)
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def make_boundary(workspace):
    name = "oskari_test_boundary"
    arcpy.management.CreateFeatureclass(
        workspace, name, "POLYGON", spatial_reference=arcpy.SpatialReference(3067)
    )
    points = [
        arcpy.Point(TEST_BBOX[0], TEST_BBOX[1]),
        arcpy.Point(TEST_BBOX[2], TEST_BBOX[1]),
        arcpy.Point(TEST_BBOX[2], TEST_BBOX[3]),
        arcpy.Point(TEST_BBOX[0], TEST_BBOX[3]),
        arcpy.Point(TEST_BBOX[0], TEST_BBOX[1]),
    ]
    polygon = arcpy.Polygon(
        arcpy.Array(points), arcpy.SpatialReference(3067)
    )
    feature_class = os.path.join(workspace, name)
    with arcpy.da.InsertCursor(feature_class, ["SHAPE@"]) as cursor:
        cursor.insertRow([polygon])
    return feature_class


def main():
    arcpy.env.overwriteOutput = True
    temporary_directory = tempfile.mkdtemp(prefix="traficom_oskari_arcgispro_")
    try:
        output_gdb = os.path.join(temporary_directory, "oskari_smoke.gdb")
        arcpy.management.CreateFileGDB(temporary_directory, "oskari_smoke.gdb")
        boundary = make_boundary(output_gdb)

        module = load_toolbox()
        tool = module.VaylaWFSDownloader()
        catalog = tool._get_traficom_oskari_layers()
        catalog_types = Counter(layer.get("catalog_type") for layer in catalog)
        if len(catalog) < 76:
            raise RuntimeError(
                "Oskarin tasoluettelo palautti vain {} tasoa; odotettiin vähintään 76.".format(
                    len(catalog)
                )
            )
        expected_catalog_minimums = {"wmslayer": 67, "wmtslayer": 5, "wfslayer": 4}
        if any(catalog_types.get(kind, 0) < count for kind, count in expected_catalog_minimums.items()):
            raise RuntimeError(
                "Oskarin palvelutyyppien määrät jäivät odotettua pienemmiksi: {}".format(
                    dict(catalog_types)
                )
            )
        download_methods = Counter(layer.get("kind") for layer in catalog)
        expected_method_minimums = {
            "wfs": 31,
            "oskari_wfs": 4,
            "oskari_wms": 35,
            "oskari_wmts": 5,
        }
        if any(download_methods.get(kind, 0) < count for kind, count in expected_method_minimums.items()):
            raise RuntimeError(
                "Oskarin lataustapoja löytyi odotettua vähemmän: {}".format(
                    dict(download_methods)
                )
            )

        parameters = tool.getParameterInfo()
        if "Traficom Oskari" not in parameters[0].filters[0].list:
            raise RuntimeError("Traficom Oskari ei tullut ArcGIS Pro -lähdevalikkoon.")

        parameters[0].values = [["Traficom Oskari"]]
        parameters[3].value = "Oma aineisto (Polygon/Polyline)"
        parameters[5].value = boundary
        parameters[6].value = output_gdb
        parameters[11].value = True
        tool.updateParameters(parameters)

        layer_label = next(
            (
                value for value in parameters[2].filter.list
                if "Matkustaja-alusten D-alueet" in value
            ),
            None,
        )
        if not layer_label:
            raise RuntimeError(
                "Oskarin dynaamisesta tasolistasta puuttuu Matkustaja-alusten D-alueet."
            )

        parameters[2].value = layer_label
        tool.updateParameters(parameters)
        tool.updateMessages(parameters)
        tool.execute(parameters, None)

        arcpy.env.workspace = output_gdb
        outputs = arcpy.ListFeatureClasses() or []
        outputs = [name for name in outputs if name != "oskari_test_boundary"]
        if not outputs:
            raise RuntimeError(
                "ArcGIS Pro ei luonut Oskarin tulosfeature classia. "
                + arcpy.GetMessages()
            )
        output = os.path.join(output_gdb, outputs[0])
        count = int(arcpy.management.GetCount(output)[0])
        spatial_reference = arcpy.Describe(output).spatialReference
        spatial_code = int(getattr(spatial_reference, "factoryCode", 0) or 0)
        shape_type = arcpy.Describe(output).shapeType
        if count < 1:
            raise RuntimeError("ArcGIS Pro -tulos jäi tyhjäksi.")
        if spatial_code != 3067:
            raise RuntimeError(
                "ArcGIS Pro -tuloksen koordinaatisto oli EPSG:{}, ei EPSG:3067.".format(
                    spatial_code
                )
            )

        wms_output = tool._download_oskari_wms_geotiff(
            layer_id="53",
            layer_name="TN_RUNWAYAREA",
            layer_title="Runway Area",
            style="",
            boundary_fc=boundary,
            workspace=temporary_directory,
        )
        wmts_output = tool._download_oskari_wmts_geotiff(
            layer_name="Traficom:Yleiskartat 250k public",
            layer_title="Yleiskartat 250k",
            boundary_fc=boundary,
            workspace=temporary_directory,
        )
        raster_results = {}
        for service, raster_path in (("WMS", wms_output), ("WMTS", wmts_output)):
            if not arcpy.Exists(raster_path):
                raise RuntimeError("{} ei luonut ArcGIS-rasteria: {}".format(service, raster_path))
            raster_desc = arcpy.Describe(raster_path)
            raster_sr = getattr(raster_desc, "spatialReference", None)
            raster_code = int(getattr(raster_sr, "factoryCode", 0) or 0)
            if raster_code != 3067:
                raise RuntimeError(
                    "{}-tulosrasterin koordinaatisto oli EPSG:{}, ei EPSG:3067.".format(
                        service, raster_code
                    )
                )
            raster_results[service] = {
                "name": os.path.basename(raster_path),
                "spatial_reference": raster_code,
                "width": int(arcpy.Raster(raster_path).width),
                "height": int(arcpy.Raster(raster_path).height),
            }
        print(json.dumps({
            "arcgis_pro_version": arcpy.GetInstallInfo().get("Version"),
            "source": "Traficom Oskari",
            "catalog_count": len(catalog),
            "catalog_service_types": dict(catalog_types),
            "catalog_download_methods": dict(download_methods),
            "layer": layer_label,
            "test_bbox_epsg_3067": TEST_BBOX,
            "output_feature_class": os.path.basename(output),
            "feature_count": count,
            "geometry_type": shape_type,
            "spatial_reference": spatial_code,
            "raster_results": raster_results,
            "result": "passed",
        }, ensure_ascii=False))
    finally:
        try:
            arcpy.management.ClearWorkspaceCache()
        except Exception:
            pass
        shutil.rmtree(temporary_directory, ignore_errors=True)


if __name__ == "__main__":
    main()
