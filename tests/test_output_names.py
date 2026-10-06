"""Pitkien aluevalintojen nimet eivät saa rikkoa ArcGIS-vientiä."""

import os
import types
import unittest
from unittest.mock import patch

from test_toolbox_helpers import MODULE


PROVINCES = [
    "Ahvenanmaa", "Etelä-Karjala", "Etelä-Savo", "Kainuu", "Kanta-Häme",
    "Keski-Pohjanmaa", "Keski-Suomi", "Kymenlaakso", "Lappi", "Pirkanmaa",
    "Pohjois-Karjala", "Pohjois-Pohjanmaa", "Pohjois-Savo", "Päijät-Häme",
    "Satakunta", "Uusimaa", "Varsinais-Suomi",
]


class OutputNameTests(unittest.TestCase):
    def setUp(self):
        self.tool = MODULE.VaylaWFSDownloader.__new__(MODULE.VaylaWFSDownloader)
        self.tool._runtime_workspace = None
        self.tool._runtime_workspace_is_folder = None
        self.tool._run_scratch_gdb = "scratch.gdb"
        self.tool._run_id = "abcdef12"
        self.tool._is_filesystem_workspace = lambda ws: not ws.endswith(".gdb")
        self.validator = patch.object(
            MODULE.arcpy, "ValidateTableName", side_effect=lambda name, ws: name,
            create=True,
        )
        self.validate = self.validator.start()
        self.addCleanup(self.validator.stop)

    def test_reported_province_selection_is_bounded_before_arcpy(self):
        name = self.tool._validated_name("+".join(PROVINCES), "scratch.gdb")
        self.assertLessEqual(len(name), 60)
        self.assertTrue(name.startswith("Ahvenanmaa_Etela_Karjala_"))
        self.assertRegex(name, r"_[0-9a-f]{12}$")
        self.assertEqual(name, self.validate.call_args.args[0])

    def test_any_selection_size_fits_gdb_and_shapefile_names(self):
        for count in (1, 17, 309, 10000):
            raw = "+".join("Äänekoski_{}".format(i) for i in range(count))
            for workspace in ("scratch.gdb", "folder", r"\\server\share\out.gdb"):
                with self.subTest(count=count, workspace=workspace):
                    path = self.tool._dataset_output_path(workspace, raw)
                    name = os.path.basename(path)
                    if workspace == "folder":
                        self.assertTrue(name.endswith(".shp"))
                        name = name[:-4]
                    self.assertLessEqual(len(name), 60)
                    self.assertRegex(name, r"^[A-Za-z_][A-Za-z0-9_]*$")

    def test_same_prefix_with_different_final_area_gets_different_name(self):
        prefix = "+".join(PROVINCES)
        first = self.tool._validated_name(prefix + "+Helsinki", "scratch.gdb")
        second = self.tool._validated_name(prefix + "+Espoo", "scratch.gdb")
        self.assertNotEqual(first, second)
        self.assertEqual(first, self.tool._validated_name(first, "scratch.gdb"))

    def test_short_names_and_shapefile_extension_are_preserved(self):
        self.assertEqual("Etela_Savo", self.tool._validated_name("Etelä-Savo", "scratch.gdb"))
        self.assertEqual(
            os.path.join("folder", "Etela_Savo.shp"),
            self.tool._dataset_output_path("folder", "Etelä-Savo.shp"),
        )

    def test_failed_arcpy_validation_still_produces_bounded_name(self):
        self.validate.side_effect = RuntimeError("validation unavailable")
        name = self.tool._validated_name("+".join(PROVINCES), "scratch.gdb")
        self.assertLessEqual(len(name), 60)

    def test_unique_names_reserve_room_for_local_and_remote_suffixes(self):
        raw = "+".join(PROVINCES)
        with patch.object(MODULE.arcpy, "Exists", return_value=False, create=True):
            for workspace in ("scratch.gdb", r"\\server\share\out.gdb"):
                names = [self.tool._unique_output_name(raw, workspace) for _ in range(100)]
                self.assertEqual(100, len(set(names)))
                self.assertTrue(all(len(name) <= 60 for name in names))
            self.assertLessEqual(len(self.tool._unique_output_name(raw, "scratch.gdb", 200)), 60)

    def test_export_and_delete_use_same_bounded_output_path(self):
        exports, deletes = [], []
        self.tool._safe_delete = deletes.append
        conversion = types.SimpleNamespace(ExportFeatures=lambda *args: exports.append(args))
        raw = "+".join(PROVINCES)
        with patch.object(MODULE.arcpy, "conversion", conversion, create=True):
            result = self.tool._feature_class_to_workspace("source", "scratch.gdb", raw, "nimi IN ('Ahvenanmaa')")
        self.assertEqual([result], deletes)
        self.assertEqual(result, exports[0][1])
        self.assertEqual("nimi IN ('Ahvenanmaa')", exports[0][2])
        self.assertLessEqual(len(os.path.basename(result)), 60)

    def test_legacy_export_uses_same_bounded_name(self):
        calls = []
        conversion = types.SimpleNamespace(FeatureClassToFeatureClass=lambda *args: calls.append(args))
        with patch.object(MODULE.arcpy, "conversion", conversion, create=True):
            result = self.tool._export_features_compat("source", "scratch.gdb", "+".join(PROVINCES), "selection")
        self.assertEqual(os.path.basename(result), calls[0][2])
        self.assertLessEqual(len(calls[0][2]), 60)

    def test_raster_collision_suffix_stays_within_name_limit(self):
        self.tool._raster_folder = lambda workspace: "folder"
        copied = []
        management = types.SimpleNamespace(CopyRaster=lambda *args: copied.append(args))
        with patch.object(MODULE.arcpy, "management", management, create=True), \
                patch.object(MODULE.os.path, "exists", side_effect=[True, False]):
            result = self.tool._copy_raster_to_workspace("R" * 200 + ".tif", "folder")
        self.assertEqual(result, copied[0][1])
        self.assertTrue(result.endswith("_1.tif"))
        self.assertLessEqual(len(os.path.splitext(os.path.basename(result))[0]), 60)

    def test_raster_bundle_collision_suffix_stays_within_name_limit(self):
        self.tool._raster_folder = lambda workspace: "folder"
        with patch.object(MODULE.shutil, "copy2") as copy, \
                patch.object(MODULE.os.path, "exists", side_effect=[True, False, False, False]):
            result = self.tool._copy_raster_bundle_to_workspace("R" * 200 + ".jpg", "folder")
        self.assertEqual(result, copy.call_args.args[1])
        self.assertTrue(result.endswith("_1.jpg"))
        self.assertLessEqual(len(os.path.splitext(os.path.basename(result))[0]), 60)


if __name__ == "__main__":
    unittest.main()
