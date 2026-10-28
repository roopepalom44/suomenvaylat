"""OSM outages must finish, cancellation must escape fallback, and catalogs stay out of downloads."""
import concurrent.futures
import gzip
import io
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch

from test_toolbox_helpers import MODULE
from test_audit_fixes import _ArcpyPatch


class Response(io.BytesIO):
    def __init__(self, body, **headers):
        super().__init__(body)
        self.headers = headers


class SelectedDefinitionsTests(unittest.TestCase):
    def setUp(self):
        self.tool = MODULE.VaylaWFSDownloader()
        self.tool._read_layer_disk_cache = Mock(return_value=None)
        self.tool._fetch_layer_list = Mock()
        self.school = {"source": "OpenStreetMap", "id": "osm_schools", "kind": "osm"}

    def test_existing_definition_never_reloads_even_partial_syke_catalog(self):
        self.tool._layer_mapping = {"Koulut - OpenStreetMap": self.school}
        self.tool.wfs_registry._capabilities_errors = {"Syke": ["disabled"]}
        self.tool._ensure_selected_layer_definitions(["Koulut - OpenStreetMap"], ["Syke", "OpenStreetMap"])
        self.tool._fetch_layer_list.assert_not_called()
        self.tool._read_layer_disk_cache.assert_not_called()

    def test_selection_from_an_earlier_source_filter_reuses_memory(self):
        self.tool._layer_mapping_cache = {"old": {"Koulut - OpenStreetMap": self.school}}
        self.tool._ensure_selected_layer_definitions(["Koulut - OpenStreetMap"], ["Syke"])
        self.assertEqual(self.school, self.tool._lookup_layer_info("Koulut - OpenStreetMap"))
        self.tool._fetch_layer_list.assert_not_called()

    def test_new_gp_process_reuses_original_combined_disk_cache(self):
        self.tool._read_layer_disk_cache.return_value = ([], {"Koulut - OpenStreetMap": self.school})
        self.tool._ensure_selected_layer_definitions(["Koulut - OpenStreetMap"], ["Syke", "OpenStreetMap"])
        self.assertEqual(self.school, self.tool._lookup_layer_info("Koulut - OpenStreetMap"))
        self.tool._fetch_layer_list.assert_not_called()

    def test_only_missing_selected_sources_are_loaded_and_known_definitions_survive(self):
        self.tool._layer_mapping = {"Koulut - OpenStreetMap": self.school}
        def fetch(sources, **kwargs):
            self.assertEqual(["Liiteri"], sources)
            self.tool._layer_mapping = {"Yläasteet - Liiteri": {"source": "Liiteri", "id": "schools"}}
        self.tool._fetch_layer_list.side_effect = fetch
        self.tool._ensure_selected_layer_definitions(
            ["Koulut - OpenStreetMap", "Yläasteet - Liiteri"], ["Aino", "Syke", "Väylä"])
        self.assertEqual(self.school, self.tool._lookup_layer_info("Koulut - OpenStreetMap"))
        self.assertEqual("schools", self.tool._lookup_layer_info("Yläasteet - Liiteri")["id"])

    def test_partial_catalog_has_one_source_parameter_summary_without_endpoint_warnings(self):
        params = [types.SimpleNamespace(valueAsText="", values=None,
                                       setErrorMessage=Mock(), clearMessage=Mock(), setWarningMessage=Mock())
                  for _ in range(13)]
        params[0].values = [["Syke"], ["OpenStreetMap"]]
        params[2].values = ["Koulut - OpenStreetMap"]
        params[2].valueAsText = "Koulut - OpenStreetMap"
        params[3].valueAsText = "Oma aineisto (Polygon/Polyline)"
        params[5].valueAsText = "boundary"
        self.tool._layer_mapping = {"Koulut - OpenStreetMap": self.school}
        self.tool.wfs_registry._capabilities_errors = {"Syke": ["https://unused.test/wfs: disabled"] * 5}
        self.tool._warn = Mock()
        self.tool.updateMessages(params)
        params[0].setWarningMessage.assert_called_once()
        message = params[0].setWarningMessage.call_args.args[0]
        self.assertIn("Syke: 5 palvelua", message)
        self.assertNotIn("unused.test", message)
        self.tool._warn.assert_not_called()


class OsmWaitTests(unittest.TestCase):
    def setUp(self):
        self.tool = MODULE.VaylaWFSDownloader()
        self.messages = []
        self.tool._msg = self.messages.append
        self.tool.wfs_registry.sources["OpenStreetMap"]["endpoints"] = [
            "https://first.test/api", "https://second.test/api"]
        self.request = MODULE.urllib.request.Request("https://first.test/api")

    def test_stuck_connection_has_a_wall_clock_deadline(self):
        release, entered, finished = threading.Event(), threading.Event(), threading.Event()
        def stuck(*args, **kwargs):
            entered.set()
            release.wait(2)
            finished.set()
            raise OSError("released")
        try:
            with patch.object(MODULE, "_urlopen", side_effect=stuck):
                start = time.monotonic()
                with self.assertRaises(TimeoutError):
                    self.tool._read_overpass_response(self.request, 0.04)
                self.assertLess(time.monotonic() - start, 0.8)
                self.assertTrue(entered.is_set())
        finally:
            release.set()
            self.assertTrue(finished.wait(1))

    def test_slow_download_deadline_includes_reading_the_body(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        class StuckBody(Response):
            def read1(self, size):
                entered.set()
                release.wait(2)
                finished.set()
                return b""
        try:
            with patch.object(MODULE, "_urlopen", return_value=StuckBody(b"")):
                with self.assertRaises(TimeoutError):
                    self.tool._read_overpass_response(self.request, 0.04)
                self.assertTrue(entered.is_set())
        finally:
            release.set()
            self.assertTrue(finished.wait(1))

    def test_wait_reports_elapsed_time_on_the_calling_thread(self):
        future = Mock()
        future.result.side_effect = [concurrent.futures.TimeoutError, {"elements": []}]
        future.done.return_value = False
        with patch.object(MODULE.concurrent.futures, "Future", return_value=future), \
                patch.object(MODULE.threading, "Thread"), \
                patch.object(MODULE.time, "monotonic", side_effect=[0, 0, 10, 10]):
            self.assertEqual({"elements": []}, self.tool._read_overpass_response(self.request, 60))
        self.assertTrue(any("10 s" in message and "50 s" in message for message in self.messages))

    def test_cancel_escapes_layer_and_grid_fallback_handlers(self):
        self.tool._read_overpass_response = Mock()
        with _ArcpyPatch(env=types.SimpleNamespace(isCancelled=True)):
            with self.assertRaises(KeyboardInterrupt):
                self.tool._fetch_overpass_json("query")
        self.tool._read_overpass_response.assert_not_called()

    def test_cancel_during_wait_never_waits_for_the_worker_to_finish(self):
        future = Mock()
        future.result.side_effect = concurrent.futures.TimeoutError
        future.done.return_value = False
        with patch.object(MODULE.concurrent.futures, "Future", return_value=future), \
                patch.object(MODULE.threading, "Thread"), \
                patch.object(self.tool, "_check_overpass_cancelled", side_effect=[None, KeyboardInterrupt]):
            with self.assertRaises(KeyboardInterrupt):
                self.tool._read_overpass_response(self.request, 60)
        self.assertEqual(1, future.result.call_count)

    def test_total_layer_deadline_stops_all_further_endpoints_and_grids(self):
        self.tool._read_overpass_response = Mock()
        with self.assertRaises(MODULE.OverpassRequestError) as caught:
            self.tool._fetch_overpass_json("query", deadline=time.monotonic() - 1)
        self.assertFalse(caught.exception.retry_grid)
        self.tool._read_overpass_response.assert_not_called()

    def test_endpoint_uses_only_remaining_layer_time(self):
        self.tool._read_overpass_response = Mock(return_value={"elements": []})
        self.tool._fetch_overpass_json("query", deadline=time.monotonic() + 5)
        timeout = self.tool._read_overpass_response.call_args.args[1]
        self.assertGreater(timeout, 0)
        self.assertLessEqual(timeout, 5)

    def test_unreachable_servers_are_tried_once_without_grid_retry(self):
        self.tool._extent_bbox_4326 = lambda fc: "63,26,64,28"
        self.tool._read_overpass_response = Mock(side_effect=OSError("offline"))
        with _ArcpyPatch(Describe=lambda fc: types.SimpleNamespace(spatialReference=None)):
            with self.assertRaises(MODULE.OverpassRequestError) as caught:
                self.tool._fetch_osm_feature_chunks("osm_schools", "boundary")
        self.assertFalse(caught.exception.retry_grid)
        self.assertEqual(2, self.tool._read_overpass_response.call_count)
        self.assertTrue(any("varapalvelinta" in message for message in self.messages))

    def test_query_timeout_remark_allows_smaller_grid_and_rejects_partial_elements(self):
        self.tool._read_overpass_response = Mock(return_value={
            "remark": "runtime error: Query timed out", "elements": [{"type": "node", "id": 1}]})
        with self.assertRaises(MODULE.OverpassRequestError) as caught:
            self.tool._fetch_overpass_json("query")
        self.assertTrue(caught.exception.retry_grid)

    def test_compressed_response_is_decoded(self):
        response = Response(gzip.compress(b'{"elements": []}'), **{"Content-Encoding": "gzip"})
        with patch.object(MODULE, "_urlopen", return_value=response):
            self.assertEqual({"elements": []}, self.tool._read_overpass_response(self.request, 1))

    def test_oversized_response_is_rejected(self):
        with patch.object(MODULE.service_network, "MAX_RESPONSE_BYTES", 10), \
                patch.object(MODULE, "_urlopen", return_value=Response(b'{"elements": []}')):
            with self.assertRaisesRegex(ValueError, "kokorajan"):
                self.tool._read_overpass_response(self.request, 1)

    def test_no_arcpy_calls_happen_in_network_thread(self):
        caller = threading.get_ident()
        self.tool._check_overpass_cancelled = lambda: self.assertEqual(caller, threading.get_ident())
        self.tool._msg = lambda msg: self.assertEqual(caller, threading.get_ident())
        calls = []
        def respond(*args, **kwargs):
            calls.append(threading.get_ident())
            return Response(b'{"elements": []}')
        with patch.object(MODULE, "_urlopen", side_effect=respond):
            self.tool._fetch_overpass_json("query")
        self.assertNotEqual([caller], calls)


if __name__ == "__main__":
    unittest.main()
