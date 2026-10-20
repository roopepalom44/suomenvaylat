"""QGIS's actual wire protocol modules, tested without QGIS or a network."""
import gzip
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse
import urllib.request
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "qgis_plugin"))
from suomenvaylat_qgis import service_network as network, wfs_pages as wfs
from test_toolbox_helpers import MODULE


class NetworkTests(unittest.TestCase):
    def test_gzip_and_both_deflate_encodings(self):
        data = b'{"features":[]}' * 10
        compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
        raw_deflate = compressor.compress(data) + compressor.flush()
        for body, encoding in ((gzip.compress(data), "gzip"), (zlib.compress(data), "deflate"),
                               (raw_deflate, "deflate"), (data, "identity")):
            self.assertEqual(data, network.decode_body(body, encoding))

    def test_decompressed_size_is_bounded(self):
        with self.assertRaisesRegex(ValueError, "kokorajan"):
            network.decode_body(gzip.compress(b"a" * 2000), "gzip", 100)

    def test_truncated_compression_is_an_error(self):
        with self.assertRaises((EOFError, OSError)):
            network.decode_body(gzip.compress(b"hello")[:-4], "gzip")
        with self.assertRaisesRegex(ValueError, "Katkennut"):
            network.decode_body(zlib.compress(b"hello")[:-2], "deflate")

    def test_xml_rejects_doctype_and_entities_in_both_encodings(self):
        text = '<!DOCTYPE a [<!ENTITY x "bad">]><a>&x;</a>'
        for data in (text.encode(), text.encode("utf-16")):
            with self.assertRaisesRegex(ValueError, "DTD/ENTITY"):
                network.parse_xml(data)

    def test_xml_size_is_bounded(self):
        with patch.object(network, "MAX_XML_BYTES", 10):
            with self.assertRaises(ValueError):
                network.parse_xml(b"<a>1234567890</a>")

    def test_ogc_exception_text_is_extracted(self):
        xml = b'<ows:ExceptionReport xmlns:ows="urn:ows"><ows:Exception exceptionCode="InvalidParameter"><ows:ExceptionText>Invalid SRS EPSG:3067</ows:ExceptionText></ows:Exception></ows:ExceptionReport>'
        self.assertEqual("Invalid SRS EPSG:3067", network.ogc_exception(xml))
        with self.assertRaisesRegex(RuntimeError, "Invalid SRS"):
            network.checked_xml(xml)

    def test_http_429_retries_then_succeeds(self):
        calls = []
        response = io.BytesIO(b'{}')
        response.headers = {}
        def opener(request, timeout):
            calls.append(request)
            if len(calls) == 1:
                raise urllib.error.HTTPError(request.full_url, 429, "busy", {"Retry-After": "0"}, io.BytesIO())
            return response
        self.assertEqual(b'{}', network.request_bytes(urllib.request.Request("https://example.test"), opener))
        self.assertEqual(2, len(calls))

    def test_http_404_is_not_retried(self):
        calls = []
        def opener(request, timeout):
            calls.append(request)
            raise urllib.error.HTTPError(request.full_url, 404, "missing", {}, io.BytesIO())
        with self.assertRaises(urllib.error.HTTPError):
            network.request_bytes(urllib.request.Request("https://example.test"), opener)
        self.assertEqual(1, len(calls))

    def test_transient_network_failure_retries(self):
        calls = []
        response = io.BytesIO(b'{}')
        response.headers = {}
        def opener(request, timeout):
            calls.append(request)
            if len(calls) < 3:
                raise ConnectionResetError()
            return response
        with patch.object(network, "retry_delay", return_value=0):
            self.assertEqual(b'{}', network.request_bytes(urllib.request.Request("https://example.test"), opener))
        self.assertEqual(3, len(calls))

    def test_cancellation_is_checked_before_request(self):
        class Canceled(Exception):
            pass
        def checkpoint():
            raise Canceled()
        with self.assertRaises(Canceled):
            network.request_bytes(urllib.request.Request("https://example.test"), None, checkpoint=checkpoint)


class WFSPagingTests(unittest.TestCase):
    entry = {"endpoint": "https://example.test/wfs?token=secret", "id": "demo:data"}
    meta = ("2.0.0", "application/json", "geometry", "id")

    def run_pages(self, pages, hits=3, wkt="POLYGON((0 0,1 0,1 1,0 0))"):
        queue = list(pages)
        calls = []
        def fetch(url, body):
            params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
            if body:
                params.update(urllib.parse.parse_qsl(body.decode()))
            calls.append((url, body, params))
            if params.get("resultType") == "hits":
                return f'<FeatureCollection numberMatched="{hits}"/>'.encode()
            value = queue.pop(0)
            if isinstance(value, Exception):
                raise value
            return json.dumps(value).encode()
        with patch.object(wfs, "metadata", return_value=self.meta):
            output = list(wfs.pages(self.entry, "0,0,1,1", wkt, fetch, None))
        return output, calls

    def test_pages_preserve_cql_and_count_before_local_clipping(self):
        pages = [{"features": [{"id": "a"}, {"id": "b"}], "numberMatched": 3},
                 {"features": [{"id": "c"}], "numberMatched": 3}]
        output, calls = self.run_pages(pages)
        self.assertEqual([2, 3], [page[2] for page in output])
        self.assertEqual(["0", "2"], [call[2]["startIndex"] for call in calls[1:]])
        self.assertTrue(all("INTERSECTS(geometry," in call[2]["CQL_FILTER"] for call in calls))
        self.assertEqual("application/json", calls[1][2]["outputFormat"])

    def test_short_page_does_not_hide_server_side_page_cap(self):
        output, _ = self.run_pages([{"features": [{"id": "a"}]}, {"features": [{"id": "b"}]},
                                   {"features": [{"id": "c"}]}])
        self.assertEqual(3, len(output))

    def test_premature_empty_page_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "1/3"):
            self.run_pages([{"features": [{"id": "a"}]}, {"features": []}])

    def test_repeated_page_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "saman sivun"):
            self.run_pages([{"features": [{"id": "a"}]}, {"features": [{"id": "a"}]}])

    def test_changing_total_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "muuttui"):
            self.run_pages([{"features": [{"id": "a"}], "numberMatched": 4}])

    def test_network_failure_after_first_page_propagates(self):
        with self.assertRaises(ConnectionResetError):
            self.run_pages([{"features": [{"id": "a"}]}, ConnectionResetError()])

    def test_no_cql_uses_same_bbox_for_hits_and_pages(self):
        _, calls = self.run_pages([{"features": [{"id": "a"}]}], hits=1, wkt=None)
        self.assertEqual("0,0,1,1,EPSG:3067", calls[0][2]["bbox"])
        self.assertEqual(calls[0][2]["bbox"], calls[1][2]["bbox"])

    def test_long_cql_uses_post_with_exact_polygon(self):
        polygon = "POLYGON((" + "1 2," * 3000 + "1 2))"
        _, calls = self.run_pages([{"features": [{"id": "a"}]}], hits=1, wkt=polygon)
        self.assertTrue(all(call[1] is not None for call in calls))
        self.assertTrue(all(polygon in call[2]["CQL_FILTER"] for call in calls))
        self.assertTrue(all("token=secret" in call[0] for call in calls))

    def test_capabilities_and_schema_negotiate_json_geometry_and_sort(self):
        caps = b'<WFS_Capabilities version="2.0.0"><Parameter name="outputFormat"><Value>text/xml</Value><Value>application/json</Value></Parameter></WFS_Capabilities>'
        schema = b'<schema><element name="geom" type="gml:MultiSurfacePropertyType"/><element name="gid" type="int"/></schema>'
        queue = [caps, schema]
        self.assertEqual(("2.0.0", "application/json", "geom", "gid"), wfs.metadata(self.entry, lambda *args: queue.pop(0)))

    def test_malformed_json_is_an_error(self):
        def fetch(url, body):
            if "resultType=hits" in url:
                return b'<FeatureCollection numberMatched="1"/>'
            return b'{"features":['
        with patch.object(wfs, "metadata", return_value=self.meta):
            with self.assertRaises(json.JSONDecodeError):
                list(wfs.pages(self.entry, "0,0,1,1", None, fetch, None))

    def test_syke_prefers_working_json_over_advertised_but_rejected_geojson(self):
        caps = b'''<WFS_Capabilities version="2.0.0"><OperationsMetadata>
        <Operation name="GetFeature"><Parameter name="outputFormat"><AllowedValues>
        <Value>application/geo+json</Value><Value>application/json</Value><Value>json</Value>
        </AllowedValues></Parameter></Operation></OperationsMetadata></WFS_Capabilities>'''
        queue = [caps, b"<schema/>"]
        self.assertEqual("application/json", wfs.metadata(self.entry, lambda *args: queue.pop(0))[1])

    def test_wfs_formats_are_read_from_getfeature_not_other_operations(self):
        caps = b'''<WFS_Capabilities version="2.0.0"><OperationsMetadata>
        <Operation name="DescribeFeatureType"><Parameter name="outputFormat">
        <Value>application/json</Value></Parameter></Operation>
        <Operation name="GetFeature"><Parameter name="outputFormat">
        <Value>text/xml</Value></Parameter></Operation></OperationsMetadata></WFS_Capabilities>'''
        queue = [caps, b"<schema/>"]
        self.assertIsNone(wfs.metadata(self.entry, lambda *args: queue.pop(0))[1])


class ArcGISIntegrationTests(unittest.TestCase):
    def test_shapefile_conversion_uses_one_cursor_and_unique_names(self):
        from types import SimpleNamespace
        fields = [SimpleNamespace(name="rakennustunnusGuid", type="GUID", required=False),
                  SimpleNamespace(name="rakennustunnusOther", type="GUID", required=False),
                  SimpleNamespace(name="rakennustu", type="String", required=False)]
        cursors, renamed, rows = [], [], []
        class Cursor:
            def __init__(self, path, names):
                cursors.append(names)
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def __iter__(self):
                return iter([["first-guid", "second-guid", None, None]])
            def updateRow(self, row):
                rows.append(list(row))
        management = SimpleNamespace(CopyFeatures=lambda *args: None, AddField=lambda *args, **kwargs: None,
                                     DeleteField=lambda *args: None, AlterField=lambda path, old, new, alias: renamed.append(new))
        with patch.object(MODULE.arcpy, "env", SimpleNamespace(scratchGDB="scratch.gdb"), create=True), \
             patch.object(MODULE.arcpy, "ListFields", lambda path: fields, create=True), \
             patch.object(MODULE.arcpy, "management", management, create=True), \
             patch.object(MODULE.arcpy, "da", SimpleNamespace(UpdateCursor=Cursor), create=True):
            MODULE.ShapefileFieldConverter.convert_feature_class("input", workspace_is_folder=True)
        self.assertEqual(1, len(cursors))
        self.assertEqual(["first-guid", "second-guid"], rows[0][2:])
        self.assertEqual(2, len(set(renamed)))
        self.assertNotIn("rakennustu", renamed)
        self.assertTrue(all(len(name) <= 10 for name in renamed))

    def test_deduplication_uses_gml_identity_and_retains_generic_id_fallback(self):
        from types import SimpleNamespace
        tool = MODULE.VaylaWFSDownloader.__new__(MODULE.VaylaWFSDownloader)
        calls = []
        class Cursor:
            def __enter__(self):
                return iter([["feature.1"], ["feature.2"]])
            def __exit__(self, *args):
                pass
        fields = [SimpleNamespace(name="gml_id", type="String"), SimpleNamespace(name="value", type="String")]
        with patch.object(MODULE.arcpy, "ListFields", lambda path: fields, create=True), \
             patch.object(MODULE.arcpy, "da", SimpleNamespace(SearchCursor=lambda *args: Cursor()), create=True), \
             patch.object(MODULE.arcpy, "management", SimpleNamespace(DeleteIdentical=lambda path, keys: calls.append(keys)), create=True):
            tool._delete_identical_downloads("data")
            fields[0].name = "id"
            tool._delete_identical_downloads("data")
        self.assertEqual(["gml_id", "Shape"], calls[0])
        self.assertEqual(["Shape", "id", "value"], calls[1])

    def test_transport_advertises_compression_and_decodes_it(self):
        calls = []
        class Response:
            headers = {"Content-Encoding": "gzip"}
            status, will_close = 200, False
            def read(self, limit):
                return gzip.compress(b'{"features":[]}')
        class Connection:
            def request(self, *args, **kwargs):
                calls.append(kwargs)
            def getresponse(self):
                return Response()
        transport = MODULE.HttpTransport()
        transport._connection = lambda *args: ("key", Connection())
        value = transport.request("https://example.test/wfs")
        self.assertEqual(b'{"features":[]}', value[2])
        self.assertEqual("gzip, deflate", calls[0]["headers"]["Accept-Encoding"])

    def test_ogc_error_redacts_secrets_after_parsing(self):
        tool = MODULE.VaylaWFSDownloader.__new__(MODULE.VaylaWFSDownloader)
        tool._redact_secrets = lambda value: value.replace("secret", "[PIILOTETTU]")
        self.assertEqual("bad [PIILOTETTU]", tool._wfs_error_snippet('<ExceptionReport><ExceptionText>bad secret</ExceptionText></ExceptionReport>'))

    def test_credentials_fallback_is_in_user_directory(self):
        tool = MODULE.VaylaWFSDownloader.__new__(MODULE.VaylaWFSDownloader)
        with patch.dict(MODULE.os.environ, {"APPDATA": "", "LOCALAPPDATA": ""}):
            path = Path(tool._credentials_file_path())
        self.assertEqual(Path.home() / ".suomenvaylat" / "service_credentials.json", path)

    def test_bbox_projection_uses_geometry_without_gp_disk_writes(self):
        tool = MODULE.VaylaWFSDownloader.__new__(MODULE.VaylaWFSDownloader)
        from types import SimpleNamespace
        geometry = SimpleNamespace(projectAs=lambda sr: SimpleNamespace(extent=SimpleNamespace(YMin=60, XMin=24, YMax=61, XMax=25)))
        tool._merged_boundary_geometry = lambda fc: (geometry, None)
        with patch.object(MODULE.arcpy, "SpatialReference", lambda epsg: epsg, create=True):
            self.assertEqual("60,24,61,25", tool._extent_bbox_4326("boundary"))


if __name__ == "__main__":
    unittest.main()
