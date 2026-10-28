"""SYKE workspace discovery and per-layer download routing, without a network."""

import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_toolbox_helpers import MODULE

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "qgis_plugin"))
from suomenvaylat_qgis import syke_sources as syke


GROUNDWATER = syke.BASE_URL + "inspire_ge/wfs"
PROTECTED = syke.BASE_URL + "inspire_ps/wfs"
DIRECTORY = ('<a href="' + GROUNDWATER + '?request=GetCapabilities">Pohjavesi</a>'
             '<a href="' + PROTECTED + '?request=GetCapabilities">Suojelu</a>').encode()


class DirectoryTests(unittest.TestCase):
    def test_directory_reads_all_workspaces_and_deduplicates_links(self):
        extra = syke.BASE_URL + "new_workspace/wfs"
        page = DIRECTORY + ('<a href="' + extra + '?service=WFS&amp;request=GetCapabilities">Uusi</a>'
                            '<p>' + GROUNDWATER + '?request=GetCapabilities</p>').encode()
        endpoints, errors = syke.discover_endpoints(lambda url: page)
        self.assertEqual(sorted([GROUNDWATER, PROTECTED, extra]), endpoints)
        self.assertEqual([], errors)

    def test_directory_ignores_other_hosts_protocols_and_service_types(self):
        page = ('<p>https://example.test/geoserver/test/wfs</p>'
                '<p>http://paikkatiedot.ymparisto.fi/geoserver/test/wfs</p>'
                '<p>' + syke.BASE_URL + 'test/wms?request=GetCapabilities</p>'
                '<p>' + syke.BASE_URL + 'test/wfs-other</p>').encode()
        self.assertEqual([], syke.endpoints_from_directory(page))

    def test_directory_failure_or_changed_markup_uses_snapshot_with_warning(self):
        def unavailable(url):
            raise OSError("directory unavailable")
        for fetch in (unavailable, lambda url: b"<html>Changed layout</html>"):
            endpoints, errors = syke.discover_endpoints(fetch)
            self.assertEqual(list(syke.WFS_ENDPOINTS), endpoints)
            self.assertIn(GROUNDWATER, endpoints)
            self.assertIn(PROTECTED, endpoints)
            self.assertEqual(1, len(errors))

    def test_directory_size_is_bounded(self):
        with patch.object(syke, "MAX_DIRECTORY_BYTES", 5):
            with self.assertRaisesRegex(ValueError, "kokorajan"):
                syke.endpoints_from_directory(DIRECTORY)


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = MODULE.WFSSourceRegistry()
        self.registry._fetch_syke_directory = lambda url: DIRECTORY
        self.calls = []

        def parse(url):
            endpoint = url.split("?", 1)[0]
            self.calls.append(endpoint)
            if endpoint == GROUNDWATER:
                return [{"id": "inspire_ge:GE.Groundwaterbody", "title": "Pohjavesialueet"},
                        {"id": "inspire_ge:GE.Pohjavesialuerajat", "title": "Pohjavesialuerajat"}]
            return [{"id": "inspire_ps:PS.ProtectedSites", "title": "Suojelualueet"}]

        self.registry._parse_capabilities = parse

    def test_all_layers_reach_ui_with_their_own_download_endpoint(self):
        tool = MODULE.VaylaWFSDownloader()
        tool.wfs_registry = self.registry
        labels = tool._get_layer_entries_for_sources(["Syke"])
        self.assertIn("Pohjavesialueet - Syke", labels)
        self.assertIn("Pohjavesialuerajat - Syke", labels)
        info = tool._layer_mapping["Pohjavesialueet - Syke"]
        self.assertEqual(GROUNDWATER, info["endpoint"])
        self.assertEqual("inspire_ge:GE.Groundwaterbody", info["id"])
        self.assertEqual(PROTECTED, tool._layer_mapping["Suojelualueet - Syke"]["endpoint"])
        self.registry.get_capabilities("Syke")
        self.assertEqual(2, len(self.calls), "a complete result should be cached")

    def test_one_failed_workspace_keeps_other_layers_and_is_retried(self):
        parse = self.registry._parse_capabilities

        def fail_groundwater(url):
            if url.startswith(GROUNDWATER):
                raise RuntimeError("Service WFS is disabled")
            return parse(url)

        self.registry._parse_capabilities = fail_groundwater
        entries = self.registry.get_capabilities("Syke")
        self.assertEqual(["inspire_ps:PS.ProtectedSites"], [entry["id"] for entry in entries])
        self.assertIn(GROUNDWATER, self.registry._capabilities_errors["Syke"][0])
        self.assertNotIn("Syke", self.registry._capabilities_cache)
        self.registry._parse_capabilities = parse
        self.assertEqual(3, len(self.registry.get_capabilities("Syke")))
        self.assertEqual([], self.registry._capabilities_errors["Syke"])

    def test_empty_capabilities_is_reported_as_an_incomplete_catalog(self):
        self.registry._parse_capabilities = lambda url: []
        self.assertEqual([], self.registry.get_capabilities("Syke"))
        self.assertEqual(2, len(self.registry._capabilities_errors["Syke"]))

    def test_partial_catalog_records_errors_without_download_warnings_or_disk_cache(self):
        tool = MODULE.VaylaWFSDownloader()
        tool.wfs_registry = self.registry
        warnings = []
        tool._warn = warnings.append
        writes = []
        tool._write_layer_disk_cache = lambda *args: writes.append(args)
        parse = self.registry._parse_capabilities
        self.registry._parse_capabilities = lambda url: [] if url.startswith(GROUNDWATER) else parse(url)
        layers = tool._fetch_layer_list(["Syke"], "test", allow_disk_cache=False)
        self.assertEqual(["Suojelualueet - Syke"], layers)
        self.assertEqual([], warnings)
        self.assertTrue(any(GROUNDWATER in error for error in self.registry._capabilities_errors["Syke"]))
        self.assertEqual([], writes)

    def test_old_twelve_layer_disk_cache_has_a_different_key(self):
        key = MODULE.VaylaWFSDownloader()._layer_catalog_cache_key(["Syke"])
        self.assertFalse(key.startswith("catalog-v3|"))

    def test_multiple_workspace_routing_is_also_preserved_for_liiteri(self):
        self.registry.sources["Liiteri"]["endpoints"] = [GROUNDWATER, PROTECTED]
        layers = self.registry.get_capabilities("Liiteri")
        self.assertEqual([GROUNDWATER, GROUNDWATER, PROTECTED], [entry["endpoint"] for entry in layers])

    def test_partial_result_from_another_source_does_not_prevent_success(self):
        tool = MODULE.VaylaWFSDownloader()
        tool.wfs_registry = SimpleNamespace(_capabilities_errors={"Syke": ["unavailable"]})
        writes = []
        tool._get_layer_entries_for_sources = lambda sources: ["Tiet - Väylä"]
        tool._write_layer_disk_cache = lambda *args: writes.append(args)
        tool._fetch_layer_list(["Väylä"], "test", allow_disk_cache=False)
        self.assertEqual(1, len(writes))


if __name__ == "__main__":
    unittest.main()
