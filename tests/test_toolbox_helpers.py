import importlib.machinery
import importlib.util
import os
import pathlib
import sys
import types
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLBOX = ROOT / "Toolboxes" / "VaylaWFSDownloader.pyt"


def load_toolbox_module():
    old_arcpy = sys.modules.get("arcpy")
    sys.modules["arcpy"] = types.ModuleType("arcpy")
    try:
        loader = importlib.machinery.SourceFileLoader("vayla_toolbox_test", str(TOOLBOX))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        return module
    finally:
        if old_arcpy is None:
            sys.modules.pop("arcpy", None)
        else:
            sys.modules["arcpy"] = old_arcpy


MODULE = load_toolbox_module()


class ToolboxHelperTests(unittest.TestCase):
    def setUp(self):
        self.tool = MODULE.VaylaWFSDownloader.__new__(MODULE.VaylaWFSDownloader)
        self.tool._wfs_geometry_field_cache = {}
        self.tool._wfs_sort_candidate_cache = {}
        self.tool._wfs_sort_field_cache = {}
        self.tool._verbose_diagnostics = False

    def test_sanitized_url_drops_credentials_query_and_fragment(self):
        value = self.tool._sanitize_url(
            "https://user:password@example.test:8443/wfs?api_key=secret#token"
        )
        self.assertEqual("https://example.test:8443/wfs", value)
        self.assertNotIn("secret", value)
        self.assertNotIn("password", value)

    def test_runtime_secrets_are_redacted_from_log_text(self):
        self.tool._runtime_mml_api_key = "key-value"
        self.tool._runtime_karttapaikka_api_key = ""
        self.tool._runtime_karttakuva_user = "user-value"
        self.tool._runtime_karttakuva_pass = "pass-value"
        value = self.tool._redact_secrets(
            "key-value user-value pass-value"
        )
        self.assertEqual("[PIILOTETTU] [PIILOTETTU] [PIILOTETTU]", value)

    def test_layer_label_removes_redundant_digiroad_parenthesis(self):
        self.assertEqual(
            "Esterakenne - DigiRoad",
            self.tool._format_layer_label("Esterakenne (Digiroad)", "DigiRoad"),
        )

    def test_layer_placeholder_is_not_a_real_selection(self):
        self.assertTrue(self.tool._is_layer_placeholder("(ei osumia – tyhjennä haku)"))
        self.assertTrue(self.tool._is_layer_placeholder("(no matches – clear search)"))
        self.assertFalse(self.tool._is_layer_placeholder("tieviiva - Karttapaikka"))

    def test_source_selection_survives_empty_validation_value(self):
        class Param:
            def __init__(self, value=None, values=None, datatype=None):
                self.value = value
                self.values = values
                self.datatype = datatype

            @property
            def valueAsText(self):
                if self.values:
                    return ";".join(
                        str(item[0] if isinstance(item, (list, tuple)) else item)
                        for item in self.values
                    )
                return self.value

        param = Param(values=[["Karttapaikka"]], datatype="GPValueTable")
        self.assertEqual(["Karttapaikka"], self.tool._source_values_from_param(param))

        param.values = []
        param.value = None
        self.assertEqual(["Karttapaikka"], self.tool._source_values_from_param(param))
        self.assertEqual([["Karttapaikka"]], param.values)

    def test_karttapaikka_uses_current_mml_endpoints(self):
        registry = MODULE.WFSSourceRegistry()
        endpoints = registry.get_endpoints("Karttapaikka")
        self.assertTrue(endpoints)
        self.assertTrue(all("inspire-wfs.maanmittauslaitos.fi" in url for url in endpoints))
        self.assertIn(
            "https://avoin-paikkatieto.maanmittauslaitos.fi/maastotiedot/features/v1/",
            registry.get_ogc_endpoint("Karttapaikka"),
        )

    def test_karttapaikka_auth_is_only_sent_to_ogc_api(self):
        self.tool._runtime_karttapaikka_api_key = "test-key"
        self.assertEqual(
            {},
            self.tool._build_source_auth_headers("Karttapaikka", layer_kind="wfs"),
        )
        headers = self.tool._build_source_auth_headers(
            "Karttapaikka",
            endpoint="https://avoin-paikkatieto.maanmittauslaitos.fi/maastotiedot/features/v1/",
            layer_kind="mml_ogcapi",
        )
        self.assertIn("Authorization", headers)
        self.assertNotIn("test-key", headers["Authorization"])

    def test_secret_cache_key_preserves_api_key_case_without_exposing_it(self):
        lower = self.tool._secret_cache_key("AbC-123")
        upper = self.tool._secret_cache_key("aBc-123")
        self.assertNotEqual(lower, upper)
        self.assertNotIn("AbC-123", lower)

    def test_ogc_pages_follow_next_link_and_keep_projection_stats(self):
        pages = [
            (
                {
                    "type": "FeatureCollection",
                    "features": [{"type": "Feature", "geometry": None, "properties": {}}],
                    "links": [{"rel": "next", "href": "/next?page=2"}],
                },
                '{"features":[{}]}',
            ),
            (
                {
                    "type": "FeatureCollection",
                    "features": [{"type": "Feature", "geometry": None, "properties": {}}],
                    "links": [],
                },
                '{"features":[{}]}',
            ),
        ]
        calls = []

        def fake_fetch(url, timeout=60, quiet=False, extra_headers=None, timings=None):
            calls.append(url)
            data, raw = pages.pop(0)
            return data, raw, 200, "application/geo+json"

        conversion = MODULE.PhaseMetrics()
        conversion.set("JSONToFeatures", 0.2)
        conversion.set("projektointi", 0.3)
        self.tool._fetch_json = fake_fetch
        self.tool._json_to_temp_fc = lambda raw, project_to_epsg=None: ("fc{}".format(len(calls)), conversion)
        self.tool._warn = lambda message: None
        chunks, found, stats = self.tool._fetch_ogcapi_feature_chunks(
            "https://example.test/features/v1/",
            "tieviiva",
            "23,61,24,62",
            1,
        )
        self.assertEqual(["fc1", "fc2"], chunks)
        self.assertEqual(2, found)
        self.assertEqual(2, stats["pages"])
        self.assertIn("/next?page=2", calls[1])
        self.assertAlmostEqual(0.4, stats["json_to_features_s"])
        self.assertAlmostEqual(0.6, stats["projection_s"])

    def test_empty_layer_list_refetches_after_api_key_change(self):
        class Filter:
            def __init__(self):
                self.list = []

        class Param:
            def __init__(self, value=None, values=None):
                self.value = value
                self.values = values
                self.filter = Filter()
                self.enabled = True
                self.errors = []

            @property
            def valueAsText(self):
                if self.values:
                    return ";".join(str(item[0] if isinstance(item, (list, tuple)) else item)
                                    for item in self.values)
                return self.value

            def setErrorMessage(self, message):
                self.errors.append(message)

            def clearMessage(self):
                self.errors = []

        self.tool._all_wfs_layers_cache = {}
        self.tool._layer_mapping = {}
        self.tool._runtime_mml_api_key = ""
        self.tool._runtime_karttapaikka_api_key = ""
        self.tool._runtime_karttakuva_user = ""
        self.tool._runtime_karttakuva_pass = ""
        calls = []

        def fake_fetch_layer_list(sources):
            calls.append(self.tool._runtime_karttapaikka_api_key)
            return (
                ["tieviiva (Maastotiedot) - Karttapaikka"]
                if self.tool._runtime_karttapaikka_api_key else []
            )

        self.tool._fetch_layer_list = fake_fetch_layer_list
        self.tool._get_extent_choices = lambda extent_type: []
        self.tool._warn = lambda message: None
        parameters = [
            Param(values=[["Karttapaikka"]]),
            Param(""),
            Param(value="(ei osumia – tyhjennä haku)", values=["(ei osumia – tyhjennä haku)"]),
            Param(None), Param(None), Param(None), Param(None),
            Param(""), Param(""), Param(""), Param(""),
        ]

        self.tool.updateParameters(parameters)
        self.assertEqual([], parameters[2].filter.list)
        self.assertEqual([], parameters[2].values)

        parameters[8].value = "new-key"
        self.tool.updateParameters(parameters)
        self.assertEqual(
            ["tieviiva (Maastotiedot) - Karttapaikka"],
            parameters[2].filter.list,
        )
        self.assertEqual(["", "new-key"], calls)

    def test_layer_selection_survives_filter_refresh(self):
        class Filter:
            def __init__(self, owner=None):
                self.owner = owner
                self._list = []
                self.assignments = 0

            @property
            def list(self):
                return self._list

            @list.setter
            def list(self, value):
                self._list = list(value)
                self.assignments += 1
                if self.owner is not None:
                    self.owner.values = []
                    self.owner.value = None

        class Param:
            def __init__(self, value=None, values=None, datatype=None, clears_on_filter=False):
                self.value = value
                self.values = values
                self.datatype = datatype
                self.filter = Filter(self if clears_on_filter else None)
                self.enabled = True

            @property
            def valueAsText(self):
                if self.values:
                    return ";".join(
                        str(item[0] if isinstance(item, (list, tuple)) else item)
                        for item in self.values
                    )
                return self.value

        layer_name = "Tiestötiedot - Väylä"
        parameters = [
            Param(values=[["Väylä"]], datatype="GPValueTable"),
            Param(""),
            Param(values=[layer_name], clears_on_filter=True),
            Param("Koko Suomi"), Param(None), Param(None), Param("C:\\output.gdb"),
            Param(""), Param(""), Param(""), Param(""),
        ]
        self.tool._all_wfs_layers_cache = {}
        self.tool._runtime_mml_api_key = ""
        self.tool._runtime_karttapaikka_api_key = ""
        self.tool._runtime_karttakuva_user = ""
        self.tool._runtime_karttakuva_pass = ""
        self.tool._fetch_layer_list = lambda sources: [layer_name]
        self.tool._warn = lambda message: None

        self.tool.updateParameters(parameters)
        self.assertEqual([layer_name], parameters[2].values)
        self.assertEqual(1, parameters[2].filter.assignments)

        parameters[8].value = "new-key"
        self.tool.updateParameters(parameters)
        self.assertEqual([layer_name], parameters[2].values)
        self.assertEqual(1, parameters[2].filter.assignments)

        self.tool.updateParameters(parameters)
        self.assertEqual([layer_name], parameters[2].values)
        self.assertEqual(1, parameters[2].filter.assignments)

    def test_kapsi_uses_selected_scale_dependent_layer(self):
        self.assertEqual(
            "taustakartta_800k",
            self.tool._kapsi_request_layer(
                "https://tiles.kartat.kapsi.fi/taustakartta",
                "taustakartta_800k",
            ),
        )

    def test_kapsi_target_gsd_uses_capabilities_scale_range(self):
        layer_ref = (
            "https://tiles.kartat.kapsi.fi/taustakartta|taustakartta_800k"
        )
        self.tool._kapsi_layer_scale_ranges = {
            layer_ref: (500000.0, 1200000.0)
        }
        gsd, exact_scale = self.tool._kapsi_target_gsd(
            layer_ref, "taustakartta_800k"
        )
        self.assertTrue(exact_scale)
        self.assertAlmostEqual(
            (500000.0 * 1200000.0) ** 0.5 * 0.00028,
            gsd,
        )

    def test_kapsi_parent_layer_keeps_automatic_resolution(self):
        layer_ref = "https://tiles.kartat.kapsi.fi/taustakartta|taustakartta"
        self.tool._kapsi_layer_scale_ranges = {
            layer_ref: (None, 7000.0)
        }
        self.assertEqual(
            (20.0, False),
            self.tool._kapsi_target_gsd(layer_ref, "taustakartta"),
        )

    def test_kapsi_exact_scale_rejects_excessive_tile_count(self):
        layer_ref = (
            "https://tiles.kartat.kapsi.fi/taustakartta|taustakartta_5k"
        )
        self.tool._kapsi_layer_scale_ranges = {
            layer_ref: (None, 7000.0)
        }
        self.tool._boundary_extent_3067 = lambda boundary: types.SimpleNamespace(
            XMin=0.0, YMin=0.0, XMax=100000.0, YMax=100000.0
        )
        self.tool.kapsi_wms_base = "https://tiles.kartat.kapsi.fi/ortokuva"

        with self.assertRaisesRegex(Exception, "Rajaa pienempi alue"):
            self.tool._download_kapsi_wms_jpeg(
                layer_ref, "boundary", "C:\\output"
            )

    @unittest.skipUnless(os.name == "nt", "DPAPI is Windows-only")
    def test_dpapi_secret_round_trip_is_not_plaintext(self):
        encrypted = self.tool._protect_secret("test-secret")
        self.assertTrue(encrypted.startswith("dpapi:"))
        self.assertNotIn("test-secret", encrypted)
        self.assertEqual("test-secret", self.tool._unprotect_secret(encrypted))

    def test_phase_metrics_distinguish_skipped_from_zero(self):
        metrics = MODULE.PhaseMetrics()
        metrics.set("fast", 0.0)
        metrics.skip("unused", "ei tarpeen")
        self.assertEqual(0.0, metrics.get("fast"))
        self.assertIsNone(metrics.get("unused"))
        self.assertEqual("ei tarpeen", metrics.status["unused"])

    def test_existing_wkt_2d_conversion_removes_z_dimension(self):
        source = "POLYGON Z ((1 2 3, 4 5 6, 1 2 3))"
        self.assertEqual(
            "POLYGON ((1 2, 4 5, 1 2))",
            self.tool._wkt_force_2d(source),
        )

    def test_cql_get_url_keeps_intersects_and_encodes_spaces(self):
        cql = "INTERSECTS(geom, POLYGON ((1 2, 3 4, 1 2)))"
        url = self.tool._build_wfs_getfeature_url(
            "https://example.test/wfs", "digiroad:test", 5000, 0,
            "application/json", cql_filter=cql, geometry_only=False,
        )
        self.assertIn("CQL_FILTER=INTERSECTS", url)
        self.assertNotIn(" ", url)
        self.assertNotIn("bbox=", url)

    def test_remote_output_name_uses_run_id_without_exists_loop(self):
        self.tool._runtime_workspace = None
        self.tool._runtime_workspace_is_folder = None
        self.tool._run_scratch_gdb = None
        self.tool._run_id = "abcdef12"
        name = self.tool._unique_output_name(
            "Kaiteet", r"\\server\share\results.gdb"
        )
        self.assertEqual("Kaiteet_abcdef12", name)

    def test_removed_execution_checkboxes_are_not_in_toolbox(self):
        source = TOOLBOX.read_text(encoding="utf-8")
        for parameter_name in (
            "keep_scratch_on_error", "clip_cql_results", "benchmark_copy"
        ):
            self.assertNotIn(parameter_name, source)

    def test_fetch_json_records_network_read_and_parse_separately(self):
        class Response:
            status = 200
            headers = {"Content-Type": "application/json"}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"features": []}'

        original = MODULE.urllib.request.urlopen
        MODULE.urllib.request.urlopen = lambda request, timeout=60: Response()
        try:
            timings = MODULE.PhaseMetrics()
            data, raw, status, content_type = self.tool._fetch_json(
                "https://example.test/wfs", timings=timings
            )
        finally:
            MODULE.urllib.request.urlopen = original

        self.assertEqual({"features": []}, data)
        self.assertEqual(200, status)
        self.assertIn("application/json", content_type)
        for phase in (
            "requestin muodostaminen", "verkkopyyntö",
            "vastauksen lukeminen", "JSON-jäsennys",
        ):
            self.assertIsInstance(timings.get(phase), float)
            self.assertGreaterEqual(timings.get(phase), 0.0)

    def test_cql_rejection_does_not_fall_back_when_caller_will_split(self):
        self.tool.heavy_chunk_sources = ["Väylä"]
        self.tool._warn = lambda message: None
        self.tool._msg = lambda message: None
        self.tool._fetch_wfs_page = lambda **kwargs: (
            None, "request rejected", 414, "text/html"
        )
        with self.assertRaises(MODULE.CQLRequestRejected) as raised:
            self.tool._fetch_bbox_feature_chunks(
                "https://example.test/wfs", "digiroad:test", "1,2,3,4",
                ["application/json"], 5000, boundary_wkt="POLYGON ((0 0, 1 0, 0 0))",
                source_name="Väylä", allow_bbox_fallback=False,
            )
        self.assertEqual("CQL", raised.exception.stats["mode"])

    def test_bbox_page_stats_are_kept_but_default_log_is_concise(self):
        self.tool.heavy_chunk_sources = ["Väylä"]
        self.tool._msg_lines = []
        self.tool._msg = self.tool._msg_lines.append
        self.tool._warn = lambda message: None
        self.tool._fetch_wfs_page = lambda **kwargs: (
            {"features": [{"type": "Feature", "geometry": None, "properties": {}}]},
            '{"features":[{}]}', 200, "application/json",
        )
        conversion = MODULE.PhaseMetrics()
        conversion.set("JSONToFeatures", 0.125)
        self.tool._json_to_temp_fc = lambda raw: ("temporary_fc", conversion)
        chunks, found, requests, stats, cql_ok = self.tool._fetch_bbox_feature_chunks(
            "https://example.test/wfs", "other:test", "1,2,3,4",
            ["application/json"], 5000, source_name="Liiteri",
        )
        self.assertEqual(["temporary_fc"], chunks)
        self.assertEqual(1, found)
        self.assertEqual(1, requests)
        self.assertFalse(cql_ok)
        self.assertAlmostEqual(0.125, stats["json_to_features_s"])
        self.assertFalse(any("sivu yhteensä" in line for line in self.tool._msg_lines))

    def test_describe_feature_type_finds_geometry_and_sort_candidate(self):
        schema = b'''<?xml version="1.0"?>
        <xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema"
                    xmlns:gml="http://www.opengis.net/gml/3.2">
          <xsd:element name="objectid" type="xsd:int"/>
          <xsd:element name="geom" type="gml:MultiSurfacePropertyType"/>
        </xsd:schema>'''

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return schema

        original = MODULE.urllib.request.urlopen
        MODULE.urllib.request.urlopen = lambda request, timeout=30: Response()
        try:
            self.tool._discover_wfs_schema(
                "https://example.test/wfs", "test:polygon"
            )
        finally:
            MODULE.urllib.request.urlopen = original

        self.assertEqual("geom", self.tool._get_wfs_geometry_field("test:polygon"))
        self.assertEqual("objectid", self.tool._wfs_sort_candidate_cache["test:polygon"])

    def test_explicit_sort_is_added_for_wfs_without_primary_key(self):
        self.tool._wfs_sort_field_cache["liiteri:test"] = "objectid"
        url = self.tool._build_wfs_getfeature_url(
            "https://example.test/wfs", "liiteri:test", 5000, 5000,
            "application/json", bbox_str="1,2,3,4", geometry_only=False,
        )
        self.assertIn("sortBy=objectid", url)

    def test_kapsi_retries_incomplete_jpeg(self):
        class Response:
            headers = {"Content-Type": "image/jpeg"}

            def __init__(self, fail=False):
                self.fail = fail

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                if self.fail:
                    raise MODULE.http.client.IncompleteRead(b"\xff\xd8partial")
                return b"\xff\xd8complete\xff\xd9"

        responses = iter([Response(True), Response(False)])
        original_open = MODULE.urllib.request.urlopen
        original_sleep = MODULE.time.sleep
        MODULE.urllib.request.urlopen = lambda request, timeout=180: next(responses)
        MODULE.time.sleep = lambda seconds: None
        try:
            raw = self.tool._download_kapsi_image_bytes("https://example.test/map")
        finally:
            MODULE.urllib.request.urlopen = original_open
            MODULE.time.sleep = original_sleep
        self.assertEqual(b"\xff\xd8complete\xff\xd9", raw)

    def test_osm_unknown_geojson_crs_is_defined_as_wgs84(self):
        original_describe = getattr(MODULE.arcpy, "Describe", None)
        original_management = getattr(MODULE.arcpy, "management", None)
        calls = []

        class UnknownSpatialReference:
            factoryCode = 0

        MODULE.arcpy.Describe = lambda path: types.SimpleNamespace(
            spatialReference=UnknownSpatialReference()
        )
        MODULE.arcpy.SpatialReference = lambda code: code
        MODULE.arcpy.management = types.SimpleNamespace(
            DefineProjection=lambda path, sr: calls.append((path, sr))
        )
        try:
            defined = self.tool._define_osm_source_projection("osm_fc")
        finally:
            if original_describe is None:
                del MODULE.arcpy.Describe
            else:
                MODULE.arcpy.Describe = original_describe
            if original_management is None:
                del MODULE.arcpy.management
            else:
                MODULE.arcpy.management = original_management

        self.assertTrue(defined)
        self.assertEqual([("osm_fc", 4326)], calls)


if __name__ == "__main__":
    unittest.main()
