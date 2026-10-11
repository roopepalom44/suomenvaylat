"""One ArcGIS tool must retain MML live maps and Kapsi file downloads."""
from pathlib import Path
import types
import unittest
from unittest.mock import Mock
import xml.etree.ElementTree as ET

from test_audit_fixes import _ArcpyPatch
import test_execute_flow as flows
from test_toolbox_helpers import MODULE


class Param:
    def __init__(self, value=None, values=None):
        self.value = value
        self.values = values
        self.enabled = True
        self.filter = types.SimpleNamespace(list=[])
        self.error = None

    @property
    def valueAsText(self):
        return ";".join(str(value) for value in self.values) if self.values else self.value

    def setErrorMessage(self, message):
        self.error = message

    def clearMessage(self):
        self.error = None


class UnifiedBasemapTests(unittest.TestCase):
    def setUp(self):
        self.harness = flows.ExecuteFlowTests()
        self.harness.setUp()
        self.tool = self.harness.tool
        self.tool._set_saved_secret = Mock()
        self.tiles = self.tool._get_mml_vector_tile_layers()
        self.labels = [entry["title"] + " - MML" for entry in self.tiles]
        self.tool._layer_mapping.update(zip(self.labels, self.tiles))
        self.added = []
        self.tool._add_mml_vector_tile_layer = lambda url, name, key: self.added.append((url, name, key))

    def params(self, labels=None, key="test-key"):
        labels = self.labels if labels is None else labels
        return [Param(values=[["MML"]]), Param(""), Param(values=labels),
                Param("Kunta/Kaupunki"), Param(), Param(), Param(),
                Param(key), Param(""), Param(""), Param(""), Param(False), Param("")]

    def test_only_one_public_tool_and_one_ribbon_button(self):
        self.assertEqual([MODULE.VaylaWFSDownloader], MODULE.Toolbox().tools)
        self.assertFalse(hasattr(MODULE, "MMLBasemapDownloader"))
        root = Path(__file__).resolve().parents[1]
        daml = ET.parse(root / "Config.daml").getroot()
        buttons = daml.findall(".//{http://schemas.esri.com/DADF/Registry}controls/{http://schemas.esri.com/DADF/Registry}button")
        self.assertEqual(["OpenSuomenvaylatToolButton"], [button.get("className") for button in buttons])

    def test_mml_catalog_retains_ogc_collections_and_adds_three_live_maps(self):
        self.tool._get_mml_ogc_layers = lambda: [{"id": "parcel", "title": "Palstat", "kind": "mml_property_ogcapi"}]
        labels = self.tool._get_layer_entries_for_sources(["MML"])
        self.assertEqual(4, len(labels))
        kinds = [entry["kind"] for entry in self.tool._layer_mapping.values()]
        self.assertEqual(3, kinds.count("mml_vector_tile"))
        self.assertEqual(1, kinds.count("mml_property_ogcapi"))
        self.assertEqual({"Taustakartta", "Maastokartta", "Kiinteistojaotus"}, {tile["style"] for tile in self.tiles})

    def test_live_maps_still_listed_when_property_catalog_is_unavailable(self):
        self.tool._get_mml_ogc_layers = Mock(side_effect=RuntimeError("offline"))
        self.assertEqual(3, len(self.tool._get_layer_entries_for_sources(["MML"])))

    def test_live_only_execute_skips_area_and_workspace(self):
        self.tool._resolve_output_workspace = Mock(side_effect=AssertionError("must not need workspace"))
        self.tool._process_administrative_boundary = Mock(side_effect=AssertionError("must not need boundary"))
        with _ArcpyPatch(ExecuteError=RuntimeError):
            self.tool._execute_impl(self.params(), None)
        self.assertEqual(3, len(self.added))
        self.assertTrue(all(key == "test-key" for _, _, key in self.added))
        self.tool._set_saved_secret.assert_called_once_with("mml_api_key", "test-key")
        self.assertFalse(self.harness.copies)

    def test_live_failure_continues_and_reports_partial_result(self):
        def add(url, name, key):
            if name == "Maastokartta":
                raise RuntimeError("offline")
            self.added.append(name)
        self.tool._add_mml_vector_tile_layer = add
        self.tool._execute_impl(self.params(), None)
        self.assertEqual(2, len(self.added))
        self.assertTrue(self.tool._run_had_layer_failures)
        self.assertTrue(any("osittain" in message for message in self.harness.messages))

    def test_mixed_live_and_vector_files_use_the_same_tool(self):
        self.harness._run([self.labels[1], "Osoitteet - OpenStreetMap (2)"], mml_api_key="test-key")
        self.assertEqual("Maastokartta", self.added[0][1])
        self.assertIn("Osoitteet", [name for _, name in self.harness.copies])

    def test_kapsi_raster_download_remains_in_main_tool(self):
        label = "Peruskartta - Kapsi"
        self.tool._layer_mapping[label] = {"source": "Kapsi", "id": "peruskartta", "kind": "kapsi_wms"}
        downloads, copies = [], []
        self.tool._download_kapsi_wms_jpeg = lambda identifier, boundary, folder: downloads.append((identifier, boundary)) or "scratch_folder/map.jpg"
        self.tool._copy_raster_bundle_to_workspace = lambda path, workspace: copies.append((path, workspace)) or "output/map.jpg"
        self.harness._run([label, self.labels[0]], mml_api_key="test-key")
        self.assertEqual([("peruskartta", "boundary_fc")], downloads)
        self.assertEqual(1, len(copies))
        self.assertEqual(1, len(self.added))

    def test_live_parameters_disable_area_but_keep_key_validation(self):
        params = self.params()
        params[3].setErrorMessage("previous boundary error")
        self.tool._all_wfs_layers_cache = {}
        self.tool._fetch_layer_list = lambda *args, **kwargs: list(self.labels)
        self.tool.updateParameters(params)
        self.tool.updateMessages(params)
        self.assertFalse(any(params[index].enabled for index in (3, 4, 5, 6)))
        self.assertFalse(any(param.error for param in params))
        params[7].value = ""
        self.tool.updateMessages(params)
        self.assertIn("API-avaimen", params[7].error)

    def test_adding_a_file_layer_restores_area_validation(self):
        params = self.params([self.labels[0], "Osoitteet - OpenStreetMap (2)"])
        self.tool._all_wfs_layers_cache = {}
        self.tool._fetch_layer_list = lambda *args, **kwargs: list(params[2].values)
        self.tool._get_extent_choices = lambda area: ["Helsinki"]
        self.tool._find_admin_gpkg = lambda: "admin.gpkg"
        self.tool.updateParameters(params)
        self.tool.updateMessages(params)
        self.assertTrue(params[3].enabled and params[6].enabled)
        self.assertIsNotNone(params[4].error)

    def test_arcgis_overpass_uses_the_shared_endpoint_list(self):
        self.assertEqual(list(MODULE.service_network.OVERPASS_ENDPOINTS), self.tool.wfs_registry.get_endpoints("OpenStreetMap"))


if __name__ == "__main__":
    unittest.main()
