"""Todellinen ArcPy-vienti pitkillä valinnoilla (aja ArcGIS Pron Pythonilla)."""

import argparse
import importlib.machinery
import importlib.util
import json
from pathlib import Path

import arcpy


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    arcpy.management.CreateFileGDB(str(output_dir), "results.gdb")
    folder = output_dir / "shapefiles"
    folder.mkdir()

    toolbox = Path(__file__).resolve().parents[1] / "Toolboxes" / "VaylaWFSDownloader.pyt"
    loader = importlib.machinery.SourceFileLoader("boundary_names_smoke", str(toolbox))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    tool = module.VaylaWFSDownloader()
    tool._create_run_scratch()
    results = []
    try:
        provinces = [
            "Ahvenanmaa", "Etelä-Karjala", "Etelä-Savo", "Kainuu", "Kanta-Häme",
            "Keski-Pohjanmaa", "Keski-Suomi", "Kymenlaakso", "Lappi", "Pirkanmaa",
            "Pohjois-Karjala", "Pohjois-Pohjanmaa", "Pohjois-Savo", "Päijät-Häme",
            "Satakunta", "Uusimaa", "Varsinais-Suomi",
        ]
        cases = [("reported_provinces", "Maakunta", provinces)]
        for extent_type in ("Maakunta", "Kunta/Kaupunki", "Elinvoimakeskus", "Hyvinvointialue"):
            source, field = tool._get_extent_fc_and_namefield(extent_type)
            with arcpy.da.SearchCursor(source, [field]) as cursor:
                values = sorted({row[0] for row in cursor if row[0]})
            cases.append(("all_" + tool.admin_layer_names[extent_type], extent_type, values))
        for label, extent_type, values in cases:
            for workspace in (tool._scratch_gdb(), str(output_dir / "results.gdb"), str(folder)):
                result = tool._process_administrative_boundary(extent_type, values, workspace)
                count = int(arcpy.management.GetCount(result)[0])
                name = Path(result).name
                stem = name[:-4] if name.lower().endswith(".shp") else name
                assert count == len(values), (label, count, len(values))
                assert len(stem) <= 60, name
                assert arcpy.Describe(result).spatialReference.factoryCode == 3067
                row = dict(case=label, selected=len(values), features=count,
                           raw_name_length=len("+".join(values)), name_length=len(stem), path=result)
                results.append(row)
                print(json.dumps(row, ensure_ascii=True), flush=True)
        source, _ = tool._get_extent_fc_and_namefield("Maakunta")
        for workspace in (str(output_dir / "results.gdb"), str(folder)):
            result = tool._export_geometry_only(source, workspace, "geometry_" + "+".join(provinces))
            count = int(arcpy.management.GetCount(result)[0])
            name = Path(result).name
            stem = name[:-4] if name.lower().endswith(".shp") else name
            assert count == int(arcpy.management.GetCount(source)[0])
            assert len(stem) <= 60, name
            row = dict(case="geometry_only", features=count, name_length=len(stem), path=result)
            results.append(row)
            print(json.dumps(row, ensure_ascii=True), flush=True)
        (output_dir / "results.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        tool._cleanup_run_scratch(preserve=True)
        raise
    else:
        tool._cleanup_run_scratch()
    print("ArcGIS Pro boundary-name regression passed", flush=True)


if __name__ == "__main__":
    main()
