"""Pitkä CQL siirtyy POSTiin ennen verkkopyyntöä ja säilyy sivutuksessa."""

import unittest
import urllib.parse
from unittest.mock import patch

import fake_http
from test_toolbox_helpers import MODULE


class CqlPostTests(unittest.TestCase):
    ENDPOINT = "https://example.test/wfs"
    LAYER = "test:roads"
    SHORT_WKT = "POLYGON ((0 0, 1 0, 1 1, 0 0))"
    LONG_WKT = "POLYGON ((" + ", ".join("{} {}".format(i, i) for i in range(1000)) + ", 0 0))"

    def setUp(self):
        self.tool = MODULE.VaylaWFSDownloader.__new__(MODULE.VaylaWFSDownloader)
        self.tool._wfs_geometry_field_cache = {self.LAYER: "geometry"}
        self.tool._wfs_sort_candidate_cache = {}
        self.tool._wfs_sort_field_cache = {}
        self.tool._wfs_output_format_cache = {}
        self.tool._discover_wfs_schema = lambda *args: None
        self.tool._verbose_diagnostics = False
        self.tool._http_max_attempts = 1
        self.tool._page_workers = 1
        self.tool._json_batch_pages = 1
        self.tool.heavy_chunk_sources = ["Väylä"]
        self.messages, self.warnings, self.deleted = [], [], []
        self.tool._msg = self.messages.append
        self.tool._warn = self.warnings.append
        self.tool._safe_delete = self.deleted.append
        self.tool._pages_to_temp_fc = lambda pages: (["temporary_fc"], MODULE.PhaseMetrics())

    @staticmethod
    def _paged_response(total, page_size):
        def handler(url, method, headers, body):
            params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
            if method == "POST":
                params.update(urllib.parse.parse_qsl(body.decode("utf-8")))
            start = int(params.get("startIndex", 0))
            payload = fake_http.feature_page(min(page_size, max(0, total - start)), start)
            payload["numberMatched"] = total
            return fake_http.FakeResponse(payload)
        return handler

    def _fetch(self, wkt, allow_bbox_fallback=False):
        return self.tool._fetch_bbox_feature_chunks(
            self.ENDPOINT, self.LAYER, "0,0,1,1", ["application/json"], 2,
            boundary_wkt=wkt, source_name="Väylä", allow_bbox_fallback=allow_bbox_fallback,
        )

    def test_direct_long_page_uses_post_without_changing_filter(self):
        transport = fake_http.FakeTransport()
        self.tool._http_transport = transport
        cql = self.tool._build_cql_intersects("geometry", self.LONG_WKT)
        self.tool._fetch_wfs_page(
            self.ENDPOINT, self.LAYER, None, 2, 0, ["application/json"], cql_filter=cql,
        )
        self.assertEqual(["POST"], [r["method"] for r in transport.requests])
        self.assertEqual(cql, transport.requests[0]["form"]["CQL_FILTER"])
        self.assertNotIn("CQL_FILTER", transport.requests[0]["query"])

    def test_limit_uses_full_encoded_url_and_allows_exact_limit(self):
        cql = "name='ää ää'"
        url = self.tool._build_wfs_getfeature_url(
            self.ENDPOINT, self.LAYER, 2, 0, "application/json", cql_filter=cql,
            geometry_only=False,
        )
        for limit, expected in ((len(url), "GET"), (len(url) - 1, "POST")):
            with self.subTest(limit=limit), patch.object(MODULE, "WFS_CQL_GET_MAX_URL_LENGTH", limit):
                transport = fake_http.FakeTransport()
                self.tool._http_transport = transport
                self.tool._fetch_wfs_page(
                    self.ENDPOINT, self.LAYER, None, 2, 0, ["application/json"],
                    cql_filter=cql, geometry_only=False,
                )
                self.assertEqual(expected, transport.requests[0]["method"])

    def test_short_cql_still_uses_get(self):
        transport = fake_http.FakeTransport(handler=self._paged_response(5, 2))
        self.tool._http_transport = transport
        _, found, pages, stats, effective = self._fetch(self.SHORT_WKT)
        self.assertEqual((5, 3, "CQL", True), (found, pages, stats["mode"], effective))
        self.assertEqual(["GET"] * 3, [r["method"] for r in transport.requests])

    def test_long_serial_and_parallel_pages_all_use_post(self):
        for workers in (1, 3):
            with self.subTest(workers=workers):
                self.tool._page_workers = workers
                transport = fake_http.FakeTransport(handler=self._paged_response(7, 2))
                self.tool._http_transport = transport
                _, found, pages, stats, effective = self._fetch(self.LONG_WKT)
                self.assertEqual((7, 4, "CQL_POST", True), (found, pages, stats["mode"], effective))
                self.assertEqual(["POST"] * 4, [r["method"] for r in transport.requests])
                self.assertEqual([0, 2, 4, 6], sorted(transport.start_indexes()))
                self.assertTrue(all(r["form"]["CQL_FILTER"].endswith(self.LONG_WKT + ")") for r in transport.requests))
                self.assertFalse(any("GET oli liian pitkä" in msg for msg in self.messages))

    def test_post_after_get_rejection_is_kept_for_serial_and_parallel_pages(self):
        for workers in (1, 3):
            with self.subTest(workers=workers):
                self.tool._page_workers = workers
                paged = self._paged_response(7, 2)
                transport = fake_http.FakeTransport(handler=lambda url, method, headers, body:
                    fake_http.FakeResponse("URI too long", status=414)
                    if method == "GET" else paged(url, method, headers, body))
                self.tool._http_transport = transport
                _, found, pages, stats, effective = self._fetch(self.SHORT_WKT)
                self.assertEqual((7, 4, "CQL_POST", True), (found, pages, stats["mode"], effective))
                self.assertEqual(["GET"] + ["POST"] * 4, [r["method"] for r in transport.requests])
                self.assertEqual([0, 0, 2, 4, 6], sorted(transport.start_indexes()))

    def test_rejected_long_post_is_not_sent_twice_before_split(self):
        transport = fake_http.FakeTransport([
            fake_http.FakeResponse("CQL rejected", status=403),
        ])
        self.tool._http_transport = transport
        with self.assertRaises(MODULE.CQLRequestRejected):
            self._fetch(self.LONG_WKT)
        self.assertEqual(["POST"], [r["method"] for r in transport.requests])

    def test_long_post_rejection_preserves_bbox_fallback(self):
        def handler(url, method, headers, body):
            params = dict(urllib.parse.parse_qsl(body.decode("utf-8"))) if body else {}
            if "CQL_FILTER" in params:
                return fake_http.FakeResponse("CQL rejected", status=403)
            return fake_http.FakeResponse(fake_http.feature_page(1))
        transport = fake_http.FakeTransport(handler=handler)
        self.tool._http_transport = transport
        _, found, _, stats, effective = self._fetch(self.LONG_WKT, allow_bbox_fallback=True)
        self.assertEqual((1, "BBOX", False), (found, stats["mode"], effective))
        self.assertEqual(["POST", "GET"], [r["method"] for r in transport.requests])
        self.assertNotIn("CQL_FILTER", transport.requests[-1]["query"])


if __name__ == "__main__":
    unittest.main()
