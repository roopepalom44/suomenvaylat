"""Tasosilmukan läpiajo vale-arcpyllä: geometriaryhmät, nimivaraus ja virheensieto."""

import pathlib
import sys
import types
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from test_audit_fixes import _ArcpyPatch, _new_tool  # noqa: E402
from test_toolbox_helpers import MODULE  # noqa: E402


def _param(text=None, values=None):
    return types.SimpleNamespace(valueAsText=text, values=values, datatype="GPString")


class ExecuteFlowTests(unittest.TestCase):
    def setUp(self):
        tool = _new_tool()
        self.tool = tool
        self.messages, self.warnings, self.errors = [], [], []
        tool._msg = self.messages.append
        tool._warn = self.warnings.append
        tool._error = self.errors.append
        tool._tool_metrics = MODULE.PhaseMetrics()
        tool._reserved_output_names = set()
        tool._run_had_layer_failures = False
        tool._run_scratch_gdb = "scratch.gdb"
        tool._run_scratch_folder = "scratch_folder"
        tool.wfs_registry = MODULE.WFSSourceRegistry()
        tool.heavy_chunk_sources = ["Väylä", "DigiRoad"]
        tool.heavy_layer_prefixes = ["liikennemaar"]
        tool.heavy_layer_exact = []
        tool._last_source_values = ["OpenStreetMap"]
        tool._http_transport = None
        tool._runtime_mml_api_key = ""
        tool._runtime_karttapaikka_api_key = ""
        tool._runtime_karttakuva_user = ""
        tool._runtime_karttakuva_pass = ""
        tool._runtime_aino_token = ""

        tool._init_workspace_cache = self._init_workspace
        tool._process_administrative_boundary = lambda *args: "boundary_fc"
        tool._boundary_extent_from_features = lambda fc: types.SimpleNamespace(
            XMin=0.0, YMin=0.0, XMax=10.0, YMax=10.0
        )
        tool._boundary_wkt_3067 = lambda fc, for_cql=False: "POLYGON ((0 0, 1 0, 1 1, 0 0))"
        tool._prepare_cql_wkts = lambda fc, wkt: ([wkt], {"elapsed_s": 0.0})
        tool._fetch_layer_list = lambda *args, **kwargs: []
        self.chunks = {
            "osm_addresses": ["pt1", "poly1", "pt2"],
            "osm_schools": ["pt3"],
            "osm_shops": ["pt4"],
            "osm_power": ["bad1"],
        }
        tool._fetch_osm_feature_chunks = lambda layer_id, boundary: (
            list(self.chunks[layer_id]), len(self.chunks[layer_id]), 1
        )
        self.copies = []
        tool._copy_features_compatible = self._copy
        tool._remove_local_output = lambda path: None
        tool._add_to_map = lambda path: (True, None)
        tool._layer_mapping = {
            "Osoitteet - OpenStreetMap": {"source": "OpenStreetMap", "id": "osm_addresses", "kind": "osm"},
            "Osoitteet - OpenStreetMap (2)": {"source": "OpenStreetMap", "id": "osm_schools", "kind": "osm"},
            "Osoitteet - OpenStreetMap (3)": {"source": "OpenStreetMap", "id": "osm_shops", "kind": "osm"},
            "Rikki - OpenStreetMap": {"source": "OpenStreetMap", "id": "osm_power", "kind": "osm"},
        }

    def _init_workspace(self, workspace):
        self.tool._runtime_workspace = workspace
        self.tool._runtime_workspace_is_folder = False
        self.tool._runtime_workspace_validated = True

    def _copy(self, source, workspace, name, metrics=None, output_known_absent=False):
        self.copies.append((source, name))
        return workspace + "\\" + name

    def _run(self, layers):
        shapes = {"pt1": "Point", "pt2": "Point", "pt3": "Point", "pt4": "Point",
                  "poly1": "Polygon", "bad1": "Polyline"}

        def describe(path):
            return types.SimpleNamespace(shapeType=shapes.get(path, "Polygon"), dataType="FeatureClass")

        def clip(source, boundary, output):
            if source == "bad1":
                raise RuntimeError("Clip failed")

        management = types.SimpleNamespace(
            Merge=lambda inputs, output: None,
            GetCount=lambda path: ["5"],
            CopyFeatures=lambda source, output: None,
            Delete=lambda path: None,
            DeleteIdentical=lambda path, fields: None,
        )
        parameters = [
            _param("OpenStreetMap", [["OpenStreetMap"]]),
            _param(""),
            _param(";".join(layers), list(layers)),
            _param("Kunta/Kaupunki"),
            _param("Helsinki"),
            _param(None),
            _param(r"C:\out.gdb"),
            _param(""), _param(""), _param(""), _param(""), _param(None), _param(""),
        ]
        with _ArcpyPatch(
            Describe=describe, Exists=lambda path: False, ExecuteError=RuntimeError,
            management=management, analysis=types.SimpleNamespace(Clip=clip),
        ):
            self.tool._execute_impl(parameters, None)

    def test_mixed_geometry_layer_gets_one_output_per_type_and_names_do_not_collide(self):
        self._run([
            "Osoitteet - OpenStreetMap",
            "Osoitteet - OpenStreetMap (2)",
            "Osoitteet - OpenStreetMap (3)",
        ])
        names = [name for _, name in self.copies]
        self.assertEqual(
            ["Helsinki", "Osoitteet_pisteet", "Osoitteet_alueet", "Osoitteet", "Osoitteet_1"],
            names,
        )
        self.assertEqual(len(names), len(set(name.casefold() for name in names)))
        self.assertFalse(self.tool._run_had_layer_failures)

    def _run_heavy(self, truncated_kunta):
        tool = self.tool
        tool._layer_mapping = {
            "Liikennemäärät - Väylä": {"source": "Väylä", "id": "tiestotiedot:liikennemaarat",
                                        "kind": "wfs"},
        }
        tool._last_source_values = ["Väylä"]
        tool._discover_wfs_schema = lambda *args, **kwargs: None
        tool._fetch_all_kunnat_fc = lambda: "kunnat_all"
        tool._select_kunnat_center_in = lambda all_fc, boundary: "kunnat_sel"
        tool._get_kunta_name_field = lambda fc: "nimi"
        extent = types.SimpleNamespace(XMin=0, YMin=0, XMax=1, YMax=1)
        tool._iter_kunnat = lambda fc, field: iter([
            (1, "Seinäjoki", types.SimpleNamespace(extent=extent)),
            (2, "Lapua", types.SimpleNamespace(extent=extent)),
        ])
        calls = []

        def fetch(**kwargs):
            calls.append(kwargs)
            truncated = len(calls) == 2 and truncated_kunta
            return ["kunta_fc_{}".format(len(calls))], 3, 1, {"truncated": truncated}, False

        tool._fetch_bbox_feature_chunks = fetch
        parameters = [
            _param("Väylä", [["Väylä"]]), _param(""),
            _param("Liikennemäärät - Väylä", ["Liikennemäärät - Väylä"]),
            _param("Maakunta"), _param("Etelä-Pohjanmaa"), _param(None), _param(r"C:\out.gdb"),
            _param(""), _param(""), _param(""), _param(""), _param(None), _param(""),
        ]
        management = types.SimpleNamespace(
            Merge=lambda inputs, output: None, GetCount=lambda path: ["6"],
            CopyFeatures=lambda source, output: None, Delete=lambda path: None,
            DeleteIdentical=lambda path, fields: None,
        )
        with _ArcpyPatch(
            Describe=lambda path: types.SimpleNamespace(shapeType="Polyline", dataType="FeatureClass"),
            Exists=lambda path: path in ("kunnat_all", "kunnat_sel"), ExecuteError=RuntimeError,
            ListFields=lambda path: [], management=management,
            analysis=types.SimpleNamespace(Clip=lambda *args: None),
        ):
            tool._execute_impl(parameters, None)
        return calls

    def test_heavy_layer_fetches_each_municipality_with_shared_pager(self):
        calls = self._run_heavy(truncated_kunta=False)
        self.assertEqual(2, len(calls))
        self.assertEqual(50, calls[0]["max_requests"])
        self.assertIsNone(calls[0]["boundary_wkt"])
        self.assertEqual(["Etela_Pohjanmaa", "Liikennemaarat"], [name for _, name in self.copies])

    def test_truncated_municipality_fails_only_that_layer(self):
        self._run_heavy(truncated_kunta=True)
        self.assertEqual(["Etela_Pohjanmaa"], [name for _, name in self.copies])
        self.assertTrue(self.tool._run_had_layer_failures)
        self.assertTrue(any("Lapua" in warning for warning in self.warnings))

    def test_failing_layer_does_not_stop_other_layers(self):
        self._run(["Rikki - OpenStreetMap", "Osoitteet - OpenStreetMap (2)"])
        names = [name for _, name in self.copies]
        self.assertEqual(["Helsinki", "Osoitteet"], names)
        self.assertTrue(self.tool._run_had_layer_failures)
        self.assertTrue(any("Rikki - OpenStreetMap" in warning and "Clip failed" in warning
                            for warning in self.warnings))
        self.assertIn("\n=== Ajo suoritettu osittain: onnistuneet tasot tallennettiin ===",
                      self.messages)


if __name__ == "__main__":
    unittest.main()
