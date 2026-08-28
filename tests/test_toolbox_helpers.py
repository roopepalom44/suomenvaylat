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

    def test_bbox_page_stats_include_conversion_and_page_total_log(self):
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
        self.assertTrue(any("sivu yhteensä" in line for line in self.tool._msg_lines))


if __name__ == "__main__":
    unittest.main()
