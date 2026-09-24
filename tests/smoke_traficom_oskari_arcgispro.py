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
        print(json.dumps({
            "arcgis_pro_version": arcpy.GetInstallInfo().get("Version"),
            "source": "Traficom Oskari",
            "layer": layer_label,
            "test_bbox_epsg_3067": TEST_BBOX,
            "output_feature_class": os.path.basename(output),
            "feature_count": count,
            "geometry_type": shape_type,
            "spatial_reference": spatial_code,
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
