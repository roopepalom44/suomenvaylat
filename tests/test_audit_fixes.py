"""Regressiotestit katselmoinnin P0/P1-korjauksille (ei vaadi arcpyä)."""

import pathlib
import shutil
import ssl
import sys
import tempfile
import threading
import types
import unittest
import urllib.error

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import fake_http  # noqa: E402
from test_toolbox_helpers import MODULE  # noqa: E402


def _new_tool():
    tool = MODULE.VaylaWFSDownloader.__new__(MODULE.VaylaWFSDownloader)
    tool._wfs_geometry_field_cache = {}
    tool._wfs_sort_candidate_cache = {}
    tool._wfs_sort_field_cache = {}
    tool._wfs_output_format_cache = {}
    tool._verbose_diagnostics = False
    tool._runtime_workspace = None
    tool._runtime_workspace_is_folder = None
    tool._run_scratch_gdb = None
    tool._run_scratch_folder = None
    tool._run_id = "abcdef12"
    tool._workspace_kind_cache = {}
    return tool


class _ArcpyPatch(object):
    """Aseta arcpy-moduulin attribuutteja testin ajaksi."""

    def __init__(self, **attrs):
        self.attrs = attrs
        self.saved = {}

    def __enter__(self):
        for name, value in self.attrs.items():
            self.saved[name] = getattr(MODULE.arcpy, name, _ArcpyPatch)
            setattr(MODULE.arcpy, name, value)
        return self

    def __exit__(self, *exc):
        for name, value in self.saved.items():
            if value is _ArcpyPatch:
                delattr(MODULE.arcpy, name)
            else:
                setattr(MODULE.arcpy, name, value)


class OutputNameReservationTests(unittest.TestCase):
    def setUp(self):
        self.tool = _new_tool()
        self.tool._reserved_output_names = set()
        self.tool._validated_name = lambda raw, ws: raw.replace(" ", "_")
        self.tool._is_filesystem_workspace = lambda ws: False

    def test_same_title_from_two_sources_gets_distinct_local_names(self):
        with _ArcpyPatch(Exists=lambda path: False):
            first = self.tool._unique_output_name("Tiet", r"C:\data\out.gdb")
            second = self.tool._unique_output_name("Tiet", r"C:\data\out.gdb")
            third = self.tool._unique_output_name("TIET", r"C:\data\out.gdb")
        self.assertEqual("Tiet", first)
        self.assertEqual("Tiet_1", second)
        self.assertEqual("TIET_2", third)

    def test_existing_dataset_is_still_skipped(self):
        existing = {r"C:\data\out.gdb/Tiet".replace("/", "\\")}
        self.tool._dataset_output_path = lambda ws, name: ws + "\\" + name
        with _ArcpyPatch(Exists=lambda path: path in existing):
            self.assertEqual("Tiet_1", self.tool._unique_output_name("Tiet", r"C:\data\out.gdb"))

    def test_remote_workspace_names_do_not_collide_within_run(self):
        workspace = r"\\server\share\results.gdb"
        first = self.tool._unique_output_name("Kaiteet", workspace)
        second = self.tool._unique_output_name("Kaiteet", workspace)
        self.assertEqual("Kaiteet_abcdef12", first)
        self.assertEqual("Kaiteet_2_abcdef12", second)

    def test_released_name_can_be_reused(self):
        workspace = r"\\server\share\results.gdb"
        name = self.tool._unique_output_name("Kaiteet", workspace)
        self.tool._release_output_name(name, workspace)
        self.assertEqual(name, self.tool._unique_output_name("Kaiteet", workspace))


class OutputWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tool = _new_tool()

    def _mp(self, default_gdb=None, fail=False):
        def project(name):
            if fail:
                raise RuntimeError("no CURRENT project")
            return types.SimpleNamespace(defaultGeodatabase=default_gdb)
        return types.SimpleNamespace(ArcGISProject=project)

    def test_explicit_workspace_is_kept(self):
        self.assertEqual(r"C:\out.gdb", self.tool._resolve_output_workspace(r"C:\out.gdb"))

    def test_project_default_gdb_is_used(self):
        with _ArcpyPatch(mp=self._mp(r"C:\proj\default.gdb")):
            self.assertEqual(r"C:\proj\default.gdb", self.tool._resolve_output_workspace(""))

    def test_missing_project_is_an_error_not_scratch(self):
        with _ArcpyPatch(mp=self._mp(fail=True)):
            with self.assertRaises(Exception):
                self.tool._resolve_output_workspace("")
        with _ArcpyPatch(mp=self._mp("")):
            with self.assertRaises(Exception):
                self.tool._resolve_output_workspace(None)

    def test_run_scratch_is_rejected(self):
        self.tool._run_scratch_folder = "/tmp/suomenvaylat_run"
        with self.assertRaises(Exception):
            self.tool._resolve_output_workspace("/tmp/suomenvaylat_run/scratch.gdb")


class GeometryGroupingTests(unittest.TestCase):
    def test_feature_classes_are_grouped_by_shape_type(self):
        tool = _new_tool()
        shapes = {"a": "Polygon", "b": "Point", "c": "Polyline", "d": "Polygon"}
        with _ArcpyPatch(Describe=lambda fc: types.SimpleNamespace(shapeType=shapes[fc])):
            groups = tool._group_feature_classes_by_shape(["a", "b", "c", "d"])
        self.assertEqual(
            [("Point", ["b"]), ("Polyline", ["c"]), ("Polygon", ["a", "d"])], groups
        )
        self.assertEqual("alueet", tool._shape_type_suffix("Polygon"))
        self.assertEqual("viivat", tool._shape_type_suffix("Polyline"))
        self.assertEqual("pisteet", tool._shape_type_suffix("Point"))


class BoundaryUnionTests(unittest.TestCase):
    def test_union_failure_uses_dissolve_instead_of_first_geometry(self):
        tool = _new_tool()
        tool._scratch_gdb = lambda: "scratch.gdb"
        tool._safe_delete = lambda path: None

        class Geometry(object):
            def __init__(self, name):
                self.name = name

            def union(self, other):
                raise RuntimeError("topology error")

        rows = {"boundary": [(Geometry("a"),), (Geometry("b"),)],
                "dissolved": [(Geometry("a+b"),)]}
        dissolve_calls = []

        class Cursor(object):
            def __init__(self, fc, fields):
                key = "dissolved" if "boundary_dissolve" in fc else "boundary"
                self.rows = rows[key]

            def __enter__(self):
                return iter(self.rows)

            def __exit__(self, *exc):
                return False

        with _ArcpyPatch(
            da=types.SimpleNamespace(SearchCursor=Cursor),
            management=types.SimpleNamespace(
                Dissolve=lambda src, dst, multi_part=None: dissolve_calls.append((src, dst))
            ),
        ):
            merged, count = tool._merged_boundary_geometry("boundary")
        self.assertEqual("a+b", merged.name)
        self.assertEqual(2, count)
        self.assertEqual(1, len(dissolve_calls))


class PaginationCompletenessTests(unittest.TestCase):
    def setUp(self):
        self.tool = _new_tool()
        self.tool._http_max_attempts = 1
        self.tool._page_workers = 1
        self.tool._json_batch_pages = 1
        self.warnings = []
        self.tool._msg = lambda text: None
        self.tool._warn = self.warnings.append
        self.tool.heavy_chunk_sources = []

        def fake_pages_to_fc(pages, project_to_epsg=None):
            return ["fc"], MODULE.PhaseMetrics()

        self.tool._pages_to_temp_fc = fake_pages_to_fc

    def _fetch(self, total, max_requests, number_matched=False):
        def handler(url, method, headers, body):
            import urllib.parse as up
            query = dict(up.parse_qsl(up.urlsplit(url).query))
            start = int(query.get("startIndex", 0))
            payload = fake_http.feature_page(min(100, max(0, total - start)), start)
            if number_matched:
                payload["numberMatched"] = total
            return fake_http.FakeResponse(payload)

        self.tool._http_transport = fake_http.FakeTransport(handler=handler)
        return self.tool._fetch_bbox_feature_chunks(
            "https://example.test/wfs", "other:test", "1,2,3,4",
            ["application/json"], 100, max_requests=max_requests, source_name="Liiteri",
        )

    def test_partial_last_page_at_limit_is_complete(self):
        _, found, requests, stats, _ = self._fetch(total=350, max_requests=4)
        self.assertEqual(350, found)
        self.assertEqual(4, requests)
        self.assertFalse(stats["truncated"])

    def test_full_last_page_at_limit_is_truncated_without_total(self):
        _, found, _, stats, _ = self._fetch(total=400, max_requests=4)
        self.assertEqual(400, found)
        self.assertTrue(stats["truncated"])

    def test_full_last_page_matching_number_matched_is_complete(self):
        _, found, _, stats, _ = self._fetch(total=400, max_requests=4, number_matched=True)
        self.assertEqual(400, found)
        self.assertFalse(stats["truncated"])

    def test_repeated_pages_mark_layer_truncated(self):
        self.tool._http_transport = fake_http.FakeTransport(
            handler=lambda url, method, headers, body: fake_http.FakeResponse(
                fake_http.feature_page(100, 0)
            )
        )
        _, _, _, stats, _ = self.tool._fetch_bbox_feature_chunks(
            "https://example.test/wfs", "other:test", "1,2,3,4",
            ["application/json"], 100, max_requests=10, source_name="Liiteri",
        )
        self.assertTrue(stats["truncated"])

    def test_ogc_revisited_next_link_is_truncated(self):
        page = {"features": [{"type": "Feature", "geometry": None, "properties": {}}],
                "links": [{"rel": "next", "href": "https://example.test/collections/c/items?limit=1&bbox=1"}]}
        self.tool._http_transport = fake_http.FakeTransport(
            handler=lambda url, method, headers, body: fake_http.FakeResponse(page)
        )
        # Ensimmäinen URL on muodossa ...items?limit=1&bbox=1, joten next-linkki
        # osoittaa jo haettuun sivuun.
        _, _, stats = self.tool._fetch_ogcapi_feature_chunks(
            "https://example.test", "c", "1", 1,
        )
        self.assertTrue(stats["truncated"])

    def test_ogc_last_page_without_next_is_complete(self):
        self.tool._http_transport = fake_http.FakeTransport([
            fake_http.FakeResponse({"features": [{"type": "Feature"}], "links": []}),
        ])
        _, found, stats = self.tool._fetch_ogcapi_feature_chunks(
            "https://example.test", "c", "1", 10, max_requests=1,
        )
        self.assertEqual(1, found)
        self.assertFalse(stats["truncated"])


class GeoJsonGeometrySplitTests(unittest.TestCase):
    """JSONToFeatures tekee GeometryCollectionista taulun ja pudottaa sekatyypit."""

    SQUARE = [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]

    @staticmethod
    def _feature(geometry, **props):
        return {"type": "Feature", "geometry": geometry, "properties": props}

    def test_single_type_uses_fast_path(self):
        split = MODULE.VaylaWFSDownloader._split_features_by_geometry([
            self._feature({"type": "Polygon", "coordinates": self.SQUARE}),
            self._feature({"type": "MultiPolygon", "coordinates": [self.SQUARE]}),
            self._feature(None),
        ])
        self.assertIsNone(split)

    def test_polygon_geometry_collection_becomes_multipolygon(self):
        # Traficomin avoin:tuotejako_satamakartat palauttaa tällaisia kohteita.
        feature = self._feature({"type": "GeometryCollection", "geometries": [
            {"type": "Polygon", "coordinates": self.SQUARE},
            {"type": "Polygon", "coordinates": self.SQUARE},
        ]}, name="Vaasa")
        split = MODULE.VaylaWFSDownloader._split_features_by_geometry([feature])
        self.assertEqual(1, len(split))
        family, features = split[0]
        self.assertEqual("POLYGON", family)
        self.assertEqual("MultiPolygon", features[0]["geometry"]["type"])
        self.assertEqual(2, len(features[0]["geometry"]["coordinates"]))
        self.assertEqual({"name": "Vaasa"}, features[0]["properties"])

    def test_mixed_collection_and_types_are_split_by_family(self):
        split = MODULE.VaylaWFSDownloader._split_features_by_geometry([
            self._feature({"type": "Point", "coordinates": [1, 2]}),
            self._feature({"type": "GeometryCollection", "geometries": [
                {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
                {"type": "Polygon", "coordinates": self.SQUARE},
            ]}),
            self._feature(None),
        ])
        families = {family: len(features) for family, features in split}
        # Geometriaton kohde liitetään suurimpaan ryhmään (tasatilanteessa ensimmäiseen).
        self.assertEqual({"POINT": 2, "POLYLINE": 1, "POLYGON": 1}, families)

    def test_pages_are_converted_once_per_geometry_type(self):
        tool = _new_tool()
        calls = []

        def fake_json_to_temp_fc(raw_text, project_to_epsg=None, geometry_type=None):
            calls.append(geometry_type)
            return "fc_{}".format(len(calls)), MODULE.PhaseMetrics()

        tool._json_to_temp_fc = fake_json_to_temp_fc
        page = {"type": "FeatureCollection", "features": [
            self._feature({"type": "Point", "coordinates": [1, 2]}),
            self._feature({"type": "Polygon", "coordinates": self.SQUARE}),
        ]}
        fcs, _ = tool._pages_to_temp_fc([page])
        self.assertEqual(["POINT", "POLYGON"], calls)
        self.assertEqual(["fc_1", "fc_2"], fcs)

        calls.clear()
        fcs, _ = tool._pages_to_temp_fc([{"type": "FeatureCollection", "features": [
            self._feature({"type": "Point", "coordinates": [1, 2]}),
        ]}])
        self.assertEqual([None], calls)
        self.assertEqual(["fc_1"], fcs)


class PageSizeTests(unittest.TestCase):
    def _batch_sizes(self, grid_levels, fail_until):
        sizes = []

        def fetch(bbox, batch_size):
            sizes.append(batch_size)
            if len(sizes) <= fail_until:
                raise RuntimeError("liian iso pyyntö")
            return [], 0

        strategy = MODULE.ResilienceStrategy(max_batch_size=5000, grid_levels=grid_levels)
        strategy.execute_with_fallback(fetch, "0,0,10,10")
        return sizes

    def test_whole_area_uses_full_page_size(self):
        self.assertEqual([5000], self._batch_sizes([1, 2, 4], fail_until=0))

    def test_finer_grids_still_shrink_page_size(self):
        # Ruudukko 2x2 = 4 ruutua, 5000 / (2 * 2) = 1250.
        self.assertEqual([5000, 1250, 1250, 1250, 1250], self._batch_sizes([1, 2], fail_until=1))


class Tm35finLabelTests(unittest.TestCase):
    @staticmethod
    def _sr(factory_code, central_meridian=27.0, sr_type="Projected"):
        return types.SimpleNamespace(
            type=sr_type, factoryCode=factory_code, centralMeridian=central_meridian,
            falseEasting=500000.0, falseNorthing=0.0, scaleFactor=0.9996, metersPerUnit=1.0,
        )

    def test_gdal_etrs89_tm35fin_is_relabelled(self):
        self.assertTrue(MODULE.VaylaWFSDownloader._is_unlabelled_tm35fin(self._sr(0)))

    def test_epsg3067_and_other_projections_are_left_alone(self):
        check = MODULE.VaylaWFSDownloader._is_unlabelled_tm35fin
        self.assertFalse(check(self._sr(3067)))
        self.assertFalse(check(self._sr(0, central_meridian=21.0)))
        self.assertFalse(check(self._sr(0, sr_type="Geographic")))
        self.assertFalse(check(None))


class ServerErrorBodyTests(unittest.TestCase):
    """HTTP-virheen JSON-runko ei saa näyttää tyhjältä kohdejoukolta."""

    GEOSERVER_500 = {"code": "NoApplicableCode", "description": "Internal server error"}

    def setUp(self):
        self.tool = _new_tool()
        self.tool._http_max_attempts = 1
        self.tool._page_workers = 1
        self.tool._msg = lambda text: None
        self.tool._warn = lambda text: None
        self.scratch = tempfile.mkdtemp()
        self.tool._run_scratch_folder = self.scratch
        self.addCleanup(shutil.rmtree, self.scratch, True)

    def test_json_error_body_with_http_500_is_not_data(self):
        self.tool._http_transport = fake_http.FakeTransport([
            fake_http.FakeResponse(self.GEOSERVER_500, status=500),
        ])
        data, raw_text, status, _ = self.tool._fetch_json("https://example.test/wfs")
        self.assertIsNone(data)
        self.assertEqual(500, status)
        self.assertIn("NoApplicableCode", raw_text)

    def test_ogc_style_error_body_with_http_200_is_not_data(self):
        self.tool._http_transport = fake_http.FakeTransport([
            fake_http.FakeResponse(self.GEOSERVER_500, status=200),
        ])
        data, _, _, _ = self.tool._fetch_json("https://example.test/wfs")
        self.assertIsNone(data)

    def test_wfs_page_requests_allow_slow_cql_pages(self):
        # DigiRoadin raskas CQL-sivu palautuu vasta ~50-90 s kuluttua.
        transport = fake_http.FakeTransport()
        self.tool._http_transport = transport
        for prefer_post in (False, True):
            self.tool._fetch_wfs_page(
                "https://example.test/wfs", "other:test", None, 100, 0,
                ["application/json"], cql_filter="INTERSECTS(geom, POINT(1 2))",
                prefer_post=prefer_post,
            )
        self.assertEqual(["GET", "POST"], [r["method"] for r in transport.requests])
        for request in transport.requests:
            self.assertGreaterEqual(request["timeout"], MODULE.WFS_PAGE_TIMEOUT_S)
            self.assertGreaterEqual(request["timeout"], 180)

    def test_format_independent_errors_are_not_repeated_per_output_format(self):
        formats = ["application/json", "application/geo+json", "json"]
        for status in (414, 500, 403):
            transport = fake_http.FakeTransport(
                handler=lambda url, method, headers, body, status=status: fake_http.FakeResponse(
                    self.GEOSERVER_500, status=status
                )
            )
            self.tool._http_transport = transport
            data, _, got_status, _ = self.tool._fetch_wfs_page(
                "https://example.test/wfs", "other:test", None, 100, 0, formats,
                cql_filter="INTERSECTS(geom, POINT(1 2))",
            )
            self.assertIsNone(data)
            self.assertEqual(status, got_status)
            self.assertEqual(1, transport.call_count, status)

    def test_bad_request_still_tries_other_output_formats(self):
        transport = fake_http.FakeTransport([
            fake_http.FakeResponse("<ExceptionReport/>", status=400, content_type="text/xml"),
            fake_http.FakeResponse(fake_http.feature_page(1)),
        ])
        self.tool._http_transport = transport
        data, _, _, _ = self.tool._fetch_wfs_page(
            "https://example.test/wfs", "other:test", None, 100, 0,
            ["application/json", "json"], cql_filter="INTERSECTS(geom, POINT(1 2))",
        )
        self.assertEqual(1, len(data["features"]))
        self.assertEqual(2, transport.call_count)

    def test_wfs_server_error_fails_layer_instead_of_empty_result(self):
        self.tool._http_transport = fake_http.FakeTransport(
            handler=lambda url, method, headers, body: fake_http.FakeResponse(
                self.GEOSERVER_500, status=500
            )
        )
        with self.assertRaisesRegex(Exception, "HTTP 500"):
            self.tool._fetch_bbox_feature_chunks(
                "https://example.test/wfs", "other:test", "1,2,3,4",
                ["application/json"], 100, max_requests=5, source_name="Liiteri",
            )


class RedirectSecurityTests(unittest.TestCase):
    def test_credentials_are_dropped_on_cross_origin_redirect(self):
        headers = {"Authorization": "Basic secret", "Cookie": "a=b", "Accept": "x"}
        same = MODULE._redirect_headers(
            "https://api.example/a", "https://api.example:443/b", headers
        )
        other = MODULE._redirect_headers("https://api.example/a", "https://evil.example/b", headers)
        self.assertEqual(headers, same)
        self.assertEqual({"Accept": "x"}, other)

    def test_https_downgrade_is_refused(self):
        with self.assertRaises(urllib.error.URLError):
            MODULE._redirect_headers("https://api.example/a", "http://api.example/a", {})

    def test_transport_redirect_strips_authorization(self):
        transport = MODULE.HttpTransport()
        calls = []

        def single(url, method, headers, body, timeout):
            calls.append((url, dict(headers)))
            if len(calls) == 1:
                return 302, {}, b"", "https://other.example/x", 0.0
            return 200, {}, b"{}", None, 0.0

        transport._single_request = single
        transport.request("https://api.example/x", headers={"Authorization": "Basic k"})
        self.assertIn("Authorization", calls[0][1])
        self.assertNotIn("Authorization", calls[1][1])

    def test_urllib_redirect_handler_strips_authorization(self):
        request = MODULE.urllib.request.Request(
            "https://api.example/x", headers={"Authorization": "Basic k", "Accept": "x"}
        )
        handler = MODULE._SafeRedirectHandler()
        new_request = handler.redirect_request(
            request, None, 302, "Found", {}, "https://other.example/y"
        )
        self.assertIsNone(new_request.get_header("Authorization"))
        self.assertEqual("x", new_request.get_header("Accept"))

    def test_retry_predicate(self):
        http_404 = urllib.error.HTTPError("u", 404, "nf", {}, None)
        http_503 = urllib.error.HTTPError("u", 503, "busy", {}, None)
        self.assertFalse(MODULE._is_retryable_error(http_404))
        self.assertTrue(MODULE._is_retryable_error(http_503))
        self.assertTrue(MODULE._is_retryable_error(TimeoutError("t")))
        self.assertFalse(MODULE._is_retryable_error(ssl.SSLCertVerificationError("bad")))
        self.assertFalse(MODULE._is_retryable_error(ValueError("x")))


class ThreadSafeLoggingTests(unittest.TestCase):
    def test_worker_thread_messages_are_written_by_owner_thread(self):
        tool = _new_tool()
        tool._runtime_mml_api_key = ""
        written = []
        with _ArcpyPatch(
            AddMessage=lambda text: written.append((threading.get_ident(), text)),
            AddWarning=lambda text: written.append((threading.get_ident(), text)),
        ):
            tool._begin_message_owner()
            worker = threading.Thread(target=lambda: tool._warn("from worker"))
            worker.start()
            worker.join()
            self.assertEqual([], written)
            tool._msg("from owner")
            tool._end_message_owner()
        owner = threading.get_ident()
        self.assertEqual([(owner, "from worker"), (owner, "from owner")], written)


if __name__ == "__main__":
    unittest.main()
